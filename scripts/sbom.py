"""Offline CycloneDX core; inventory coverage is intentionally not asserted."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft7Validator, RefResolver
from jsonschema.exceptions import RefResolutionError, ValidationError

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "security/cyclonedx-1.7"
HASHES = {
    "bom-1.7.schema.json": "df472ef4aaf593904c479293723a1a5c191d6672715c93b3c0b5c318f3914221",
    "cryptography-defs.schema.json": "018ea7f78b5208ec647cfd10f669cc9c26aba6aceb79c4da7f9c0ef4c99b60de",
    "jsf-0.82.schema.json": "8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae",
    "spdx.schema.json": "54a6288292bc6c90b0d3952f5f939f17436fa76704ffe68a46e5b78539c7cc1b",
}
PINS = json.loads((ROOT / "security/vulnerability-scanners.json").read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=300
    )


def verify_tool(tool: Path) -> None:
    if digest(tool) != PINS["trivy_linux_x64_binary_sha256"]:
        raise ValueError("Trivy SHA-256 mismatch")
    version = json.loads(
        run([str(tool.resolve()), "version", "--format", "json", "--config", ""]).stdout
    )
    if version.get("Version") != PINS["trivy"]:
        raise ValueError("Trivy version mismatch")


def validate(
    path: Path, expected_hash: str | None = None, require_components: bool = True
) -> dict:
    if expected_hash is not None and digest(path) != expected_hash:
        raise ValueError("SBOM SHA-256 mismatch")
    doc = json.loads(path.read_text())
    schemas = {}
    for name, expected in HASHES.items():
        source = SCHEMAS / name
        if digest(source) != expected:
            raise ValueError("Schema SHA-256 mismatch")
        schemas[name] = json.loads(source.read_text())
    schema = schemas["bom-1.7.schema.json"]
    store = {(SCHEMAS / name).as_uri(): value for name, value in schemas.items()}
    store.update(
        {
            f"http://cyclonedx.org/schema/{name}": value
            for name, value in schemas.items()
        }
    )

    def deny_remote(uri: str) -> None:
        raise ValueError(f"Remote schema reference forbidden: {uri}")

    resolver = RefResolver(
        base_uri=SCHEMAS.as_uri() + "/",
        referrer=schema,
        store=store,
        handlers={"http": deny_remote, "https": deny_remote},
    )
    Draft7Validator(schema, resolver=resolver).validate(doc)
    if doc.get("bomFormat") != "CycloneDX" or doc.get("specVersion") != "1.7":
        raise ValueError("Unsupported SBOM format")
    if require_components and not doc.get("components"):
        raise ValueError("Empty component inventory")
    tools = doc.get("metadata", {}).get("tools", {}).get("components", [])
    if not any(
        x.get("name") == "trivy" and x.get("version") == PINS["trivy"] for x in tools
    ):
        raise ValueError("Inconsistent tool metadata")
    if not doc.get("metadata", {}).get("component", {}).get("name"):
        raise ValueError("Missing inventory metadata")
    return doc


def inventory(source: Path, kind: str) -> tuple[set[tuple[str, str]], list[str]]:
    expected: set[tuple[str, str]] = set()
    excluded: list[str] = []
    if kind == "python":
        text = source.read_text().replace("\\\n", " ")
        names: set[str] = set()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.fullmatch(
                r"([A-Za-z0-9_.-]+)==(\S+)(?:\s+--hash=sha256:[a-f0-9]{64})+\s*", line
            )
            if not match:
                raise ValueError("Invalid Python lock entry")
            name, version = match.groups()
            name = re.sub(r"[-_.]+", "-", name).lower()
            if name in names:
                raise ValueError("Duplicate Python lock entry")
            names.add(name)
            expected.add((name, version))
    elif kind == "npm":
        lock = json.loads(source.read_text())
        if lock.get("lockfileVersion") not in (2, 3) or not isinstance(
            lock.get("packages"), dict
        ):
            raise ValueError("Unsupported npm lock")
        for path, entry in lock["packages"].items():
            if not path:
                excluded.append("Root project is metadata, not a dependency")
                continue
            if (
                not isinstance(entry, dict)
                or entry.get("link")
                or "node_modules/" not in path
            ):
                raise ValueError("Unsupported npm lock entry: " + path)
            name = path.rsplit("node_modules/", 1)[-1]
            version = entry.get("version")
            if not name or not isinstance(version, str) or not version:
                raise ValueError("Missing npm identity: " + path)
            item = (name, version)
            if item in expected:
                excluded.append(f"Repeated installation of {name}@{version}")
            expected.add(item)
    else:
        raise ValueError("Unknown inventory type")
    if not expected:
        raise ValueError("Empty inventory")
    return expected, excluded


def coverage(
    doc: dict, expected: set[tuple[str, str]], excluded: list[str], kind: str
) -> dict:
    found: set[tuple[str, str]] = set()
    unclassified: list[str] = []
    wrapper_seen = False
    for component in doc.get("components", []):
        purl = component.get("purl", "")
        prefix = "pkg:pypi/" if kind == "python" else "pkg:npm/"
        if not purl.startswith(prefix):
            wrapper_name = (
                "requirements.txt" if kind == "python" else "package-lock.json"
            )
            wrapper_type = "pip" if kind == "python" else "npm"
            properties = component.get("properties", [])
            marker = {(p.get("name"), p.get("value")) for p in properties}
            is_wrapper = (
                not purl
                and component.get("type") == "application"
                and component.get("name") == wrapper_name
                and not component.get("version")
                and not component.get("group")
                and ("aquasecurity:trivy:Class", "lang-pkgs") in marker
                and ("aquasecurity:trivy:Type", wrapper_type) in marker
                and Path(
                    doc.get("metadata", {}).get("component", {}).get("name", "")
                ).name
                == wrapper_name
            )
            if is_wrapper and not wrapper_seen:
                wrapper_seen = True
                excluded = [*excluded, f"Trivy inventory wrapper: {wrapper_name}"]
                continue
            unclassified.append(component.get("name", "<unnamed>"))
            continue
        identity = purl[len(prefix) :].split("?", 1)[0].split("#", 1)[0]
        if "@" not in identity:
            raise ValueError("Component missing version in purl")
        name, version = map(unquote, identity.rsplit("@", 1))
        if kind == "python":
            name = re.sub(r"[-_.]+", "-", name).lower()
            actual_name = re.sub(r"[-_.]+", "-", component.get("name", "")).lower()
            if component.get("group"):
                raise ValueError("Unexpected Python component group")
        else:
            group = component.get("group")
            actual_name = (
                f"{group}/{component.get('name', '')}"
                if group
                else component.get("name", "")
            )
        if name != actual_name or version != component.get("version"):
            raise ValueError("Component identity contradicts purl")
        item = (name, version)
        if item in found:
            raise ValueError("Duplicate SBOM component")
        found.add(item)
    if not found:
        raise ValueError("Empty SBOM inventory")
    missing = expected - found
    unexpected = found - expected
    return {
        "status": "COVERAGE_PASS"
        if not missing and not unexpected and not unclassified
        else "COVERAGE_INCOMPLETE",
        "expected": len(expected),
        "found": len(found),
        "missing": [f"{n}@{v}" for n, v in sorted(missing)],
        "unexpected": [f"{n}@{v}" for n, v in sorted(unexpected)],
        "version_mismatches": sorted(
            {n for n, _ in missing} & {n for n, _ in unexpected}
        ),
        "exclusions": excluded,
        "unclassified": unclassified,
    }


def check_output(output: Path, inputs: list[Path]) -> None:
    target = output.resolve()
    if target == ROOT or ROOT in target.parents or output.is_symlink():
        raise ValueError("Output must be outside the checkout and cannot be a symlink")
    protected = [
        *inputs,
        ROOT / "security/vulnerability-scanners.json",
        ROOT / "security/sbom-tools.lock",
        *(SCHEMAS / name for name in HASHES),
    ]
    for source in protected:
        if target == source.resolve() or (
            target.exists() and source.exists() and os.path.samefile(target, source)
        ):
            raise ValueError("Output collides with an input")


def atomic_write(output: Path, data: bytes, inputs: list[Path]) -> str:
    check_output(output, inputs)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=".sbom-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        checksum = hashlib.sha256(data).hexdigest()
        validate(temporary, checksum)
        check_output(output, inputs)
        os.replace(temporary, output)
        return checksum
    finally:
        temporary.unlink(missing_ok=True)


def generate(tool: Path, source: Path, output: Path, kind: str | None = None) -> dict:
    started = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()
    check_output(output, [tool, source])
    verify_tool(tool)
    if not source.exists():
        raise FileNotFoundError(source)
    source_hash = digest(source)
    expected, excluded = inventory(source, kind) if kind else (set(), [])
    commit = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="infrapulse-sbom-") as temporary:
        result = Path(temporary) / "sbom.json"
        scan_source = source.resolve()
        if kind == "python":
            scan_source = Path(temporary) / "requirements.txt"
            shutil.copyfile(source, scan_source)
        options = ["--include-dev-deps"] if kind == "npm" else []
        run(
            [
                str(tool.resolve()),
                "fs",
                "--format",
                "cyclonedx",
                "--offline-scan",
                "--skip-version-check",
                *options,
                "--output",
                str(result),
                str(scan_source),
            ]
        )
        doc = validate(result)
        report = coverage(doc, expected, excluded, kind) if kind else None
        if digest(source) != source_hash:
            raise ValueError("Inventory changed during generation")
        data = result.read_bytes()
    checksum = atomic_write(output, data, [tool, source])
    return {
        "status": report["status"] if report else "COVERAGE_PENDING",
        "coverage": report,
        "generation_status": "SUCCESS",
        "tool": "trivy",
        "tool_version": PINS["trivy"],
        "commit": commit,
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "sha256": checksum,
        "bytes": len(data),
        "generated_at": timestamp,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def resolve_image(reference: str) -> dict:
    try:
        result = run(["docker", "image", "inspect", reference])
    except subprocess.CalledProcessError as exc:
        # Inspect's nonzero result alone does not distinguish missing image from daemon failure.
        if "No such image" in exc.stderr:
            return {
                "status": "COVERAGE_PENDING",
                "reference": reference,
                "reason": "image unavailable locally",
            }
        raise
    images = json.loads(result.stdout)
    if not isinstance(images, list) or len(images) != 1:
        raise ValueError("Unexpected Docker inspect response")
    image_id = images[0].get("Id", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Invalid immutable Docker image ID")
    return {
        "reference": reference,
        "image_id": image_id,
        "repo_digests_unverified": images[0].get("RepoDigests") or [],
    }


def generate_image(tool: Path, reference: str, output: Path) -> dict:
    started = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()
    check_output(output, [tool])
    verify_tool(tool)
    identity = resolve_image(reference)
    if identity.get("status") == "COVERAGE_PENDING":
        return identity
    commit = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="infrapulse-image-sbom-") as temporary:
        result = Path(temporary) / "sbom.json"
        run(
            [
                str(tool.resolve()),
                "image",
                "--image-src",
                "docker",
                "--format",
                "cyclonedx",
                "--offline-scan",
                "--skip-version-check",
                "--output",
                str(result),
                identity["image_id"],
            ]
        )
        doc = validate(result)
        if doc["metadata"]["component"].get("name") != identity["image_id"]:
            raise ValueError("SBOM image identity mismatch")
        data = result.read_bytes()
    checksum = atomic_write(output, data, [tool])
    return {
        **identity,
        "status": "COVERAGE_PARTIAL",
        "generation_status": "SUCCESS",
        "coverage_note": "Detected packages only; completeness is not established.",
        "components": len(doc["components"]),
        "tool": "trivy",
        "tool_version": PINS["trivy"],
        "commit": commit,
        "sha256": checksum,
        "bytes": len(data),
        "generated_at": timestamp,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trivy", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path)
    inputs.add_argument("--image")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("python", "npm"))
    args = parser.parse_args()
    try:
        if args.image:
            if args.kind:
                raise ValueError("--kind applies only to lockfiles")
            manifest = generate_image(args.trivy, args.image, args.output)
        else:
            manifest = generate(args.trivy, args.source, args.output, args.kind)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        ValidationError,
        RefResolutionError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "COVERAGE_ERROR" if args.kind else "OPERATIONAL_ERROR",
                    "error": str(exc),
                }
            )
        )
        return 1
    print(json.dumps(manifest, sort_keys=True))
    return (
        1
        if manifest["status"] in {"COVERAGE_INCOMPLETE", "COVERAGE_PENDING"}
        and (args.kind or args.image)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
