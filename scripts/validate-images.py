"""Build and inspect runtime images, then smoke them without a database or network."""

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from integration_support import ROOT, IntegrationProject, run_cli

logger = logging.getLogger("infrapulse.integration")
LABEL = "com.infrapulse.image-validation"
SECRET = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{32,}\r?\n"
    rb"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    rb"|\bAKIA[A-Z0-9]{16}\b"
)


def scan_archive(archive: tarfile.TarFile) -> dict[str, int]:
    """Inspect tar members without extracting untrusted paths onto the host."""
    files = 0
    for member in archive:
        path = Path(member.name)
        if any(
            part == ".env"
            or part.startswith(".env.")
            or part in {".aws", ".ssh", ".git", ".venv", "node_modules"}
            for part in path.parts
        ):
            raise ValueError("Environment, credential or Git file found in image")
        if path.parts[:2] == ("app", "tests"):
            raise ValueError("Development tests in runtime image")
        # Linux symlink modes are conventionally 0777; only target inode modes apply.
        if (
            (member.isfile() or member.isdir())
            and path.parts[:2] in {("app", "app"), ("etc", "nginx")}
            and member.mode & 0o002
        ):
            raise ValueError(f"World-writable application/configuration inode: {path}")
        if not member.isfile():
            continue
        files += 1
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("Unreadable image file")
        # Stream even large files; overlap detects a credential spanning chunks.
        tail = b""
        while data := source.read(1024 * 1024):
            if SECRET.search(tail + data):
                raise ValueError("Credential pattern found in image file")
            tail = data[-256:]
    return {"files": files}


def images(project: IntegrationProject) -> dict[str, str]:
    return {
        "BACKEND": f"{project.name}:backend",
        "FRONTEND_BUILD": f"{project.name}:frontend-build",
        "FRONTEND": f"{project.name}:frontend",
    }


def image_marker(project: IntegrationProject) -> Path:
    return project.marker.with_suffix(".images-owned")


def reserve_images(project: IntegrationProject) -> None:
    for tag in images(project).values():
        if project.run(
            ["docker", "image", "ls", "-q", "--filter", f"reference={tag}"],
            capture=True,
        ).strip():
            raise RuntimeError("Refusing pre-existing image tag")
    with image_marker(project).open("x") as marker:
        marker.write(project.name)


def cleanup_images(project: IntegrationProject) -> None:
    marker = image_marker(project)
    if not marker.exists():
        return
    if marker.read_text() != project.name:
        raise RuntimeError("Invalid image ownership marker")
    errors: list[Exception] = []
    for tag in images(project).values():
        try:
            if not project.run(
                ["docker", "image", "ls", "-q", "--filter", f"reference={tag}"],
                capture=True,
            ).strip():
                continue
            label = project.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    f'{{{{index .Config.Labels "{LABEL}"}}}}',
                    tag,
                ],
                capture=True,
            ).strip()
            if label != project.name:
                raise RuntimeError("Refusing image without matching ownership label")
            project.run(["docker", "image", "rm", tag])
        except Exception as error:
            logger.exception("Owned image cleanup failed")
            errors.append(error)
    if errors:
        raise errors[0]
    marker.unlink()
    print("Owned image tags removed; shared build cache retained", flush=True)


def build(project: IntegrationProject) -> None:
    project.run(["docker", "buildx", "version"])
    for dockerfile, context in (
        ("backend/Dockerfile", "backend"),
        ("frontend/Dockerfile", "."),
    ):
        project.run(["docker", "buildx", "build", "--check", "-f", dockerfile, context])
    for kind, target, dockerfile, context in (
        ("BACKEND", "runtime", "backend/Dockerfile", "backend"),
        ("FRONTEND_BUILD", "build", "frontend/Dockerfile", "."),
        ("FRONTEND", "runtime", "frontend/Dockerfile", "."),
    ):
        project.run(
            [
                "docker",
                "buildx",
                "build",
                "--load",
                "--platform",
                "linux/amd64",
                "--progress",
                "plain",
                "--target",
                target,
                "--label",
                f"{LABEL}={project.name}",
                "-t",
                images(project)[kind],
                "-f",
                dockerfile,
                context,
            ]
        )


