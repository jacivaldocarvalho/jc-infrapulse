"""Local informational discovery. Findings are data; operational failures are not PASS."""

import argparse
import hashlib
import json
import os
import re
import resource
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from integration_support import ROOT, IntegrationProject

PINS = json.loads((ROOT / "security/vulnerability-scanners.json").read_text())
SEVERITIES = {"UNKNOWN", "INFO", "LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL"}
ID = r"(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|PYSEC-\d{4}-\d+|NPM-\d+|BIT-[A-Za-z0-9_.-]+-\d{4}-\d+|TEMP-\d{7}-[A-F0-9]{6})"
VERSION = r"[0-9][A-Za-z0-9.+_:~*!-]{0,127}"
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}"
)


def safe(value: object, pattern: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(pattern, value)
        or SECRET.search(value)
    ):
        raise ValueError("Invalid or sensitive report field")
    return value


def finding(
    package: str,
    version: str,
    ids: list[str],
    severity: str,
    fixes: list[str],
    origin: str,
    fix_available: bool | None = None,
) -> dict:
    if not ids or severity.upper() not in SEVERITIES:
        raise ValueError("Missing ID or unsupported severity")
    if origin not in {"python", "npm", "os-pkgs", "lang-pkgs"}:
        raise ValueError("Unsupported package origin")
    if fix_available is not None and not isinstance(fix_available, bool):
        raise TypeError("Invalid fix availability")
    return {
        "package": safe(package, r"[@A-Za-z0-9][A-Za-z0-9@/_.+-]{0,159}"),
        "installed_version": safe(version, VERSION),
        "ids": sorted({safe(item, ID) for item in ids}),
        "severity": severity.upper(),
        "fix_versions": sorted({safe(item, VERSION) for item in fixes}),
        "fix_available": bool(fixes) if fix_available is None else fix_available,
        "origin": origin,
    }


def inventory() -> dict[str, str]:
    records: dict[str, str] = {}
    for name in ("requirements.lock", "requirements-build.lock"):
        text = (ROOT / "backend" / name).read_text().replace("\\\n", " ")
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(
                r"([A-Za-z0-9_.-]+)==(\S+)(?:\s+--hash=sha256:[a-f0-9]{64})+", line
            )
            if not match:
                raise ValueError("Invalid locked Python input")
            package, version = match.groups()
            package = re.sub(r"[-_.]+", "-", package).lower()
            if package in records and records[package] != version:
                raise ValueError("Conflicting Python pins")
            records[package] = version
    return records


def parse_pip(document: dict, expected: dict[str, str]) -> tuple[list[dict], dict]:
    dependencies = document["dependencies"]
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Empty Python audit")
    seen = {}
    rows = []
    for dependency in dependencies:
        if "skip_reason" in dependency:
            raise ValueError("Python dependency was not audited")
        name = re.sub(r"[-_.]+", "-", dependency["name"]).lower()
        version = dependency["version"]
        seen[name] = version
        if not isinstance(dependency["vulns"], list):
            raise TypeError("Invalid Python vulnerability list")
        for vulnerability in dependency["vulns"]:
            rows.append(
                finding(
                    name,
                    version,
                    [vulnerability["id"], *vulnerability.get("aliases", [])],
                    "UNKNOWN",
                    vulnerability["fix_versions"],
                    "python",
                )
            )
    if seen != expected:
        raise ValueError("Python audit coverage differs from locked inventory")
    rows = list({json.dumps(row, sort_keys=True): row for row in rows}.values())
    return rows, {
        "dependencies": len(seen),
        "severity_note": "pip-audit JSON does not supply severity",
    }


