"""Unsigned local provenance for SBOMs produced by the F3A runner."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import sbom
from jsonschema.exceptions import RefResolutionError, ValidationError

TYPE = "https://in-toto.io/Statement/v1"
PREDICATE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://github.com/jacivaldocarvalho/jc-infrapulse/blob/develop/docs/provenance-f3b1.md#local-sbom"


def normalized_inventory(doc: dict, image_id: str) -> tuple[Counter, Counter]:
    root = doc.get("metadata", {}).get("component", {})
    root_purl = root.get("purl", "")
    if (
        root.get("type") != "container"
        or root.get("name") != image_id
        or not root.get("bom-ref")
        or not isinstance(root_purl, str)
        or not root_purl.startswith("pkg:oci/")
        or "@" not in root_purl
        or root_purl.split("@", 1)[1].split("?", 1)[0] != image_id
    ):
        raise ValueError("Invalid image SBOM root")
    if (
        not isinstance(doc.get("components"), list)
        or not doc["components"]
        or not isinstance(doc.get("dependencies"), list)
        or not doc["dependencies"]
    ):
        raise ValueError("Incomplete image inventory or dependency graph")

    def canonical(value: object) -> object:
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items() if k != "bom-ref"}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        return value

    nodes: Counter = Counter()
    root_key = json.dumps(canonical(root), sort_keys=True, separators=(",", ":"))
    nodes[root_key] += 1
    refs = {root["bom-ref"]: root_key}
    for component in doc["components"]:
        if (
            not isinstance(component, dict)
            or not component.get("bom-ref")
            or not component.get("type")
            or not component.get("name")
        ):
            raise ValueError("Unidentified image component")
        ref = component["bom-ref"]
        if ref in refs:
            raise ValueError("Ambiguous component reference")
        key = json.dumps(canonical(component), sort_keys=True, separators=(",", ":"))
        refs[ref] = key
        nodes[key] += 1
    edges: Counter = Counter()
    seen = set()
    for relation in doc["dependencies"]:
        if not isinstance(relation, dict) or set(relation) - {"ref", "dependsOn"}:
            raise ValueError("Unsupported dependency relationship")
        ref = relation.get("ref")
        targets = relation.get("dependsOn", [])
        if (
            ref not in refs
            or ref in seen
            or not isinstance(targets, list)
            or any(x not in refs for x in targets)
            or len(set(targets)) != len(targets)
        ):
            raise ValueError("Unresolved or duplicate dependency reference")
        seen.add(ref)
        if refs[ref] != "IMAGE_ROOT" and nodes[refs[ref]] != 1:
            raise ValueError("Ambiguous duplicate dependency identity")
        for target in targets:
            if refs[target] != "IMAGE_ROOT" and nodes[refs[target]] != 1:
                raise ValueError("Ambiguous duplicate dependency identity")
            edges[(refs[ref], refs[target])] += 1
        edges[(refs[ref], None)] += 1
    return nodes, edges


def verify_image(
    doc: dict, manifest: dict, tool: Path, expected_id: str | None
) -> None:
    if not isinstance(expected_id, str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", expected_id
    ):
        raise ValueError("Independent full image ID is required")
    if (
        manifest.get("image_id") != expected_id
        or manifest.get("status") != "COVERAGE_PARTIAL"
        or manifest.get("components") != len(doc.get("components", []))
    ):
        raise ValueError("Image ID or inventory manifest mismatch")
    identity = sbom.resolve_image(expected_id)
    if identity.get("image_id") != expected_id:
        raise ValueError("Expected local image unavailable")
    original = normalized_inventory(doc, expected_id)
    with tempfile.TemporaryDirectory(prefix="infrapulse-verify-image-") as directory:
        generated = Path(directory) / "fresh.json"
        report = sbom.generate_image(tool, expected_id, generated)
        if (
            report.get("generation_status") != "SUCCESS"
            or report.get("status") != "COVERAGE_PARTIAL"
            or report.get("image_id") != expected_id
        ):
            raise ValueError("Image regeneration failed")
        fresh = sbom.validate(generated, report["sha256"])
        if (
            report.get("components") != len(fresh.get("components", []))
            or normalized_inventory(fresh, expected_id) != original
        ):
            raise ValueError("Image inventory mismatch")
    if sbom.resolve_image(expected_id).get("image_id") != expected_id:
        raise ValueError("Image changed during verification")


def verify_inventory(source: str, source_hash: str, doc: dict, manifest: dict) -> Path:
    allowed = {
        str(sbom.ROOT / "backend/requirements.lock"): (
            "backend",
            "requirements.lock",
            "python",
            "requirements.txt",
        ),
        str(sbom.ROOT / "frontend/package-lock.json"): (
            "frontend",
            "package-lock.json",
            "npm",
            "package-lock.json",
        ),
    }
    if source not in allowed:
        raise ValueError("Unexpected inventory path")
    directory, filename, kind, wrapper = allowed[source]
    if (
        Path(doc.get("metadata", {}).get("component", {}).get("name", "")).name
        != wrapper
    ):
        raise ValueError("SBOM inventory type mismatch")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd = os.open(sbom.ROOT, flags | os.O_DIRECTORY)
    try:
        directory_fd = os.open(directory, flags | os.O_DIRECTORY, dir_fd=root_fd)
        try:
            fd = os.open(filename, flags | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_size > 10 * 1024 * 1024
                ):
                    raise ValueError("Inventory is not an acceptable regular file")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual_hash != source_hash:
                    raise ValueError("Input inventory digest mismatch")
                expected, excluded = sbom.inventory(Path(f"/proc/self/fd/{fd}"), kind)
                report = sbom.coverage(doc, expected, excluded, kind)
                after = os.fstat(fd)
                path_stat = os.stat(
                    filename, dir_fd=directory_fd, follow_symlinks=False
                )
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or (after.st_dev, after.st_ino) != (
                    path_stat.st_dev,
                    path_stat.st_ino,
                ):
                    raise ValueError("Inventory changed during verification")
                if report != manifest.get("coverage") or report[
                    "status"
                ] != manifest.get("status"):
                    raise ValueError("Manifest coverage does not match SBOM")
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)
    return sbom.ROOT / directory / filename


def statement(
    artifact: Path, manifest_path: Path, tool: Path, expected_image: str | None = None
) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get("generation_status") != "SUCCESS":
        raise ValueError("Invalid or unsuccessful SBOM manifest")
    checksum = manifest.get("sha256")
    commit = manifest.get("commit")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Invalid SBOM digest")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid commit")
    current = sbom.run(
        ["git", "-C", str(sbom.ROOT), "rev-parse", "HEAD"]
    ).stdout.strip()
    if current != commit:
        raise ValueError("Commit does not match checkout")
    if (
        manifest.get("tool") != "trivy"
        or manifest.get("tool_version") != sbom.PINS["trivy"]
    ):
        raise ValueError("Invalid tool metadata")
    generated_at = manifest.get("generated_at")
    if (
        not isinstance(generated_at, str)
        or datetime.fromisoformat(generated_at).tzinfo is None
    ):
        raise ValueError("Missing or invalid generation time")
    sbom.verify_tool(tool)
    doc = sbom.validate(artifact, checksum)
    if manifest.get("bytes") != artifact.stat().st_size:
        raise ValueError("Artifact size mismatch")
    dependencies = []
    parameters = {
        "context": "local-untrusted",
        "manifestSha256": sbom.digest(manifest_path),
        "commit": commit,
        "tool": "trivy",
        "toolVersion": sbom.PINS["trivy"],
        "coverageStatus": manifest.get("status"),
    }
    if manifest.get("source") and manifest.get("image_id"):
        raise ValueError("Ambiguous inventory source")
    if manifest.get("source"):
        source_path = manifest["source"]
        source_hash = manifest.get("source_sha256")
        if (
            not isinstance(source_path, str)
            or not isinstance(source_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", source_hash)
        ):
            raise ValueError("Input inventory digest mismatch")
        source = verify_inventory(source_path, source_hash, doc, manifest)
        dependencies.append(
            {"uri": source.resolve().as_uri(), "digest": {"sha256": source_hash}}
        )
        parameters["source"] = str(source.resolve())
    elif manifest.get("image_id"):
        verify_image(doc, manifest, tool, expected_image)
        parameters["imageId"] = expected_image
        parameters["imageReference"] = manifest.get("reference")
    else:
        raise ValueError("Missing inventory source")
    if parameters["coverageStatus"] not in {
        "COVERAGE_PASS",
        "COVERAGE_INCOMPLETE",
        "COVERAGE_PENDING",
        "COVERAGE_PARTIAL",
    }:
        raise ValueError("Invalid coverage status")
    dependencies.append(
        {"uri": tool.resolve().as_uri(), "digest": {"sha256": sbom.digest(tool)}}
    )
    return {
        "_type": TYPE,
        "subject": [{"name": artifact.name, "digest": {"sha256": checksum}}],
        "predicateType": PREDICATE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": parameters,
                "resolvedDependencies": dependencies,
            },
            "runDetails": {"builder": {}, "metadata": {"startedOn": generated_at}},
        },
    }


def verify(
    document: dict,
    artifact: Path,
    manifest: Path,
    tool: Path,
    expected_image: str | None = None,
) -> None:
    expected = statement(artifact, manifest, tool, expected_image)
    if document != expected:
        raise ValueError("Statement differs from verified local evidence")


def write_statement(output: Path, document: dict, inputs: list[Path]) -> None:
    sbom.check_output(output, inputs)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=".provenance-",
        delete=False,
    ) as stream:
        temp = Path(stream.name)
        try:
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        sbom.check_output(output, inputs)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "verify"))
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trivy", type=Path, required=True)
    parser.add_argument("--statement", type=Path, required=True)
    parser.add_argument(
        "--expected-image-id", help="Independent full local image ID (sha256:...)"
    )
    args = parser.parse_args()
    try:
        if args.action == "generate":
            doc = statement(
                args.sbom, args.manifest, args.trivy, args.expected_image_id
            )
            source = json.loads(args.manifest.read_text()).get("source")
            inputs = [args.sbom, args.manifest, args.trivy]
            if source:
                inputs.append(Path(source))
            write_statement(args.statement, doc, inputs)
        else:
            verify(
                json.loads(args.statement.read_text()),
                args.sbom,
                args.manifest,
                args.trivy,
                args.expected_image_id,
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        ValidationError,
        RefResolutionError,
    ) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 1
    print(json.dumps({"status": "INTEGRITY_VERIFIED", "authenticity": "NOT_VERIFIED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