def inspect_image(
    project: IntegrationProject, tag: str, container: str, directory: Path
) -> dict[str, object]:
    metadata = json.loads(
        project.run(["docker", "image", "inspect", tag], capture=True)
    )[0]
    config = metadata["Config"]
    if (metadata["Os"], metadata["Architecture"]) != ("linux", "amd64"):
        raise ValueError("Expected a Linux amd64 runtime image")
    if config["User"].split(":")[0] in {"", "0", "root"}:
        raise ValueError("Runtime image must declare a non-root user")
    keys = [entry.split("=", 1)[0] for entry in config.get("Env", [])]
    if any(
        re.search(
            r"PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY|DATABASE_URL",
            key,
            re.IGNORECASE,
        )
        for key in keys
    ):
        raise ValueError("Sensitive environment key baked into runtime image")
    if tag.endswith(":backend"):
        assert config["Cmd"] == [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
        assert config.get("Entrypoint") is None
        assert "8000/tcp" in config.get("ExposedPorts", {})
    else:
        assert config["Cmd"] == ["nginx", "-g", "daemon off;"]
        assert config["Entrypoint"] == ["/docker-entrypoint.sh"]
        assert "8080/tcp" in config.get("ExposedPorts", {})
        assert "/healthz" in " ".join(config["Healthcheck"]["Test"])
    rootfs = directory / "rootfs.tar"
    project.run(["docker", "export", "--output", str(rootfs), container])
    with tarfile.open(rootfs) as archive:
        final_files = scan_archive(archive)
        members = archive.getmembers()
        permissions = {
            item.name: {"mode": oct(item.mode), "uid": item.uid, "gid": item.gid}
            for item in members
            if Path(item.name).as_posix()
            in {
                "app",
                "app/app",
                "app/app/main.py",
                "etc/nginx",
                "etc/nginx/nginx.conf",
                "usr/share/nginx/html",
                "usr/share/nginx/html/index.html",
            }
        }
        build_residue = sum(
            item.isfile()
            and (
                "build" in Path(item.name).parts
                or any(part.endswith(".egg-info") for part in Path(item.name).parts)
            )
            for item in members
            if Path(item.name).parts[:1] == ("app",)
        )
    saved = directory / "image.tar"
    project.run(["docker", "image", "save", "--output", str(saved), tag])
    with tarfile.open(saved) as outer:
        stream = outer.extractfile("manifest.json")
        if stream is None:
            raise ValueError("Missing image manifest")
        layers = json.load(stream)[0]["Layers"]
        for name in layers:
            layer = outer.extractfile(name)
            if layer is None:
                raise ValueError("Missing image layer")
            with tarfile.open(fileobj=layer, mode="r|*") as archive:
                scan_archive(archive)
    history = project.run(
        ["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", tag],
        capture=True,
    )
    if SECRET.search(history.encode()):
        raise ValueError("Credential pattern in image history")
    return {
        "id": metadata["Id"],
        "os": metadata["Os"],
        "architecture": metadata["Architecture"],
        "user": config["User"],
        "entrypoint": config.get("Entrypoint"),
        "cmd": config["Cmd"],
        "ports": config.get("ExposedPorts", {}),
        "healthcheck": config.get("Healthcheck"),
        "environment_keys": keys,
        "layers": len(layers),
        "rootfs": final_files,
        "bytes": metadata["Size"],
        "permissions": permissions,
        "application_build_residue_files": build_residue,
    }


API_PROBE = """
import importlib.util, json, os, time, urllib.error, urllib.request
assert os.geteuid() != 0
assert not os.access('/app/app', os.W_OK)
for name in ('pytest', 'mypy', 'ruff', 'httpx'):
    assert importlib.util.find_spec(name) is None, name
for attempt in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
            assert response.status == 200 and json.load(response)['status'] == 'healthy'
        break
    except urllib.error.URLError:
        time.sleep(0.25)
else:
    raise RuntimeError('API health did not respond')
try:
    urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)
except urllib.error.HTTPError as error:
    assert error.code == 503 and json.load(error)['status'] == 'not_ready'
else:
    raise AssertionError('Readiness must fail without a database')
print('Non-root API healthy; readiness correctly unavailable; no development packages')
"""


def smoke(project: IntegrationProject) -> None:
    project.run(
        [*project.compose, "up", "-d", "--no-build", "api", "worker", "frontend"]
    )
    project.run(
        [*project.compose, "exec", "-T", "api", "python", "-"], source=API_PROBE
    )
    project.run([*project.compose, "exec", "-T", "api", "python", "-m", "pip", "check"])
    project.run([*project.compose, "exec", "-T", "frontend", "nginx", "-t"])
    project.run(
        [
            *project.compose,
            "exec",
            "-T",
            "frontend",
            "sh",
            "-ec",
            "test ! -e /usr/local/bin/node; test ! -e /usr/local/bin/python; test ! -d /workspace",
        ]
    )
    html = project.run(
        [
            *project.compose,
            "exec",
            "-T",
            "frontend",
            "wget",
            "-qO-",
            "http://127.0.0.1:8080/",
        ],
        capture=True,
    )
    if '<div id="root">' not in html or not re.search(r'/assets/[^" ]+\.js', html):
        raise ValueError("Missing built frontend HTML/JavaScript")
    for asset in re.findall(r'(?:src|href)="(/assets/[^" ]+)"', html):
        project.run(
            [
                *project.compose,
                "exec",
                "-T",
                "frontend",
                "wget",
                "-qO",
                "/dev/null",
                f"http://127.0.0.1:8080{asset}",
            ]
        )
    state: dict[str, object] = {}
    for attempt in range(60):
        identifiers = project.run([*project.compose, "ps", "-q"], capture=True).split()
        state = {
            item["Config"]["Labels"]["com.docker.compose.service"]: item
            for item in json.loads(
                project.run(["docker", "inspect", *identifiers], capture=True)
            )
        }
        if (
            state.get("frontend", {}).get("State", {}).get("Health", {}).get("Status")
            == "healthy"
        ):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Nginx image healthcheck did not become healthy")
    for item in state.values():
        assert item["HostConfig"]["NetworkMode"] == "none"
        assert (
            item["HostConfig"]["ReadonlyRootfs"]
            and not item["HostConfig"]["Privileged"]
        )
        assert not item["HostConfig"]["PortBindings"]
        assert not any(mount["Type"] == "bind" for mount in item["Mounts"])
    worker_logs = project.run(
        [*project.compose, "logs", "--no-color", "worker"], capture=True
    )
    assert '"message": "worker_started"' in worker_logs
    try:
        project.run(
            [
                *project.compose,
                "exec",
                "-T",
                "worker",
                "python",
                "-m",
                "app.worker",
                "healthcheck",
            ]
        )
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
    else:
        raise AssertionError("Worker must not claim a healthy heartbeat without DB")
    project.run(
        [*project.compose, "stop", "--timeout", "20", "api", "worker", "frontend"]
    )
    worker_logs = project.run(
        [*project.compose, "logs", "--no-color", "worker"], capture=True
    )
    assert '"message": "worker_stopped"' in worker_logs
    assert '"message": "scheduler_stopped"' in worker_logs
    for service in ("api", "worker", "frontend"):
        identifier = project.run(
            [*project.compose, "ps", "-aq", service], capture=True
        ).strip()
        assert (
            project.run(
                ["docker", "inspect", "--format", "{{.State.ExitCode}}", identifier],
                capture=True,
            ).strip()
            == "0"
        )
    print(
        "PASS: API health, negative readiness/heartbeat, worker SIGTERM, Nginx config/static/health and isolated runtimes",
        flush=True,
    )


REFERENCE_GENERATOR = """
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { constants, tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
const digest = value => createHash('sha256').update(value).digest('hex');
const input = JSON.parse(readFileSync(0, 'utf8'));
const source = process.cwd();
for (const [name, expected] of Object.entries(input.expected)) {
  if (digest(readFileSync(join(source, name))) !== expected) {
    throw new Error('Reference generator source or lockfile mismatch');
  }
}
const installed = JSON.parse(readFileSync(join(source, 'node_modules/openapi-typescript/package.json'), 'utf8'));
if (installed.version !== input.version) throw new Error('Reference generator version mismatch');
const temporary = mkdtempSync(join(tmpdir(), 'openapi-reference-'));
const frontend = join(temporary, 'frontend');
mkdirSync(join(frontend, 'scripts'), { recursive: true, mode: 0o700 });
copyFileSync(join(source, 'scripts/generate-api.mjs'), join(frontend, 'scripts/generate-api.mjs'));
symlinkSync(join(source, 'node_modules'), join(frontend, 'node_modules'));
const schema = join(temporary, 'openapi.json');
writeFileSync(schema, input.schema, { mode: 0o600 });
// Fresh location: never read or copy the types already produced by the build.
const result = spawnSync(process.execPath, [join(frontend, 'scripts/generate-api.mjs')], {
  cwd: frontend, env: { OPENAPI_SCHEMA_FILE: schema }, timeout: 60000
});
if (result.status !== 0) {
  const code = result.status ?? (result.signal ? 128 + constants.signals[result.signal] : 1);
  console.error('Reference generator failed; status=' + code + '; stderr_sha256=' + digest(result.stderr ?? ''));
  process.exit(code);
}
process.stdout.write(readFileSync(join(frontend, 'src/shared/api/generated.d.ts')));
"""


def generate_reference(
    project: IntegrationProject, schema: Path
) -> tuple[bytes, dict[str, str]]:
    frontend = ROOT / "frontend"
    lock_bytes = (frontend / "package-lock.json").read_bytes()
    generator_bytes = (frontend / "scripts/generate-api.mjs").read_bytes()
    locked = json.loads(lock_bytes)["packages"]["node_modules/openapi-typescript"]
    declared = json.loads((frontend / "package.json").read_text())["devDependencies"][
        "openapi-typescript"
    ]
    if declared != locked["version"] or not locked.get("integrity"):
        raise ValueError(
            "Reference generator must use the exact declared and locked version"
        )
    metadata = {
        "generator_version": locked["version"],
        "generator_source_sha256": hashlib.sha256(generator_bytes).hexdigest(),
        "lockfile_sha256": hashlib.sha256(lock_bytes).hexdigest(),
    }
    payload = {
        "schema": schema.read_text(),
        "version": locked["version"],
        "expected": {
            "scripts/generate-api.mjs": metadata["generator_source_sha256"],
            "package-lock.json": metadata["lockfile_sha256"],
        },
    }
    command = [
        "docker",
        "run",
        "--pull",
        "never",
        "--rm",
        "-i",
        "--name",
        f"{project.name}-types-reference",
        "--label",
        f"com.docker.compose.project={project.name}",
        "--label",
        "com.docker.compose.service=types-reference",
        "--label",
        "com.docker.compose.oneoff=True",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "node",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--workdir",
        "/workspace/frontend",
        "--entrypoint",
        "node",
        images(project)["FRONTEND_BUILD"],
        "--input-type=module",
        "-e",
        REFERENCE_GENERATOR,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=project.env,
            input=json.dumps(payload).encode(),
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # Keep status/exception identity while bounding all logged diagnostics.
        error.cmd = ["docker", "run", "types-reference"]
        print(
            f"Reference generation failed: exit_code={getattr(error, 'returncode', 'timeout')}; "
            f"stdout_sha256={hashlib.sha256(error.stdout or b'').hexdigest()}; "
            f"stderr_sha256={hashlib.sha256(error.stderr or b'').hexdigest()}",
            file=sys.stderr,
        )
        raise
    return result.stdout, metadata


def compare_types(
    project: IntegrationProject, schema: Path, built: Path
) -> dict[str, str]:
    try:
        content = built.read_bytes()
    except FileNotFoundError:
        raise ValueError("Built OpenAPI TypeScript types are missing") from None
    if not content.strip():
        raise ValueError("Built OpenAPI TypeScript types are empty")
    reference, metadata = generate_reference(project, schema)
    if not reference.strip():
        raise ValueError("Generated reference OpenAPI TypeScript types are empty")
    hashes = {
        "built_sha256": hashlib.sha256(content).hexdigest(),
        "reference_sha256": hashlib.sha256(reference).hexdigest(),
    }
    diagnostic = "; ".join(f"{key}={value}" for key, value in hashes.items())
    if content != reference:
        raise ValueError(f"OpenAPI TypeScript content mismatch: {diagnostic}")
    print(f"PASS: independent OpenAPI TypeScript comparison; {diagnostic}", flush=True)
    return hashes | metadata


def validate(project: IntegrationProject, schema: Path, results: Path) -> None:
    reserve_images(project)
    build(project)
    project.run([*project.compose, "create", "--no-build"])
    with tempfile.TemporaryDirectory(prefix="infrapulse-image-inspect-") as temporary:
        directory = Path(temporary)
        builder = project.run(
            [*project.compose, "ps", "-aq", "build-inspection"], capture=True
        ).strip()
        project.run(
            [
                "docker",
                "cp",
                f"{builder}:/tmp/openapi.json",
                str(directory / "openapi.json"),
            ]
        )
        if json.loads((directory / "openapi.json").read_text()) != json.loads(
            schema.read_text()
        ):
            raise ValueError("Built frontend contract differs from this run's OpenAPI")
        project.run(
            [
                "docker",
                "cp",
                f"{builder}:/workspace/frontend/src/shared/api/generated.d.ts",
                str(directory / "generated.d.ts"),
            ]
        )
        types = compare_types(project, schema, directory / "generated.d.ts")
        report: dict[str, object] = {
            "contract_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
            "types": types,
        }
        for service, kind in (("api", "BACKEND"), ("frontend", "FRONTEND")):
            identifier = project.run(
                [*project.compose, "ps", "-aq", service], capture=True
            ).strip()
            report[service] = inspect_image(
                project, images(project)[kind], identifier, directory
            )
        smoke(project)
        report["smoke"] = "PASS"
        results.write_text(json.dumps(report, indent=2) + "\n")
        print(
            "PASS: both runtimes and all layers inspected; frontend schema matches this run",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if not args.cleanup and (args.schema is None or args.report is None):
        parser.error("--schema and --report are required for validation")
    project = IntegrationProject("images", ["compose.images.test.yaml"])
    # The inspection-only container must also belong to Compose down's service set.
    project.compose.extend(["--profile", "inspection"])
    project.env.update({f"IMAGE_{key}": tag for key, tag in images(project).items()})
    failure: BaseException | None = None
    try:
        if args.cleanup:
            project.cleanup()
        else:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with project:
                project.run([*project.compose, "config", "--quiet"])
                validate(project, args.schema, args.report)
    except BaseException as error:
        logger.exception("Image validation or container cleanup failed")
        failure = error
    try:
        cleanup_images(project)
    except Exception:
        logger.exception("Image cleanup failed")
        if failure is None:
            raise
    if failure is not None:
        raise failure


if __name__ == "__main__":
    run_cli(main)
