"""Run the fixed F3C.3.2 disposable-runner validation without publishing output."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

REPOSITORY = "jacivaldocarvalho/jc-infrapulse"
RUN_ID = "41238810850"
RUN_ATTEMPT = 1
EVIDENCE_COMMIT = "9f237be45ccd6e7977f50f85d37ebcc32d81a2bb"
VALIDATOR_COMMIT = "09a281533864c1c7c582a14ba08992ffa4976da4"
GIB = 1024**3
MAX_API_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_ZIP = 64 * 1024 * 1024
MAX_HANDOFF_ZIP = 1024 * 1024 * 1024
MAX_REPORT_ZIP = 2 * 1024 * 1024
MAX_EVIDENCE_TOTAL = 128 * 1024 * 1024
MAX_HANDOFF_TOTAL = 4 * GIB
MAX_REPORT_TOTAL = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * GIB
COSIGN_V313_LINUX_AMD64_BYTES = 141_178_250
TRIVY_EVIDENCE_URI = "file:///home/runner/work/_temp/sbom-tools/bin/trivy"
MIN_DOWNLOAD_HEADROOM = 2 * GIB
LOAD_HEADROOM = 1024**3
OWNER_MARKER = ".infrapulse-f3c3-owner.json"
IMAGE_ID_RE = re.compile(r"sha256:[a-f0-9]{64}\Z")
SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")
ARTIFACT_PREFIX = f"{EVIDENCE_COMMIT}-1"


@dataclass(frozen=True)
class ArtifactSpec:
    key: str
    name: str
    zip_limit: int
    expanded_limit: int
    members: frozenset[str]


ARTIFACTS = (
    ArtifactSpec(
        "evidence",
        f"ci-evidence-{ARTIFACT_PREFIX}",
        MAX_EVIDENCE_ZIP,
        MAX_EVIDENCE_TOTAL,
        frozenset(
            {
                "evidence-manifest.json",
                "evidence-manifest.sha256",
                *(
                    f"{name}.{suffix}"
                    for name in ("python", "npm", "backend-image", "nginx-image")
                    for suffix in ("cdx.json", "manifest.json", "provenance.json")
                ),
            }
        ),
    ),
    ArtifactSpec(
        "handoff",
        f"vulnerability-images-{ARTIFACT_PREFIX}",
        MAX_HANDOFF_ZIP,
        MAX_HANDOFF_TOTAL,
        frozenset({"manifest.json", "backend-image.tar", "nginx-image.tar"}),
    ),
    ArtifactSpec(
        "validation",
        "docker-image-validation",
        MAX_REPORT_ZIP,
        MAX_REPORT_TOTAL,
        frozenset({"report.json"}),
    ),
)


class ValidationError(RuntimeError):
    """A fail-closed validation error safe to show in workflow logs."""


@contextmanager
def _phase(name: str) -> Iterator[None]:
    print(f"PHASE {name} START", flush=True)
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - prevent sensitive exception text in logs
        allowed = (ValidationError, OSError, ValueError, KeyError, TypeError)
        kind = type(exc).__name__ if type(exc) in allowed else "OtherError"
        print(f"PHASE {name} FAIL {kind}", file=sys.stderr, flush=True)
        raise ValidationError("Integrated validation phase failed") from None
    else:
        print(f"PHASE {name} PASS", flush=True)


@contextmanager
def _subphase(marker: str) -> Iterator[None]:
    print(f"SUBPHASE {marker} START", flush=True)
    try:
        yield
    except Exception as exc:
        allowed = (ValidationError, OSError, ValueError, KeyError, TypeError)
        kind = type(exc).__name__ if type(exc) in allowed else "OtherError"
        print(f"SUBPHASE {marker} FAIL {kind}", file=sys.stderr, flush=True)
        raise
    print(f"SUBPHASE {marker} PASS", flush=True)


@contextmanager
def _sign_diagnostics(evidence: Any, signature: Any) -> Iterator[None]:
    """Temporarily instrument fixed validator entry points without changing results."""
    targets = (
        (evidence, "_load_and_validate", "evidence_manifest"),
        (evidence, "_validate_handoff_archives", "handoff_archives"),
        (evidence, "_validate_signature_output", "signature_destination"),
        (signature, "_trusted_key", "trust_policy"),
        (signature, "_validate_provenance", "provenance"),
        (signature, "sign_blob_bytes", "sign_blob"),
        (signature, "verify_blob_bytes", "verify_blob"),
        (signature, "verify_signature", "verify_component"),
    )
    originals = []
    component_ids = ("python", "npm", "backend_image", "nginx_image")
    provenance_count = 0

    def instrument(original: Any, label: str) -> Any:
        @wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            nonlocal provenance_count
            marker = label
            if label == "provenance":
                marker = "provenance_" + component_ids[provenance_count % 4]
                provenance_count += 1
            with _subphase(marker):
                return original(*args, **kwargs)

        return wrapped

    try:
        for module, attribute, label in targets:
            original = getattr(module, attribute)
            originals.append((module, attribute, original))
            setattr(module, attribute, instrument(original, label))
        yield
    finally:
        for module, attribute, original in reversed(originals):
            setattr(module, attribute, original)


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("JSON contains a duplicate key")
        result[key] = value
    return result


def _json_bytes(raw: bytes, label: str, limit: int = MAX_API_BYTES) -> Any:
    if len(raw) > limit:
        raise ValidationError(f"{label} exceeds its size limit")
    try:
        return json.loads(raw, object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} is malformed JSON") from exc


def _require_run(run: dict[str, Any], repository: str = REPOSITORY) -> None:
    if (
        run.get("id") != int(RUN_ID)
        or run.get("run_attempt") != RUN_ATTEMPT
        or run.get("head_sha") != EVIDENCE_COMMIT
        or run.get("event") != "push"
        or run.get("head_branch") != "develop"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != repository
        or not isinstance(run.get("head_repository"), dict)
        or run["head_repository"].get("full_name") != repository
    ):
        raise ValidationError("Source workflow run identity or result mismatch")


def _require_artifacts(
    records: list[dict[str, Any]], run_id: int = int(RUN_ID)
) -> dict[str, dict[str, Any]]:
    expected_names = {spec.name for spec in ARTIFACTS}
    found = [item for item in records if item.get("name") in expected_names]
    by_name = {item.get("name"): item for item in found}
    if len(found) != len(ARTIFACTS) or set(by_name) != expected_names:
        raise ValidationError("Required run artifacts are missing or ambiguous")

    result: dict[str, dict[str, Any]] = {}
    for spec in ARTIFACTS:
        item = by_name[spec.name]
        workflow_run = item.get("workflow_run")
        size = item.get("size_in_bytes")
        digest = item.get("digest")
        if (
            item.get("expired") is not False
            or type(item.get("id")) is not int
            or item["id"] <= 0
            or type(size) is not int
            or size <= 0
            or size > spec.zip_limit
            or not isinstance(workflow_run, dict)
            or workflow_run.get("id") != run_id
            or workflow_run.get("head_sha") != EVIDENCE_COMMIT
        ):
            raise ValidationError(f"Artifact metadata is invalid: {spec.key}")
        if digest is not None and (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValidationError(f"Artifact digest metadata is invalid: {spec.key}")
        result[spec.key] = {
            "id": item.get("id"),
            "name": spec.name,
            "size_in_bytes": size,
            "digest": digest,
        }
    return result


def _api_base() -> str:
    value = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or parts.hostname != "api.github.com" or parts.path:
        raise ValidationError("Unexpected GitHub API endpoint")
    return value


def _api_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "infrapulse-f3c3-validation",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return _json_bytes(response.read(MAX_API_BYTES + 1), "GitHub API response")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        raise ValidationError("GitHub API metadata request failed") from None


def _source_metadata(token: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not token:
        raise ValidationError("GitHub Actions read token is unavailable")
    base = _api_base()
    run_url = f"{base}/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
    artifacts_url = f"{run_url}/artifacts?per_page=100"
    run = _api_json(run_url, token)
    artifacts_doc = _api_json(artifacts_url, token)
    if not isinstance(run, dict) or not isinstance(artifacts_doc, dict):
        raise ValidationError("GitHub API returned an unexpected response")
    _require_run(run)
    records = artifacts_doc.get("artifacts")
    if not isinstance(records, list) or len(records) > 100:
        raise ValidationError("GitHub artifact metadata is malformed")
    artifacts = _require_artifacts(records)
    return run, artifacts


def _runner_temp() -> Path:
    value = os.environ.get("RUNNER_TEMP")
    if not value:
        raise ValidationError("GitHub runner temporary directory is unavailable")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise ValidationError("GitHub runner temporary path is invalid")
    return path


def _trivy_destination(path: Path, *, create: bool) -> Path:
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise ValidationError("Trivy destination requires a GitHub-hosted runner")
    raw_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    temp = _runner_temp()
    target = temp / "sbom-tools" / "bin"
    if (
        not raw_temp.is_absolute()
        or raw_temp != temp
        or path != target
        or (target / "trivy").as_uri() != TRIVY_EVIDENCE_URI
    ):
        raise ValidationError("Trivy destination or evidence URI is invalid")
    if create:
        root_fd = os.open(
            temp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            os.mkdir("sbom-tools", mode=0o700, dir_fd=root_fd)
            parent_fd = os.open(
                "sbom-tools",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
            try:
                os.mkdir("bin", mode=0o700, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            os.close(root_fd)
    for directory in (target.parent, target):
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValidationError("Trivy directory is not private and owned")
    return target


def _validate_trivy_binary(trivy: Path) -> None:
    expected = _trivy_destination(trivy.parent, create=False) / "trivy"
    if trivy != expected:
        raise ValidationError("Trivy binary path is not authorized")
    info = trivy.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValidationError("Trivy binary is not an owned regular file")


def _work_dir(path: Path, *, must_exist: bool) -> Path:
    temp = _runner_temp()
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.parent.resolve(strict=True) != temp:
        raise ValidationError("Work directory must be a direct child of RUNNER_TEMP")
    if candidate.name != f"infrapulse-f3c3-runner-{RUN_ID}":
        raise ValidationError("Unexpected work directory name")
    if must_exist:
        info = candidate.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValidationError("Work directory is not a private real directory")
    elif candidate.exists() or candidate.is_symlink():
        raise ValidationError("Work directory already exists")
    return candidate


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError as exc:
        raise ValidationError("Unable to determine runner disk capacity") from exc


def require_free_space(path: Path, required: int, label: str) -> None:
    if required < 0 or _free_bytes(path) < required:
        raise ValidationError(f"Insufficient free disk space for {label}")


def _write_private(path: Path, raw: bytes) -> None:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def preflight(work_dir: Path, token: str) -> dict[str, Any]:
    work_dir = _work_dir(work_dir, must_exist=False)
    run, artifacts = _source_metadata(token)
    compressed_total = sum(item["size_in_bytes"] for item in artifacts.values())
    require_free_space(
        _runner_temp(), compressed_total + MIN_DOWNLOAD_HEADROOM, "artifact download"
    )
    work_dir.mkdir(mode=0o700)
    (work_dir / "zips").mkdir(mode=0o700)
    metadata = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "commit": EVIDENCE_COMMIT,
        "run_event": run["event"],
        "artifacts": artifacts,
    }
    marker = json.dumps(
        {"owner": "infrapulse-f3c3-validation", "run_id": RUN_ID},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    state = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    _write_private(work_dir / OWNER_MARKER, marker)
    _write_private(work_dir / "metadata.json", state)
    print(
        f"Preflight PASS: run={RUN_ID} attempt={RUN_ATTEMPT}; "
        f"artifacts={len(ARTIFACTS)}; download_bytes={compressed_total}; "
        f"transport_digests={sum(item['digest'] is not None for item in artifacts.values())}/3"
    )
    return metadata


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _download_location(token: str, artifact_id: int) -> str:
    url = f"{_api_base()}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "infrapulse-f3c3-validation",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    except (OSError, urllib.error.URLError):
        raise ValidationError("Artifact download URL request failed") from None
    try:
        if response.code not in {302, 303}:
            raise ValidationError("GitHub did not provide a download redirect")
        location = response.headers.get("Location", "")
    finally:
        response.close()
    parts = urllib.parse.urlsplit(location)
    host = (parts.hostname or "").lower()
    allowed = host.endswith((".blob.core.windows.net", ".githubusercontent.com"))
    allowed = allowed or host == "release-assets.githubusercontent.com"
    if (
        parts.scheme != "https"
        or not allowed
        or parts.username
        or parts.password
        or not parts.query
    ):
        raise ValidationError("Artifact service returned an untrusted download URL")
    return location


def _download_zip(
    spec: ArtifactSpec, item: dict[str, Any], token: str, output: Path
) -> bool:
    location = _download_location(token, item["id"])
    request = urllib.request.Request(
        location, headers={"User-Agent": "infrapulse-f3c3-validation"}
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != item["size_in_bytes"]:
                raise ValidationError(f"Artifact size header mismatch: {spec.key}")
            fd = os.open(
                output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            try:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > spec.zip_limit or total > item["size_in_bytes"]:
                        raise ValidationError(
                            f"Artifact exceeded its size limit: {spec.key}"
                        )
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        count = os.write(fd, view)
                        if count <= 0:
                            raise OSError("short artifact write")
                        view = view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
    except ValidationError:
        output.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, ValueError):
        output.unlink(missing_ok=True)
        raise ValidationError(f"Artifact download failed: {spec.key}") from None
    if total != item["size_in_bytes"]:
        output.unlink(missing_ok=True)
        raise ValidationError(f"Artifact length mismatch: {spec.key}")
    expected = item["digest"]
    if expected is None:
        return False
    if digest.hexdigest() != expected.removeprefix("sha256:"):
        output.unlink(missing_ok=True)
        raise ValidationError(f"Artifact transport digest mismatch: {spec.key}")
    return True


def download_artifacts(work_dir: Path, token: str) -> None:
    work_dir = _work_dir(work_dir, must_exist=True)
    if not token:
        raise ValidationError("GitHub Actions read token is unavailable")
    state = _json_bytes((work_dir / "metadata.json").read_bytes(), "preflight metadata")
    if (
        state.get("schema_version") != 1
        or state.get("run_id") != RUN_ID
        or state.get("commit") != EVIDENCE_COMMIT
    ):
        raise ValidationError("Preflight metadata identity mismatch")
    run, current = _source_metadata(token)
    if current != state.get("artifacts") or run.get("head_sha") != state.get("commit"):
        raise ValidationError("Source run artifacts changed after preflight")
    specs = {spec.key: spec for spec in ARTIFACTS}
    for key, item in state["artifacts"].items():
        spec = specs[key]
        output = work_dir / "zips" / f"{key}.zip"
        digest_ok = _download_zip(spec, item, token, output)
        if not digest_ok:
            print(f"Artifact transport digest unavailable: {key}")
    print("Downloaded only the three pinned artifact ZIPs; contents remain untrusted")


def _safe_extract_zip(
    archive_path: Path, destination: Path, spec: ArtifactSpec
) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValidationError(f"Artifact destination already exists: {spec.key}")
    destination.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (
                len(infos) != len(spec.members)
                or len(set(names)) != len(names)
                or set(names) != set(spec.members)
            ):
                raise ValidationError(
                    f"Artifact members are missing or unexpected: {spec.key}"
                )
            total = 0
            for item in infos:
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    item.is_dir()
                    or item.flag_bits & 0x1
                    or file_type not in {0, stat.S_IFREG}
                    or item.file_size < 0
                    or item.file_size > spec.expanded_limit
                ):
                    raise ValidationError(
                        f"Artifact member type or size is invalid: {spec.key}"
                    )
                total += item.file_size
                if total > spec.expanded_limit:
                    raise ValidationError(
                        f"Expanded artifact exceeds its limit: {spec.key}"
                    )
            require_free_space(
                archive_path.parent, total + 512 * 1024 * 1024, "artifact extraction"
            )
            directory_fd = os.open(
                destination,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                for item in infos:
                    fd = os.open(
                        item.filename,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_CLOEXEC
                        | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    try:
                        written = 0
                        with archive.open(item, "r") as source:
                            while chunk := source.read(1024 * 1024):
                                written += len(chunk)
                                if written > item.file_size:
                                    raise ValidationError(
                                        "ZIP member exceeded declared size"
                                    )
                                view = memoryview(chunk)
                                while view:
                                    count = os.write(fd, view)
                                    if count <= 0:
                                        raise OSError("short extraction write")
                                    view = view[count:]
                        if written != item.file_size:
                            raise ValidationError("ZIP member size mismatch")
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                os.close(directory_fd)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, zlib.error) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"Artifact ZIP is malformed: {spec.key}") from None


def _zip_inventory(
    archive_path: Path, spec: ArtifactSpec
) -> tuple[list[zipfile.ZipInfo], int]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (
                len(infos) != len(spec.members)
                or len(set(names)) != len(names)
                or set(names) != set(spec.members)
            ):
                raise ValidationError(
                    f"Artifact members are missing or unexpected: {spec.key}"
                )
            total = 0
            for item in infos:
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    item.is_dir()
                    or item.flag_bits & 0x1
                    or file_type not in {0, stat.S_IFREG}
                    or item.file_size < 0
                    or item.file_size > spec.expanded_limit
                    or item.compress_size < 0
                ):
                    raise ValidationError(
                        f"Artifact member type or size is invalid: {spec.key}"
                    )
                total += item.file_size
                if total > spec.expanded_limit:
                    raise ValidationError(
                        f"Expanded artifact exceeds its limit: {spec.key}"
                    )
            return infos, total
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, zlib.error) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"Artifact ZIP is malformed: {spec.key}") from None


def extract_artifacts(work_dir: Path) -> dict[str, Path]:
    work_dir = _work_dir(work_dir, must_exist=True)
    marker = _json_bytes((work_dir / OWNER_MARKER).read_bytes(), "owner marker")
    if marker != {"owner": "infrapulse-f3c3-validation", "run_id": RUN_ID}:
        raise ValidationError("Temporary workspace ownership marker mismatch")
    state = _json_bytes((work_dir / "metadata.json").read_bytes(), "preflight metadata")
    dirs: dict[str, Path] = {}
    expanded_total = 0
    archive_copy_bytes = 0
    image_bytes = 0
    for spec in ARTIFACTS:
        archive_path = work_dir / "zips" / f"{spec.key}.zip"
        if not archive_path.is_file() or archive_path.is_symlink():
            raise ValidationError(f"Artifact ZIP missing: {spec.key}")
        item = state.get("artifacts", {}).get(spec.key)
        if not isinstance(item, dict) or archive_path.stat().st_size != item.get(
            "size_in_bytes"
        ):
            raise ValidationError(f"Stored artifact length mismatch: {spec.key}")
        if item.get("digest"):
            digest = hashlib.sha256()
            with archive_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item["digest"].removeprefix("sha256:"):
                raise ValidationError(f"Stored artifact digest mismatch: {spec.key}")
        inventory = _zip_inventory(archive_path, spec)
        expanded_total += inventory[1]
        if expanded_total > MAX_HANDOFF_TOTAL + MAX_EVIDENCE_TOTAL + MAX_REPORT_TOTAL:
            raise ValidationError("Expanded artifacts exceed aggregate size limit")
        if spec.key == "handoff":
            archive_copy_bytes = sum(
                info.file_size
                for info in inventory[0]
                if info.filename.endswith(".tar")
            )
        elif spec.key == "validation":
            report_info = inventory[0][0]
            with zipfile.ZipFile(archive_path, "r") as archive:
                report_doc = _json_bytes(
                    archive.read(report_info),
                    "independent image report",
                    MAX_REPORT_TOTAL,
                )
            if not isinstance(report_doc, dict):
                raise ValidationError("Independent image report must be an object")
            api = report_doc.get("api")
            frontend = report_doc.get("frontend")
            if not isinstance(api, dict) or not isinstance(frontend, dict):
                raise ValidationError("Independent image sizes are unavailable")
            for record in (api, frontend):
                size = record.get("bytes")
                if type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES:
                    raise ValidationError("Independent image size is invalid")
                image_bytes += size
    require_free_space(
        _runner_temp(),
        expanded_total + archive_copy_bytes + 2 * GIB,
        "bounded artifact extraction and OCI canonicalization",
    )
    require_free_space(
        _docker_root(),
        2 * image_bytes + LOAD_HEADROOM,
        "disposable Docker image import",
    )
    for spec in ARTIFACTS:
        archive_path = work_dir / "zips" / f"{spec.key}.zip"
        target = work_dir / spec.key
        _safe_extract_zip(archive_path, target, spec)
        dirs[spec.key] = target
    print(
        "ZIP structure PASS: exact member allowlists, no links, duplicates, or traversal"
    )
    return dirs


def _read_json_file(path: Path, limit: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValidationError("Expected bounded regular JSON file")
    document = _json_bytes(path.read_bytes(), "artifact JSON", limit)
    if not isinstance(document, dict):
        raise ValidationError("Artifact JSON root must be an object")
    return document


def _validate_image_report(
    report_path: Path, handoff: dict[str, Any]
) -> dict[str, str]:
    report = _read_json_file(report_path, MAX_REPORT_TOTAL)
    if set(report) != {"contract_sha256", "types", "api", "frontend", "smoke"}:
        raise ValidationError("Independent Docker image report schema mismatch")
    if (
        not SHA256_RE.fullmatch(str(report.get("contract_sha256", "")))
        or report.get("smoke") != "PASS"
        or not isinstance(report.get("types"), dict)
    ):
        raise ValidationError("Independent Docker validation report is incomplete")
    image_map = {"backend-image": "api", "nginx-image": "frontend"}
    ids: dict[str, str] = {}
    handoff_images = handoff.get("images")
    if not isinstance(handoff_images, dict) or set(handoff_images) != set(image_map):
        raise ValidationError("Handoff image map is incomplete")
    for name, report_name in image_map.items():
        record = report.get(report_name)
        handoff_record = handoff_images.get(name)
        if not isinstance(record, dict) or not isinstance(handoff_record, dict):
            raise ValidationError("Independent image identity record is missing")
        image_id = record.get("id")
        if (
            set(record)
            != {
                "id",
                "os",
                "architecture",
                "user",
                "entrypoint",
                "cmd",
                "ports",
                "healthcheck",
                "environment_keys",
                "layers",
                "rootfs",
                "bytes",
                "permissions",
                "application_build_residue_files",
            }
            or not isinstance(image_id, str)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or record.get("os") != "linux"
            or record.get("architecture") != "amd64"
            or type(record.get("bytes")) is not int
            or not 0 < record["bytes"] <= MAX_IMAGE_BYTES
            or handoff_record.get("image_id") != image_id
        ):
            raise ValidationError(f"Independent image identity mismatch: {name}")
        ids[name] = image_id
    return ids


def _safe_subprocess(
    arguments: list[str], *, cwd: Path | None = None, timeout: int = 120
) -> bytes:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=timeout,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"GITHUB_TOKEN", "GH_TOKEN"}
            },
        )
    except (OSError, subprocess.SubprocessError):
        raise ValidationError(
            f"Required local validation command failed: {Path(arguments[0]).name}"
        ) from None
    return result.stdout


def _load_modules(project_root: Path) -> tuple[Any, Any, Any, Any]:
    sys.path.insert(0, str(project_root / "scripts"))
    loaded = []
    for name in (
        "ci_evidence_signature",
        "provenance_signature",
        "oci_canonicalizer",
        "sbom",
    ):
        try:
            loaded.append(importlib.import_module(name))
        except ModuleNotFoundError as exc:
            reason = "module missing" if exc.name == name else "dependency missing"
            raise ValidationError(f"Unable to load {name}: {reason}") from None
        except ImportError:
            raise ValidationError(
                f"Unable to load {name}: internal import error"
            ) from None
    evidence, provenance_signature, canonicalizer, sbom = loaded
    return evidence, provenance_signature, canonicalizer, sbom


def _docker_root() -> Path:
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or os.environ.get(
        "DOCKER_HOST"
    ):
        raise ValidationError(
            "Refusing to use a non-GitHub-hosted or overridden Docker daemon"
        )
    raw = (
        _safe_subprocess(
            ["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=30
        )
        .decode("utf-8", errors="strict")
        .strip()
    )
    root = Path(raw)
    if not root.is_absolute() or not root.is_dir():
        raise ValidationError("Disposable Docker daemon root is unavailable")
    return root


def _docker_load_and_inspect(
    canonical: Path,
    image_id: str,
    expected_size: int,
    docker_root: Path,
    runner_temp: Path,
) -> None:
    required = expected_size * 2 + LOAD_HEADROOM
    require_free_space(docker_root, required, "Docker image load")
    require_free_space(runner_temp, required, "Docker image load workspace")
    _safe_subprocess(["docker", "load", "--input", str(canonical)], timeout=600)
    output = _safe_subprocess(
        ["docker", "image", "inspect", image_id, "--format", "{{json .}}"], timeout=60
    )
    inspected = _json_bytes(output, "Docker image inspection", 1024 * 1024)
    if (
        inspected.get("Id") != image_id
        or inspected.get("Os") != "linux"
        or inspected.get("Architecture") != "amd64"
        or inspected.get("Size") != expected_size
    ):
        raise ValidationError("Loaded image identity differs from independent report")


def _generate_test_key(
    cosign: Path, directory: Path
) -> tuple[Path, Path, Path, str, str]:
    private_key = directory / "test.key"
    public_key = directory / "test.pub"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GITHUB_TOKEN", "GH_TOKEN"}
    }
    env["COSIGN_PASSWORD"] = ""
    try:
        subprocess.run(
            [
                str(cosign),
                "generate-key-pair",
                "--output-key-prefix",
                str(directory / "test"),
            ],
            cwd=directory,
            env=env,
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValidationError("Disposable Cosign key generation failed") from None
    for key in (private_key, public_key):
        if key.is_symlink() or not key.is_file():
            raise ValidationError("Cosign did not produce regular test key files")
        key.chmod(0o600)
    pem = public_key.read_bytes()
    try:
        public_text = pem.decode("ascii")
    except UnicodeDecodeError:
        raise ValidationError("Disposable public key encoding is invalid") from None
    fingerprint = hashlib.sha256(pem).hexdigest()
    key_id = f"sha256:{fingerprint}"
    policy = directory / "trust-policy.json"
    policy_data = json.dumps(
        {
            "schema_version": 1,
            "keys": {
                key_id: {
                    "public_key": public_text,
                    "sha256": fingerprint,
                    "status": "ACTIVE",
                }
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    _write_private(policy, policy_data)
    policy_sha256 = hashlib.sha256(policy_data).hexdigest()
    return private_key, public_key, policy, policy_sha256, key_id


def _negative_crypto_checks(
    signature_module: Any,
    cosign: Path,
    policy: Path,
    policy_sha256: str,
    key_id: str,
    test_payload: bytes,
    bundle: bytes,
    temp_root: Path,
) -> None:
    try:
        signature_module.verify_blob_bytes(
            test_payload + b"tampered",
            bundle,
            policy,
            policy_sha256,
            key_id,
            cosign,
        )
    except (ValueError, RuntimeError):
        pass
    else:
        raise ValidationError("Tampered signed bytes were unexpectedly accepted")

    try:
        signature_module.verify_blob_bytes(
            test_payload,
            b'{"tampered":true}',
            policy,
            policy_sha256,
            key_id,
            cosign,
        )
    except (ValueError, RuntimeError):
        pass
    else:
        raise ValidationError("Tampered signature bundle was unexpectedly accepted")

    unauthorized = temp_root / "unauthorized"
    unauthorized.mkdir(mode=0o700)
    wrong_private, _, wrong_policy, wrong_policy_sha, wrong_key_id = _generate_test_key(
        cosign, unauthorized
    )
    unauthorized_bundle_path = unauthorized / "unauthorized.sigstore.json"
    signature_module.sign_blob_bytes(
        test_payload, unauthorized_bundle_path, wrong_private, cosign
    )
    if unauthorized_bundle_path.is_symlink() or not unauthorized_bundle_path.is_file():
        raise ValidationError("Unauthorized test signature bundle is missing")
    unauthorized_bundle = unauthorized_bundle_path.read_bytes()
    own_key_result = signature_module.verify_blob_bytes(
        test_payload,
        unauthorized_bundle,
        wrong_policy,
        wrong_policy_sha,
        wrong_key_id,
        cosign,
    )
    if (
        own_key_result.get("signature") != "SIGNATURE_VALID"
        or own_key_result.get("signer") != "TRUSTED_SIGNER"
    ):
        raise ValidationError(
            "Unauthorized test signature is not valid under its own test key"
        )
    try:
        signature_module.verify_blob_bytes(
            test_payload,
            unauthorized_bundle,
            policy,
            policy_sha256,
            wrong_key_id,
            cosign,
        )
    except ValueError as exc:
        if str(exc) != "Signer is not authorized by trust policy":
            raise ValidationError(
                "Unauthorized signer was rejected for an unexpected reason"
            ) from None
    else:
        raise ValidationError("Unauthorized test signer was unexpectedly accepted")
    try:
        signature_module.verify_blob_bytes(
            test_payload,
            bundle,
            policy,
            policy_sha256,
            "sha256:" + "0" * 64,
            cosign,
        )
    except (ValueError, RuntimeError):
        pass
    else:
        raise ValidationError("Divergent trust fingerprint was unexpectedly accepted")


def execute(work_dir: Path, project_root: Path, trivy: Path, cosign: Path) -> None:
    with _phase("checkout"):
        work_dir = _work_dir(work_dir, must_exist=True)
        project_root = project_root.resolve(strict=True)
        if not (project_root / ".git").exists():
            raise ValidationError("F3C.2 source checkout is unavailable")
        head = (
            _safe_subprocess(["git", "-C", str(project_root), "rev-parse", "HEAD"])
            .decode()
            .strip()
        )
        if head != EVIDENCE_COMMIT:
            raise ValidationError("F3C.2 validation checkout commit mismatch")
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise ValidationError(
                "Integrated validation is restricted to GitHub-hosted Actions"
            )

    with _phase("tools"):
        _validate_trivy_binary(trivy)
        evidence, signature, _, sbom = _load_modules(project_root)
        signature.verify_cosign(cosign)
        sbom.verify_tool(trivy)
    with _phase("extract"):
        dirs = extract_artifacts(work_dir)
        evidence_dir = dirs["evidence"]
        handoff_dir = dirs["handoff"]
        validation_dir = dirs["validation"]

    with _phase("manifests"):
        image_report = _read_json_file(validation_dir / "report.json", MAX_REPORT_TOTAL)
        handoff_fd = os.open(
            handoff_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        try:
            handoff = evidence._read_handoff_manifest(handoff_fd)[0]
        finally:
            os.close(handoff_fd)
        image_ids = _validate_image_report(validation_dir / "report.json", handoff)

        data, manifest = evidence._load_and_validate(
            evidence_dir,
            EVIDENCE_COMMIT,
            RUN_ID,
            RUN_ATTEMPT,
            REPOSITORY,
            image_ids,
        )
    with _phase("canonicalize"):
        canonical_dir = work_dir / "canonical"
        canonical_dir.mkdir(mode=0o700)
        prior_run_id = os.environ.get("GITHUB_RUN_ID")
        prior_run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
        os.environ["GITHUB_RUN_ID"] = RUN_ID
        os.environ["GITHUB_RUN_ATTEMPT"] = str(RUN_ATTEMPT)
        try:
            receipt = evidence._validate_handoff_archives(
                handoff_dir,
                manifest,
                EVIDENCE_COMMIT,
                RUN_ID,
                RUN_ATTEMPT,
                image_ids,
                evidence_manifest_bytes=data["evidence-manifest.json"],
                canonical_output_dir=canonical_dir,
            )
        finally:
            if prior_run_id is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = prior_run_id
            if prior_run_attempt is None:
                os.environ.pop("GITHUB_RUN_ATTEMPT", None)
            else:
                os.environ["GITHUB_RUN_ATTEMPT"] = prior_run_attempt
        if not isinstance(receipt, bytes) or not receipt:
            raise ValidationError("Canonical archive receipt was not produced")

    with _phase("docker_import"):
        image_sizes = {
            "backend-image": image_report["api"]["bytes"],
            "nginx-image": image_report["frontend"]["bytes"],
        }
        runner_temp = _runner_temp()
        docker_root = _docker_root()
        require_free_space(
            docker_root,
            2 * sum(image_sizes.values()) + LOAD_HEADROOM,
            "Docker image import",
        )
        for name, image_id in sorted(image_ids.items()):
            canonical = canonical_dir / f"{name}.canonical.tar"
            if canonical.is_symlink() or not canonical.is_file():
                raise ValidationError("Canonical archive output is missing")
            _docker_load_and_inspect(
                canonical,
                image_id,
                image_sizes[name],
                docker_root,
                runner_temp,
            )

    with _phase("sign_verify"):
        crypto_dir = work_dir / "crypto"
        crypto_dir.mkdir(mode=0o700)
        with _subphase("disposable_keys"):
            private_key, _, policy, policy_sha, key_id = _generate_test_key(
                cosign, crypto_dir
            )
        signatures_dir = work_dir / "signatures"
        prior_run_id = os.environ.get("GITHUB_RUN_ID")
        prior_run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
        os.environ["GITHUB_RUN_ID"] = RUN_ID
        os.environ["GITHUB_RUN_ATTEMPT"] = str(RUN_ATTEMPT)
        try:
            with _subphase("sign_evidence"), _sign_diagnostics(evidence, signature):
                result = evidence.sign_evidence(
                    evidence_dir,
                    handoff_dir,
                    signatures_dir,
                    private_key,
                    cosign,
                    trivy,
                    policy,
                    policy_sha,
                    key_id,
                    EVIDENCE_COMMIT,
                    RUN_ID,
                    RUN_ATTEMPT,
                    REPOSITORY,
                    image_ids,
                    canonicalize_oci=True,
                )
            if (
                result.get("integrity") != "INTEGRITY_VERIFIED"
                or result.get("signature") != "SIGNATURE_VALID"
                or result.get("signer") != "TRUSTED_SIGNER"
            ):
                raise ValidationError(
                    "Signing round-trip did not return all required states"
                )
            with _subphase("verify_evidence"), _sign_diagnostics(evidence, signature):
                verified = evidence.verify_evidence(
                    evidence_dir,
                    handoff_dir,
                    signatures_dir,
                    cosign,
                    trivy,
                    policy,
                    policy_sha,
                    key_id,
                    EVIDENCE_COMMIT,
                    RUN_ID,
                    RUN_ATTEMPT,
                    REPOSITORY,
                    image_ids,
                    canonicalize_oci=True,
                )
        finally:
            if prior_run_id is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = prior_run_id
            if prior_run_attempt is None:
                os.environ.pop("GITHUB_RUN_ATTEMPT", None)
            else:
                os.environ["GITHUB_RUN_ATTEMPT"] = prior_run_attempt
        if (
            verified.get("integrity") != "INTEGRITY_VERIFIED"
            or verified.get("signature") != "SIGNATURE_VALID"
            or verified.get("signer") != "TRUSTED_SIGNER"
        ):
            raise ValidationError(
                "Offline verification did not return all required states"
            )

    with _phase("negative_checks"):
        bundles = evidence._read_exact_directory(
            signatures_dir, evidence._bundle_names(canonicalize_oci=True)
        )
        manifest_bytes = data["evidence-manifest.json"]
        _negative_crypto_checks(
            signature,
            cosign,
            policy,
            policy_sha,
            key_id,
            manifest_bytes,
            bundles["evidence-manifest.sigstore.json"],
            crypto_dir,
        )
        tampered_evidence = work_dir / "tampered-evidence"
        shutil.copytree(evidence_dir, tampered_evidence, symlinks=False)
        sbom_path = tampered_evidence / "python.cdx.json"
        with sbom_path.open("r+b") as stream:
            first = stream.read(1)
            if not first:
                raise ValidationError("Python SBOM fixture is empty")
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        try:
            evidence.verify_evidence(
                tampered_evidence,
                handoff_dir,
                signatures_dir,
                cosign,
                trivy,
                policy,
                policy_sha,
                key_id,
                EVIDENCE_COMMIT,
                RUN_ID,
                RUN_ATTEMPT,
                REPOSITORY,
                image_ids,
                canonicalize_oci=True,
            )
        except (ValueError, RuntimeError):
            pass
        else:
            raise ValidationError("Tampered SBOM package was unexpectedly accepted")

        print(
            "F3B.1 and Cosign round-trip PASS: "
            "INTEGRITY_VERIFIED, SIGNATURE_VALID, TRUSTED_SIGNER; "
            "coverage=image COVERAGE_PARTIAL; builder authenticity/SLSA not asserted"
        )
        print(
            "Negative checks PASS: payload tampering, bundle tampering, "
            "unauthorized key, fingerprint mismatch, SBOM tampering"
        )


def install_cosign(project_root: Path, output: Path) -> None:
    project_root = project_root.resolve(strict=True)
    lock = _read_json_file(project_root / "security/cosign-tools.json", 64 * 1024)
    version = lock.get("version")
    digest = lock.get("sha256")
    expected_url = f"https://github.com/sigstore/cosign/releases/download/v{version}/cosign-linux-amd64"
    if (
        lock.get("schema_version") != 1
        or lock.get("name") != "cosign"
        or lock.get("platform") != "linux-amd64"
        or version != "3.1.3"
        or lock.get("release_url") != expected_url
        or not isinstance(digest, str)
        or not SHA256_RE.fullmatch(digest)
    ):
        raise ValidationError("Cosign tool lock is unsupported")
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValidationError("Cosign destination already exists")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    request = urllib.request.Request(
        expected_url,
        headers={"User-Agent": "infrapulse-f3c3-validation"},
    )
    temp = output.with_name(f".{output.name}.download")
    h = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            try:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > COSIGN_V313_LINUX_AMD64_BYTES:
                        raise ValidationError("Cosign binary exceeded its size limit")
                    h.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("short Cosign write")
                        view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
    except (OSError, urllib.error.URLError):
        temp.unlink(missing_ok=True)
        raise ValidationError("Pinned Cosign download failed") from None
    if size != COSIGN_V313_LINUX_AMD64_BYTES:
        temp.unlink(missing_ok=True)
        raise ValidationError("Cosign binary size mismatch")
    if h.hexdigest() != digest:
        temp.unlink(missing_ok=True)
        raise ValidationError("Pinned Cosign SHA-256 mismatch")
    os.chmod(temp, 0o700)
    os.replace(temp, output)
    _, signature, _, _ = _load_modules(project_root)
    signature.verify_cosign(output)
    print("Cosign v3.1.3 installed and verified against the checked-in SHA-256 pin")


def cleanup(work_dir: Path) -> None:
    work_dir = _work_dir(work_dir, must_exist=True)
    marker = _json_bytes((work_dir / OWNER_MARKER).read_bytes(), "owner marker")
    if marker != {"owner": "infrapulse-f3c3-validation", "run_id": RUN_ID}:
        raise ValidationError("Refusing to remove an unowned temporary directory")
    shutil.rmtree(work_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "download", "execute", "cleanup"):
        item = sub.add_parser(command)
        if command in {"preflight", "download", "execute", "cleanup"}:
            item.add_argument("--work-dir", type=Path, required=True)
        if command in {"preflight", "download"}:
            item.add_argument("--token-env", default="GITHUB_TOKEN")
        if command == "execute":
            item.add_argument("--project-root", type=Path, required=True)
    install = sub.add_parser("install-cosign")
    install.add_argument("--project-root", type=Path, required=True)
    install.add_argument("--output", type=Path, required=True)
    install_trivy = sub.add_parser("install-trivy")
    install_trivy.add_argument("--project-root", type=Path, required=True)
    install_trivy.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            preflight(args.work_dir, os.environ.get(args.token_env, ""))
        elif args.command == "download":
            download_artifacts(args.work_dir, os.environ.get(args.token_env, ""))
        elif args.command == "execute":
            execute(
                args.work_dir,
                args.project_root,
                Path(os.environ["TRIVY_BINARY"]),
                Path(os.environ["COSIGN_BINARY"]),
            )
        elif args.command == "cleanup":
            cleanup(args.work_dir)
        elif args.command == "install-cosign":
            install_cosign(args.project_root, args.output)
        elif args.command == "install-trivy":
            destination = _trivy_destination(args.output_dir, create=True)
            result = _safe_subprocess(
                [
                    sys.executable,
                    str(args.project_root / "scripts/vulnerability_ci.py"),
                    "--install-trivy",
                    str(destination),
                ],
                cwd=args.project_root,
                timeout=300,
            )
            del result
            print("Pinned Trivy installation completed through the existing installer")
    except (ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        message = (
            str(exc) if isinstance(exc, ValidationError) else "F3C.3.2 operation failed"
        )
        print(f"BLOCKED: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