def parse_npm(document: dict, lock: dict) -> tuple[list[dict], dict]:
    if document.get("error") or document.get("auditReportVersion") != 2:
        raise ValueError("Unsupported or failed npm audit")
    vulnerabilities = document["vulnerabilities"]
    counts = document["metadata"]["vulnerabilities"]
    dependencies = document["metadata"]["dependencies"]["total"]
    if (
        not isinstance(vulnerabilities, dict)
        or not isinstance(dependencies, int)
        or dependencies < 1
    ):
        raise ValueError("npm audit has no inventory")
    if counts["total"] != len(vulnerabilities):
        raise ValueError("npm findings/summary mismatch")

    def advisories(name: str, visited: set[str]) -> list[dict]:
        if name in visited:
            return []
        result = []
        for via in vulnerabilities[name]["via"]:
            if isinstance(via, str):
                result.extend(advisories(via, visited | {name}))
            elif isinstance(via, dict):
                result.append(via)
            else:
                raise TypeError("Invalid npm advisory")
        return result

    rows = []
    for name, vulnerability in vulnerabilities.items():
        versions = {
            lock["packages"][node]["version"] for node in vulnerability["nodes"]
        }
        via = advisories(name, set())
        if not versions or not via:
            raise ValueError("npm vulnerability has no version or advisory")
        fix = vulnerability["fixAvailable"]
        fixes = [fix["version"]] if isinstance(fix, dict) else []
        if not isinstance(fix, (dict, bool)):
            raise TypeError("Invalid npm fix availability")
        for advisory in via:
            source = advisory["source"]
            if not isinstance(source, int) or source < 1:
                raise ValueError("Invalid npm advisory ID")
            ids = [f"NPM-{source}"]
            match = re.fullmatch(
                r"https://github\.com/advisories/(GHSA-[a-z0-9-]+)",
                advisory.get("url", ""),
            )
            if match:
                ids.append(match[1])
            for version in versions:
                row = finding(
                    name,
                    version,
                    ids,
                    vulnerability["severity"],
                    fixes,
                    "npm",
                    bool(fix),
                )
                row["fix_package"] = (
                    safe(fix["name"], r"[@A-Za-z0-9][A-Za-z0-9@/_.+-]{0,159}")
                    if isinstance(fix, dict)
                    else None
                )
                row["requires_major_upgrade"] = (
                    fix.get("isSemVerMajor") if isinstance(fix, dict) else None
                )
                if row["requires_major_upgrade"] is not None and not isinstance(
                    row["requires_major_upgrade"], bool
                ):
                    raise TypeError("Invalid npm upgrade metadata")
                rows.append(row)
    rows = list({json.dumps(row, sort_keys=True): row for row in rows}.values())
    return rows, {"dependencies": dependencies, "vulnerable_packages": counts["total"]}


def parse_trivy(document: dict, backend: bool) -> tuple[list[dict], dict]:
    if (
        document.get("SchemaVersion") != 2
        or document.get("ArtifactType") != "container_image"
    ):
        raise ValueError("Unsupported Trivy report")
    os_metadata = document["Metadata"]["OS"]
    if os_metadata["Family"] not in {"debian", "alpine"}:
        raise ValueError("OS vulnerability coverage is not established")
    results = document["Results"]
    os_count = sum(
        len(result.get("Packages") or [])
        for result in results
        if result.get("Class") == "os-pkgs"
    )
    library_count = sum(
        len(result.get("Packages") or [])
        for result in results
        if result.get("Class") == "lang-pkgs"
    )
    if os_count < 1 or (backend and library_count < 1):
        raise ValueError("Trivy package inventory is incomplete")
    rows = []
    for result in results:
        # Trivy may omit the field for a clean result. A present field must be a
        # list: falsey malformed values (including null) are operational errors.
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise TypeError("Invalid Trivy findings")
        for vulnerability in vulnerabilities:
            fixed = vulnerability.get("FixedVersion", "")
            rows.append(
                finding(
                    vulnerability["PkgName"],
                    vulnerability["InstalledVersion"],
                    [vulnerability["VulnerabilityID"]],
                    vulnerability["Severity"],
                    [v.strip() for v in fixed.split(",")] if fixed else [],
                    result["Class"],
                )
            )
    return rows, {
        "os": safe(os_metadata["Family"], r"[a-z]+"),
        "os_packages": os_count,
        "library_packages": library_count,
        "os_eosl": bool(os_metadata.get("EOSL", False)),
    }


