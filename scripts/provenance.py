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
IMAGE_LABEL = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class ImageRootValidationError(ValueError):
    """Fail-closed image root error with safe, structured diagnostic fields."""

    def __init__(
        self,
        code: str,
        condition: str,
        expected: str,
        observed: str,
        image_name: str | None,
        image_id: str | None,
        message: str = "Invalid image SBOM root",
        root_component: object = None,
        sbom_source: str = "unknown",
    ) -> None:
        self.code = code
        self.condition = condition
        self.expected = expected
        self.observed = observed
        self.image_name = (
            image_name
            if image_name and IMAGE_LABEL.fullmatch(image_name)
            else "unknown-image"
        )
        self.image_id = (
            image_id
            if isinstance(image_id, str)
            and re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
            else "invalid-or-unavailable"
        )
        self.root_summary = _root_summary(root_component, self.image_id)
        self.sbom_source = (
            sbom_source
            if sbom_source in {"ci-evidence-sbom", "verification-regenerated-sbom"}
            else "unknown"
        )
        super().__init__(message)

    def diagnostic(self) -> dict[str, object]:
        return {
            "code": self.code,
            "image": self.image_name,
            "condition": self.condition,
            "expected": self.expected,
            "observed": self.observed,
            "expected_local_image_id": self.image_id,
            "sbom_source": self.sbom_source,
            "root": self.root_summary,
        }


def _observed_type(value: object) -> str:
    if value is None:
        return "missing"
    if isinstance(value, str) and value in {
        "application",
        "container",
        "cryptographic-asset",
        "data",
        "device",
        "file",
        "firmware",
        "framework",
        "library",
        "machine-learning-model",
        "operating-system",
    }:
        return value
    return "invalid-value"


def _root_summary(root: object, expected_image_id: str) -> dict[str, object]:
    if not isinstance(root, dict):
        return {
            "type_category": "missing-or-invalid",
            "identity_matches_expected": False,
            "bom_ref_present": False,
            "bom_ref_conformant": False,
            "purl": {
                "present": False,
                "string": False,
                "scheme": "unavailable",
                "package_type": "unavailable",
                "name_present": False,
                "version_present": False,
                "digest_format": "unavailable",
                "digest_matches_expected": False,
            },
        }
    bom_ref = root.get("bom-ref")
    purl = root.get("purl")
    purl_string = isinstance(purl, str)
    bounded = purl_string and len(purl) <= 4096
    scheme = (
        "pkg"
        if bounded and purl.startswith("pkg:")
        else ("other" if bounded and purl else "unavailable")
    )
    package_type = "unavailable"
    name_present = False
    version = ""
    if bounded and purl.startswith("pkg:"):
        package_path = purl[4:].split("?", 1)[0].split("#", 1)[0]
        if "/" in package_path:
            raw_type, name_version = package_path.split("/", 1)
            package_type = raw_type if raw_type in {"oci", "docker"} else "other"
            name, separator, version = name_version.partition("@")
            name_present = bool(name)
            if not separator:
                version = ""
    version = version.split("?", 1)[0].split("#", 1)[0]
    if not bounded:
        digest_format = "unavailable"
    elif not version:
        digest_format = "missing"
    elif re.fullmatch(r"sha256:[a-f0-9]{64}", version):
        digest_format = "sha256-valid"
    elif version.startswith("sha256:"):
        digest_format = "sha256-invalid"
    else:
        digest_format = "other"
    return {
        "type_category": _observed_type(root.get("type")),
        "identity_matches_expected": root.get("name") == expected_image_id,
        "bom_ref_present": bool(bom_ref),
        "bom_ref_conformant": isinstance(bom_ref, str) and bool(bom_ref),
        "purl": {
            "present": purl is not None and purl != "",
            "string": purl_string,
            "scheme": scheme,
            "package_type": package_type,
            "name_present": name_present,
            "version_present": bool(version),
            "digest_format": digest_format,
            "digest_matches_expected": (
                digest_format == "sha256-valid" and version == expected_image_id
            ),
        },
    }


