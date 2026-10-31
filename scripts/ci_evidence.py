"""Generate unsigned CI SBOM and provenance evidence outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import provenance
import sbom
import vulnerability_ci
from jsonschema.exceptions import RefResolutionError, ValidationError

ROOT = sbom.ROOT
SOURCES = {
    "python": (ROOT / "backend/requirements.lock", "python"),
    "npm": (ROOT / "frontend/package-lock.json", "npm"),
}
IMAGE_TARGETS = ("backend-image", "nginx-image")
MAX_STATEMENT_BYTES = 16 * 1024 * 1024
LIMITATIONS = [
    "Image inventory is detected-only and remains COVERAGE_PARTIAL.",
    "Only backend-image and nginx-image archives from docker-build are covered; other Compose images are not analyzed.",
    "Statements are unsigned; builder identity, authenticity, and SLSA level are not asserted.",
    "Inventory equality does not establish scanner completeness or reproducible builds.",
]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Persisted statement contains duplicate JSON keys")
        value[key] = item
    return value


def _read_persisted_statement(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Persisted statement path is not normalized")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_STATEMENT_BYTES:
            raise ValueError("Persisted statement is not an acceptable regular file")
        contents = bytearray()
        while len(contents) <= MAX_STATEMENT_BYTES:
            block = os.read(fd, min(65536, MAX_STATEMENT_BYTES + 1 - len(contents)))
            if not block:
                break
            contents.extend(block)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            len(contents) > MAX_STATEMENT_BYTES
            or before_identity != after_identity
            or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("Persisted statement changed while being read")
    finally:
        os.close(fd)
    raw = bytes(contents)
    document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(document, dict):
        raise TypeError("Persisted statement must be a JSON object")
    return document, raw


def verify_handoff(directory: Path, commit: str) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Image handoff directory is missing or unsafe")
    manifest_path = directory / "manifest.json"
    if not regular_file(manifest_path):
        raise ValueError("Image handoff manifest is missing or unsafe")
    manifest = vulnerability_ci.verify_handoff(directory)
    if manifest.get("commit") != commit:
        raise ValueError("Image handoff commit mismatch")
    if set(manifest.get("images", {})) != set(IMAGE_TARGETS):
        raise ValueError("Unexpected image handoff targets")
    return manifest


def run_checked(args: list[str], error: str) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(error) from exc
    return result.stdout.strip()


def file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def generate_one(
    name: str,
    kind: str,
    source: Path | None,
    image_id: str | None,
    tool: Path,
    output: Path,
    commit: str,
    material: dict[str, Any],
) -> dict[str, Any]:
    sbom_path = output / f"{name}.cdx.json"
    manifest_path = output / f"{name}.manifest.json"
    statement_path = output / f"{name}.provenance.json"
    if kind == "image":
        if image_id is None:
            raise ValueError(f"{name}: image ID is required")
        generation = sbom.generate_image(tool, image_id, sbom_path)
    else:
        if source is None:
            raise ValueError(f"{name}: lock source is required")
        generation = sbom.generate(tool, source, sbom_path, kind)
    if generation.get("generation_status") != "SUCCESS":
        raise RuntimeError(f"{name}: SBOM generation did not succeed")
    expected_coverage = "COVERAGE_PARTIAL" if kind == "image" else "COVERAGE_PASS"
    if generation.get("status") != expected_coverage:
        raise ValueError(
            f"{name}: unexpected coverage state {generation.get('status')}"
        )
    if generation.get("commit") != commit:
        raise ValueError(f"{name}: generated commit mismatch")
    if source is not None and material.get("sha256") != generation.get("source_sha256"):
        raise ValueError(f"{name}: source material digest changed during generation")
    write_json(manifest_path, generation)
    document = provenance.statement(sbom_path, manifest_path, tool, image_id)
    source_inputs = [sbom_path, manifest_path, tool]
    if source is not None:
        source_inputs.append(source)
    provenance.write_statement(statement_path, document, source_inputs)
    persisted_document, persisted_bytes = _read_persisted_statement(statement_path)
    provenance.verify(
        persisted_document,
        sbom_path,
        manifest_path,
        tool,
        expected_image=image_id,
    )
    if sbom.digest(sbom_path) != generation.get("sha256"):
        raise ValueError(f"{name}: SBOM digest mismatch after validation")
    statement_record = file_record(output, statement_path)
    if statement_record["sha256"] != hashlib.sha256(
        persisted_bytes
    ).hexdigest() or statement_record["bytes"] != len(persisted_bytes):
        raise ValueError(f"{name}: persisted statement changed after validation")
    return {
        "name": name,
        "kind": kind,
        "inventory_source": str(source.relative_to(ROOT))
        if source
        else "docker-build image archive",
        "coverage_status": generation["status"],
        "coverage": generation.get("coverage"),
        "image_identity": (
            {"kind": "docker-local-image-id", "value": image_id} if image_id else None
        ),
        "materials": [material],
        "duration_seconds": generation.get("duration_seconds"),
        "artifacts": [
            file_record(output, sbom_path),
            file_record(output, manifest_path),
            statement_record,
        ],
    }


def generate_evidence(
    output: Path,
    handoff: Path,
    tool: Path,
    commit: str,
    run_id: str,
    run_attempt: str,
    repository: str,
    workflow: str,
) -> dict[str, Any]:
    started = time.monotonic()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid CI commit SHA")
    if run_id != os.environ.get("GITHUB_RUN_ID") or run_attempt != os.environ.get(
        "GITHUB_RUN_ATTEMPT"
    ):
        raise ValueError("CI run identity mismatch")
    actual_commit = run_checked(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], "Cannot resolve checkout commit"
    )
    if actual_commit != commit:
        raise ValueError("Checkout does not match CI commit")
    if output.is_symlink() or output.exists() or output.resolve().is_relative_to(ROOT):
        raise ValueError("Evidence output must be a new directory outside the checkout")
    output.mkdir(parents=True, mode=0o700)
    handoff_manifest = verify_handoff(handoff, commit)
    records = [
        generate_one(
            name,
            kind,
            source,
            None,
            tool,
            output,
            commit,
            {
                "path": str(source.relative_to(ROOT)),
                "sha256": sha256(source),
                "bytes": source.stat().st_size,
            },
        )
        for name, (source, kind) in SOURCES.items()
    ]
    for target in IMAGE_TARGETS:
        item = handoff_manifest["images"][target]
        archive = handoff / item["file"]
        run_checked(
            ["docker", "load", "--input", str(archive)], "Docker image load failed"
        )
        image_id = item["image_id"]
        actual_id = run_checked(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_id],
            "Loaded image cannot be inspected",
        )
        if actual_id != image_id:
            raise ValueError(f"{target}: loaded image ID differs from handoff")
        records.append(
            generate_one(
                target,
                "image",
                None,
                image_id,
                tool,
                output,
                commit,
                {
                    "path": f"docker-build/{item['file']}",
                    "sha256": item["sha256"],
                    "bytes": item["bytes"],
                },
            )
        )

    files = [file for record in records for file in record["artifacts"]]
    for item in files:
        actual = file_record(output, output / item["path"])
        if actual != item:
            raise ValueError("Evidence artifact changed before manifest creation")
    manifest = {
        "schema_version": 1,
        "repository": repository,
        "workflow": workflow,
        "workflow_run_id": run_id,
        "run_attempt": int(run_attempt),
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "signature_status": "NOT_GENERATED",
        "authenticity": "NOT_VERIFIED",
        "builder_identity": "NOT_ASSERTED",
        "slsa_level": "NOT_CLAIMED",
        "tools": {
            "trivy": {
                "version": sbom.PINS["trivy"],
                "sha256": sbom.PINS["trivy_linux_x64_binary_sha256"],
            },
            "sbom_tools_lock_sha256": sha256(ROOT / "security/sbom-tools.lock"),
        },
        "overall_coverage": "COVERAGE_PARTIAL",
        "components": records,
        "files": files,
        "duration_seconds": round(time.monotonic() - started, 3),
        "bytes": sum(item["bytes"] for item in files),
        "limitations": LIMITATIONS,
    }
    write_json(output / "evidence-manifest.json", manifest)
    (output / "evidence-manifest.sha256").write_text(
        f"{sha256(output / 'evidence-manifest.json')}  evidence-manifest.json\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--trivy", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    args = parser.parse_args()
    try:
        manifest = generate_evidence(
            args.output,
            args.handoff,
            args.trivy,
            args.commit,
            args.run_id,
            args.run_attempt,
            args.repository,
            args.workflow,
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
        ValidationError,
        RefResolutionError,
    ) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "EVIDENCE_READY",
                "coverage": manifest["overall_coverage"],
                "files": len(manifest["files"]),
                "bytes": manifest["bytes"],
                "duration_seconds": manifest["duration_seconds"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