def execute(
    command: list[str], directory: Path, env: dict[str, str], timeout: int = 180
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def classify(code: int, findings: list[dict], finding_code: int) -> str:
    if code not in (0, finding_code) or bool(findings) != (code == finding_code):
        raise ValueError("Scanner exit status/report mismatch")
    return "FINDINGS" if findings else "CLEAN"


def collect(name: str, action: Callable[[], dict]) -> dict:
    started = time.perf_counter()
    try:
        result = action()
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        # Do not publish raw diagnostics, URLs, filenames, package descriptions or env.
        result = {
            "status": "ERROR",
            "error_type": type(error).__name__,
            "diagnostic": "Scanner failed, report invalid, or coverage incomplete",
        }
        if isinstance(error, subprocess.CalledProcessError):
            result["exit_code"] = error.returncode
            result["stderr_sha256"] = hashlib.sha256(
                (error.stderr or "").encode()
            ).hexdigest()
    result.update(
        {"target": name, "duration_seconds": round(time.perf_counter() - started, 3)}
    )
    return result


def checked_version(
    binary: str, tool: str, directory: Path, env: dict[str, str]
) -> str:
    resolved = shutil.which(binary, path=env.get("PATH", os.defpath))
    if resolved is None:
        raise FileNotFoundError("Scanner executable is unavailable")
    binary = str(Path(resolved).resolve())
    if tool == "trivy":
        with Path(binary).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != PINS["trivy_linux_x64_binary_sha256"]:
            raise ValueError("Trivy executable digest mismatch")
        result = execute(
            [binary, "version", "--format", "json", "--config", ""], directory, env
        )
        version = json.loads(result.stdout)["Version"]
    else:
        result = execute([binary, "--version"], directory, env)
        version = result.stdout.strip().removeprefix("pip-audit ")
    if result.returncode or version != PINS[tool]:
        raise ValueError("Scanner version differs from reviewed pin")
    return version


def scan_dependencies(
    args: argparse.Namespace, directory: Path, env: dict[str, str]
) -> dict:
    def python_audit() -> dict:
        version = checked_version(args.pip_audit, "pip-audit", directory, env)
        command = [
            args.pip_audit,
            "--disable-pip",
            "--require-hashes",
            "--strict",
            "--format",
            "json",
            "--desc",
            "off",
            "--aliases",
            "on",
            "--progress-spinner",
            "off",
            "--vulnerability-service",
            "pypi",
            "--cache-dir",
            str(directory / "pip-cache"),
        ]
        for name in ("requirements.lock", "requirements-build.lock"):
            command.extend(["-r", str(ROOT / "backend" / name)])
        result = execute(command, directory, env)
        rows, coverage = parse_pip(json.loads(result.stdout), inventory())
        return {
            "status": classify(result.returncode, rows, 1),
            "scanner": {"name": "pip-audit", "version": version},
            "exit_code": result.returncode,
            "database": {
                "source": "PyPI/Python Packaging Advisory Database",
                "revision": None,
                "note": "Live API has no snapshot revision",
            },
            "coverage": coverage,
            "findings": rows,
        }

    def node_audit() -> dict:
        version = checked_version(args.npm, "npm", directory, env)
        frontend = directory / "frontend"
        frontend.mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ROOT / "frontend" / name, frontend / name)
        lock_path = frontend / "package-lock.json"
        original = lock_path.read_bytes()
        command = [
            args.npm,
            "audit",
            "--json",
            "--package-lock-only",
            "--ignore-scripts",
            "--audit-level=info",
            "--registry=https://registry.npmjs.org",
            "--userconfig",
            str(directory / "npm-user.cfg"),
            "--globalconfig",
            str(directory / "npm-global.cfg"),
            "--cache",
            str(directory / "npm-cache"),
        ]
        result = execute(command, frontend, env)
        if lock_path.read_bytes() != original:
            raise ValueError("npm modified the input lock")
        rows, coverage = parse_npm(json.loads(result.stdout), json.loads(original))
        return {
            "status": classify(result.returncode, rows, 1),
            "scanner": {"name": "npm audit", "version": version},
            "exit_code": result.returncode,
            "database": {
                "source": "npm registry security advisories",
                "revision": None,
                "note": "Live API has no snapshot revision",
            },
            "coverage": coverage,
            "findings": rows,
        }

    return {
        "python": collect("python", python_audit),
        "node": collect("node", node_audit),
    }