def normalized_inventory(
    doc: dict,
    image_id: str,
    image_name: str | None = None,
    sbom_source: str = "unknown",
) -> tuple[Counter, Counter]:
    metadata = doc.get("metadata")
    root = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(root, dict):
        raise ImageRootValidationError(
            "SBOM_ROOT_COMPONENT_INVALID",
            "metadata.component",
            "object",
            "missing-or-invalid",
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    observed_type = root.get("type")
    if observed_type != "container":
        raise ImageRootValidationError(
            "SBOM_ROOT_TYPE_INVALID",
            "type",
            "container",
            _observed_type(observed_type),
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    if root.get("name") != image_id:
        raise ImageRootValidationError(
            "SBOM_ROOT_LOCAL_IDENTITY_MISMATCH",
            "name",
            "expected-local-image-id",
            "mismatch",
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    if not root.get("bom-ref"):
        raise ImageRootValidationError(
            "SBOM_ROOT_BOM_REF_MISSING",
            "bom-ref",
            "non-empty",
            "missing-or-empty",
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    root_purl = root.get("purl", "")
    if not isinstance(root_purl, str):
        purl_state = "non-string"
    elif not root_purl.startswith("pkg:oci/"):
        purl_state = "not-pkg-oci"
    elif "@" not in root_purl:
        purl_state = "digest-missing"
    else:
        purl_state = "valid-oci-shape"
    if purl_state != "valid-oci-shape":
        raise ImageRootValidationError(
            "SBOM_ROOT_PURL_INVALID",
            "purl",
            "pkg:oci/<name>@sha256:<digest>",
            purl_state,
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    purl_digest = root_purl.split("@", 1)[1].split("?", 1)[0]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", purl_digest):
        raise ImageRootValidationError(
            "SBOM_ROOT_PURL_DIGEST_INVALID",
            "purl-digest",
            "sha256:<64 lowercase hex characters>",
            "not-sha256-digest",
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
    if purl_digest != image_id:
        raise ImageRootValidationError(
            "SBOM_ROOT_PURL_DIGEST_MISMATCH",
            "purl-digest",
            "expected-local-image-id",
            "different-sha256-digest",
            image_name,
            image_id,
            root_component=root,
            sbom_source=sbom_source,
        )
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
    doc: dict,
    manifest: dict,
    tool: Path,
    expected_id: str | None,
    image_name: str | None = None,
) -> None:
    if not isinstance(expected_id, str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", expected_id
    ):
        raise ImageRootValidationError(
            "SBOM_EXPECTED_LOCAL_IMAGE_ID_INVALID",
            "expected-local-image-id",
            "sha256:<64 lowercase hex characters>",
            "missing-or-invalid",
            image_name,
            None,
            "Independent full image ID is required",
        )
    if image_name is None:
        raise ValueError("Independent logical image name is required")
    sbom.validate_image_name(image_name)
    if (
        manifest.get("image_id") != expected_id
        or manifest.get("status") != "COVERAGE_PARTIAL"
        or manifest.get("components") != len(doc.get("components", []))
    ):
        raise ValueError("Image ID or inventory manifest mismatch")
    identity = sbom.resolve_image(expected_id)
    if identity.get("image_id") != expected_id:
        raise ValueError("Expected local image unavailable")
    original = normalized_inventory(doc, expected_id, image_name, "ci-evidence-sbom")
    with tempfile.TemporaryDirectory(prefix="infrapulse-verify-image-") as directory:
        generated = Path(directory) / "fresh.json"
        report = sbom.generate_image(
            tool, expected_id, generated, image_name=image_name
        )
        if (
            report.get("generation_status") != "SUCCESS"
            or report.get("status") != "COVERAGE_PARTIAL"
            or report.get("image_id") != expected_id
        ):
            raise ValueError("Image regeneration failed")
        fresh = sbom.validate(generated, report["sha256"])
        if (
            report.get("components") != len(fresh.get("components", []))
            or normalized_inventory(
                fresh, expected_id, image_name, "verification-regenerated-sbom"
            )
            != original
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
    artifact: Path,
    manifest_path: Path,
    tool: Path,
    expected_image: str | None = None,
    image_name: str | None = None,
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
        verify_image(doc, manifest, tool, expected_image, image_name)
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
    image_name: str | None = None,
) -> None:
    expected = statement(artifact, manifest, tool, expected_image, image_name)
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
    parser.add_argument("--image-name", help="Validated logical image name")
    args = parser.parse_args()
    try:
        if args.action == "generate":
            doc = statement(
                args.sbom,
                args.manifest,
                args.trivy,
                args.expected_image_id,
                args.image_name,
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
                args.image_name,
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
        result = {"status": "ERROR", "error": str(exc)}
        if isinstance(exc, ImageRootValidationError):
            result["diagnostic"] = exc.diagnostic()
        print(json.dumps(result))
        return 1
    print(json.dumps({"status": "INTEGRITY_VERIFIED", "authenticity": "NOT_VERIFIED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