def scan_images(args: argparse.Namespace, directory: Path, env: dict[str, str]) -> dict:
    names = ("backend-image", "nginx-image")
    if not args.build_images:
        return {
            name: {
                "target": name,
                "status": "NOT_RUN",
                "diagnostic": "Disposable image build not requested",
            }
            for name in names
        }
    results: dict[str, dict] = {}
    project = IntegrationProject("images", ["compose.images.test.yaml"])
    image_tools = runpy.run_path(str(ROOT / "scripts/validate-images.py"))
    tags = image_tools["images"](project)
    project.env.update({f"IMAGE_{key}": value for key, value in tags.items()})
    stage = "scanner/database"
    started = time.perf_counter()
    try:
        version = checked_version(args.trivy, "trivy", directory, env)
        cache = directory / "trivy-cache"
        base = [
            args.trivy,
            "image",
            "--config",
            "",
            "--cache-dir",
            str(cache),
            "--db-repository",
            "ghcr.io/aquasecurity/trivy-db:2",
        ]
        downloaded = execute([*base, "--download-db-only"], directory, env, 300)
        if downloaded.returncode:
            downloaded.check_returncode()
        database = json.loads((cache / "db/metadata.json").read_text())
        database = {
            key: database[key]
            for key in ("Version", "UpdatedAt", "NextUpdate", "DownloadedAt")
        }
        if database["Version"] != 2:
            raise ValueError("Unsupported vulnerability database schema")
        for key in ("UpdatedAt", "NextUpdate", "DownloadedAt"):
            safe(database[key], r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z")
            datetime.fromisoformat(database[key])
        with (cache / "db/trivy.db").open("rb") as stream:
            database["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        database["source"] = "ghcr.io/aquasecurity/trivy-db:2"
        stage = "disposable image build"
        with project:
            image_tools["reserve_images"](project)
            image_tools["build"](project)
            for name, kind in zip(names, ("BACKEND", "FRONTEND"), strict=True):

                def audit_image(
                    tag: str = tags[kind], backend: bool = kind == "BACKEND"
                ) -> dict:
                    archive = directory / "image.tar"
                    saved = execute(
                        ["docker", "image", "save", "--output", str(archive), tag],
                        ROOT,
                        env,
                    )
                    if saved.returncode:
                        raise ValueError("Image archive export failed")
                    with archive.open("rb") as stream:
                        archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                    result = execute(
                        [
                            *base,
                            "--skip-db-update",
                            "--scanners",
                            "vuln",
                            "--pkg-types",
                            "os,library",
                            "--ignorefile",
                            "",
                            "--list-all-pkgs",
                            "--exit-code",
                            "10",
                            "--format",
                            "json",
                            "--input",
                            str(archive),
                            "--timeout",
                            "4m",
                        ],
                        directory,
                        env,
                        300,
                    )
                    archive.unlink()
                    rows, coverage = parse_trivy(json.loads(result.stdout), backend)
                    return {
                        "status": classify(result.returncode, rows, 10),
                        "scanner": {"name": "Trivy", "version": version},
                        "exit_code": result.returncode,
                        "database": database,
                        "archive_sha256": archive_hash,
                        "coverage": coverage,
                        "findings": rows,
                    }

                results[name] = collect(name, audit_image)
            stage = "project cleanup"
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        for name in names:
            results.setdefault(
                name,
                {
                    "target": name,
                    "status": "ERROR",
                    "stage": stage,
                    "error_type": type(error).__name__,
                },
            )
            if isinstance(error, subprocess.CalledProcessError):
                results[name]["exit_code"] = error.returncode
            if stage == "project cleanup":
                results[name]["status"] = "ERROR"
                results[name]["cleanup"] = "ERROR"
    finally:
        try:
            image_tools["cleanup_images"](project)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            for name in names:
                results.setdefault(name, {"target": name})
                results[name].update(
                    {
                        "status": "ERROR",
                        "cleanup": "ERROR",
                        "cleanup_error_type": type(error).__name__,
                    }
                )
    for result in results.values():
        result["image_lifecycle_seconds"] = round(time.perf_counter() - started, 3)
    return results


def consolidate(targets: dict[str, dict]) -> tuple[dict, int]:
    if set(targets) != {"python", "node", "backend-image", "nginx-image"} or any(
        result.get("status") not in {"CLEAN", "FINDINGS", "ERROR", "NOT_RUN"}
        for result in targets.values()
    ):
        raise ValueError("Incomplete audit target inventory")
    states = {result["status"] for result in targets.values()}
    status, code = (
        ("ERROR", 2)
        if "ERROR" in states
        else ("INCOMPLETE", 3)
        if "NOT_RUN" in states
        else ("FINDINGS", 0)
        if "FINDINGS" in states
        else ("CLEAN", 0)
    )
    summary = {
        "status": status,
        "informational": True,
        "targets": targets,
        "severity_instances": dict(
            sorted(
                Counter(
                    row["severity"]
                    for result in targets.values()
                    for row in result.get("findings", [])
                ).items()
            )
        ),
    }
    return summary, code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pip-audit", default="pip-audit")
    parser.add_argument("--npm", default="npm")
    parser.add_argument("--trivy", default="trivy")
    parser.add_argument("--build-images", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.is_relative_to(ROOT):
        parser.error("Generated reports must stay outside the repository")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    targets = {
        name: {"target": name, "status": "NOT_RUN"}
        for name in ("python", "node", "backend-image", "nginx-image")
    }
    summary, code = consolidate(targets)
    temporary = tempfile.TemporaryDirectory(prefix="infrapulse-vulnerability-")
    try:
        directory = Path(temporary.name)
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(directory),
            "LANG": "C.UTF-8",
        }
        targets.update(scan_dependencies(args, directory, env))
        targets.update(scan_images(args, directory, env))
        summary, code = consolidate(targets)
        summary.update(
            {
                "queried_at": datetime.now(UTC).isoformat(),
                "duration_seconds": round(time.perf_counter() - started, 3),
                "scanner_pins": PINS,
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "input_sha256": {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in (
                        "backend/requirements.lock",
                        "backend/requirements-build.lock",
                        "frontend/package-lock.json",
                        "backend/Dockerfile",
                        "frontend/Dockerfile",
                    )
                },
            }
        )
        usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        summary["resources"] = {
            "child_cpu_seconds": round(
                usage_after.ru_utime
                + usage_after.ru_stime
                - usage_before.ru_utime
                - usage_before.ru_stime,
                3,
            ),
            "children_peak_rss_kib": usage_after.ru_maxrss,
            "temporary_cache_bytes": sum(
                path.stat().st_size for path in directory.rglob("*") if path.is_file()
            ),
            "note": "Linux child-process metrics exclude Docker daemon CPU and shared build cache",
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        summary, code = consolidate(targets)
        summary.update(
            {"status": "ERROR", "operational_error_type": type(error).__name__}
        )
        code = 2
    finally:
        try:
            temporary.cleanup()
        except OSError:
            summary.update({"status": "ERROR", "temporary_cleanup": "ERROR"})
            code = 2
    summary.setdefault("duration_seconds", round(time.perf_counter() - started, 3))
    for name, report in targets.items():
        (args.output_dir / (name + ".json")).write_text(
            json.dumps(report, indent=2) + "\n"
        )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "targets": {name: report["status"] for name, report in targets.items()},
                "severity_instances": summary["severity_instances"],
                "duration_seconds": summary["duration_seconds"],
            }
        )
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
