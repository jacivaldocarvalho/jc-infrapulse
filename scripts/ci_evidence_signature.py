"""Sign and verify a downloaded F3C.2 evidence package offline."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import errno
import gzip
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tarfile
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import provenance
import provenance_signature as signature
import sbom
import vulnerability_ci

COMPONENTS = {
    "python": ("python", "COVERAGE_PASS"),
    "npm": ("npm", "COVERAGE_PASS"),
    "backend-image": ("image", "COVERAGE_PARTIAL"),
    "nginx-image": ("image", "COVERAGE_PARTIAL"),
}
F3C2_LIMITATIONS = [
    "Image inventory is detected-only and remains COVERAGE_PARTIAL.",
    "Only backend-image and nginx-image archives from docker-build are covered; other Compose images are not analyzed.",
    "Statements are unsigned; builder identity, authenticity, and SLSA level are not asserted.",
    "Inventory equality does not establish scanner completeness or reproducible builds.",
]
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_HANDOFF_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_IMAGE_CONFIG_BYTES = 16 * 1024 * 1024
TAR_BLOCK_BYTES = 512
OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
OCI_GZIP_LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
OCI_TAR_LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar"
OCI_EMPTY_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"
DOCKER_ATTESTATION_MEDIA_TYPE = "application/vnd.docker.attestation.manifest.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
MANIFEST_KEYS = {
    "schema_version",
    "repository",
    "workflow",
    "workflow_run_id",
    "run_attempt",
    "commit",
    "generated_at",
    "signature_status",
    "authenticity",
    "builder_identity",
    "slsa_level",
    "tools",
    "overall_coverage",
    "components",
    "files",
    "duration_seconds",
    "bytes",
    "limitations",
}
OCI_TRANSFORM_RECEIPT = "image-archive-transformation.json"
OCI_TRANSFORM_SIGNATURE = "image-archive-transformation.sigstore.json"
OCI_TRANSFORM_ID = "oci-canonicalize-recognized-legacy-layer-metadata-v1"


def _bundle_names(canonicalize_oci: bool = False) -> set[str]:
    names = {"evidence-manifest.sigstore.json"} | {
        f"{name}.provenance.sigstore.json" for name in COMPONENTS
    }
    if canonicalize_oci:
        names |= {OCI_TRANSFORM_RECEIPT, OCI_TRANSFORM_SIGNATURE}
    return names


def _assert_trusted_directory(directory_fd: int) -> None:
    metadata = os.fstat(directory_fd)
    writable_by_others = bool(metadata.st_mode & 0o022)
    sticky = bool(metadata.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or (writable_by_others and not sticky)
    ):
        raise ValueError("Signature output path has an untrusted directory")


def _directory_fd(path: Path, require_trusted: bool = False) -> int:
    if not path.is_absolute():
        path = Path.cwd() / path
    if ".." in path.parts:
        raise ValueError("Directory path must be normalized")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        if require_trusted:
            _assert_trusted_directory(fd)
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            if require_trusted:
                try:
                    _assert_trusted_directory(next_fd)
                except BaseException:
                    os.close(next_fd)
                    raise
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_exact_directory(path: Path, expected: set[str]) -> dict[str, bytes]:
    directory_fd = _directory_fd(path)
    try:
        names = set(os.listdir(directory_fd))
        if names != expected:
            raise ValueError("Directory has missing or unexpected files")
        contents: dict[str, bytes] = {}
        total = 0
        for name in sorted(expected):
            fd = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError("Input is not an acceptable regular file")
                chunks = bytearray()
                while len(chunks) <= MAX_FILE_BYTES:
                    block = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(chunks)))
                    if not block:
                        break
                    chunks.extend(block)
                after = os.fstat(fd)
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                identity_before = (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                identity_after = (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                if (
                    len(chunks) > MAX_FILE_BYTES
                    or identity_before != identity_after
                    or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
                ):
                    raise ValueError("Input changed while being read")
                total += len(chunks)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Evidence package exceeds total size limit")
                contents[name] = bytes(chunks)
            finally:
                os.close(fd)
        return contents
    finally:
        os.close(directory_fd)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=signature._pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed JSON: {label}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {label}")
    return value


def _expected_evidence_files() -> set[str]:
    files = {"evidence-manifest.json", "evidence-manifest.sha256"}
    for name in COMPONENTS:
        files.update(
            {
                f"{name}.cdx.json",
                f"{name}.manifest.json",
                f"{name}.provenance.json",
            }
        )
    return files


def _safe_tar_name(name: str) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not name.startswith("/")
        and "\\" not in name
        and all(part not in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    )


def _read_tar_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int
) -> bytes:
    if not member.isfile() or member.size < 0 or member.size > limit:
        raise ValueError("Docker archive member is not a bounded regular file")
    source = archive.extractfile(member)
    if source is None:
        raise ValueError("Docker archive member cannot be read")
    with source:
        data = source.read(limit + 1)
    if len(data) != member.size:
        raise ValueError("Docker archive member size mismatch")
    return data


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _tar_text(field: bytes, label: str) -> str:
    value, separator, padding = field.partition(b"\0")
    if separator and any(padding):
        raise ValueError(f"Malformed Docker TAR {label}")
    try:
        return value.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Unsupported Docker TAR {label} encoding") from exc


def _tar_octal(field: bytes, label: str) -> int:
    value = field.strip(b" \0")
    if not value:
        return 0
    if any(byte < ord("0") or byte > ord("7") for byte in value):
        raise ValueError(f"Malformed Docker TAR {label}")
    return int(value, 8)


def _prevalidate_docker_tar(fd: int) -> list[tuple[str, int, str]]:
    """Validate bounded classic USTAR headers before tarfile interprets members."""
    metadata = os.fstat(fd)
    size = metadata.st_size
    if (
        not stat.S_ISREG(metadata.st_mode)
        or size < TAR_BLOCK_BYTES * 2
        or size > MAX_ARCHIVE_BYTES
        or size % TAR_BLOCK_BYTES
    ):
        raise ValueError("Docker archive size or TAR block alignment is invalid")

    members: list[tuple[str, int, str]] = []
    paths: dict[str, str] = {}
    seen_parent_paths: set[str] = set()
    offset = 0
    expanded_bytes = 0
    zero_blocks = 0
    while offset + TAR_BLOCK_BYTES <= size:
        header = os.pread(fd, TAR_BLOCK_BYTES, offset)
        if len(header) != TAR_BLOCK_BYTES:
            raise ValueError("Truncated Docker TAR header")
        if not any(header):
            zero_blocks += 1
            offset += TAR_BLOCK_BYTES
            if zero_blocks == 2:
                break
            continue
        if zero_blocks:
            raise ValueError("Docker TAR has data after its end marker")
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            raise ValueError("Docker archive member count is invalid")
        if header[257:263] != b"ustar\0" or header[263:265] != b"00":
            raise ValueError("Only classic USTAR Docker archives are supported")
        typeflag = header[156:157]
        if typeflag not in (b"\0", b"0", b"5"):
            raise ValueError("Docker TAR extension or special entry is unsupported")
        stored_checksum = _tar_octal(header[148:156], "checksum")
        actual_checksum = sum(header[:148]) + (32 * 8) + sum(header[156:])
        if stored_checksum != actual_checksum:
            raise ValueError("Docker TAR header checksum mismatch")
        for field, label in (
            (header[100:108], "mode"),
            (header[108:116], "owner"),
            (header[116:124], "group"),
            (header[136:148], "mtime"),
            (header[329:337], "device major"),
            (header[337:345], "device minor"),
        ):
            _tar_octal(field, label)
        name = _tar_text(header[:100], "name")
        linkname = _tar_text(header[157:257], "link name")
        _tar_text(header[265:297], "user name")
        _tar_text(header[297:329], "group name")
        prefix = _tar_text(header[345:500], "prefix")
        if linkname or any(header[500:512]):
            raise ValueError("Unsupported Docker TAR header fields")
        raw_name = f"{prefix}/{name}" if prefix else name
        size_field = _tar_octal(header[124:136], "member size")
        if size_field > MAX_ARCHIVE_BYTES:
            raise ValueError("Docker TAR member exceeds size limit")
        kind = "directory" if typeflag == b"5" else "file"
        if (kind == "directory" and size_field != 0) or not _safe_tar_name(raw_name):
            raise ValueError("Invalid Docker TAR path or directory size")
        if (kind == "directory") != raw_name.endswith("/"):
            raise ValueError("Docker TAR path does not match its entry type")
        canonical_name = raw_name[:-1] if kind == "directory" else raw_name
        if canonical_name in paths:
            raise ValueError("Duplicate Docker TAR path")
        parents = {
            parent.as_posix()
            for parent in Path(canonical_name).parents
            if parent.as_posix() != "."
        }
        for parent_name in parents:
            if paths.get(parent_name) == "file":
                raise ValueError("Docker TAR file is used as a directory")
        if kind == "file" and canonical_name in seen_parent_paths:
            raise ValueError("Docker TAR directory is replaced by a file")
        paths[canonical_name] = kind
        seen_parent_paths.update(parents)
        expanded_bytes += size_field
        if expanded_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError("Docker archive expanded size exceeds limit")
        padded_size = (
            (size_field + TAR_BLOCK_BYTES - 1) // TAR_BLOCK_BYTES
        ) * TAR_BLOCK_BYTES
        next_offset = offset + TAR_BLOCK_BYTES + padded_size
        if next_offset > size:
            raise ValueError("Truncated Docker TAR member data")
        members.append((raw_name, size_field, kind))
        offset = next_offset

    if zero_blocks != 2:
        raise ValueError("Docker TAR end marker is missing or truncated")
    while offset < size:
        trailing = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not trailing or any(trailing):
            raise ValueError("Docker TAR has nonzero data after its end marker")
        offset += len(trailing)
    if not members:
        raise ValueError("Docker archive member count is invalid")
    return members


def _oci_object(data: bytes, label: str) -> dict[str, Any]:
    value = _json_object(data, label)
    return value


def _oci_descriptor(
    value: Any,
    label: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, int]:
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required - optional
        or not isinstance(value.get("digest"), str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", value["digest"])
        or type(value.get("size")) is not int
        or value["size"] < 0
        or not isinstance(value.get("mediaType"), str)
    ):
        raise ValueError(f"Invalid or unsupported OCI descriptor: {label}")
    return value["digest"], value["size"]


def _read_oci_blob(
    archive: tarfile.TarFile,
    member_map: dict[str, tarfile.TarInfo],
    descriptor: dict[str, Any],
    label: str,
    referenced: set[str],
    limit: int,
) -> tuple[bytes, str]:
    if not isinstance(descriptor, dict):
        raise TypeError(f"Invalid OCI blob descriptor: {label}")
    digest_fields = {
        key: descriptor[key]
        for key in ("mediaType", "digest", "size")
        if key in descriptor
    }
    digest, size = _oci_descriptor(
        digest_fields, label, {"mediaType", "digest", "size"}
    )
    if digest in referenced:
        raise ValueError(f"Duplicate OCI blob descriptor: {label}")
    referenced.add(digest)
    if size > limit:
        raise ValueError(f"OCI blob exceeds size limit: {label}")
    path = f"blobs/sha256/{digest.split(':', 1)[1]}"
    member = member_map.get(path)
    if member is None or not member.isfile() or member.size != size:
        raise ValueError(f"OCI blob path or size mismatch: {label}")
    data = _read_tar_member(archive, member, limit)
    if hashlib.sha256(data).hexdigest() != digest.split(":", 1)[1]:
        raise ValueError(f"OCI blob digest mismatch: {label}")
    return data, path


def _hash_oci_layer(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    digest: str,
    expected_diff_id: str,
    expanded_total: int,
) -> int:
    compressed = archive.extractfile(member)
    if compressed is None:
        raise ValueError("OCI layer blob cannot be read")
    compressed_hash = hashlib.sha256()
    with compressed:
        while chunk := compressed.read(1024 * 1024):
            compressed_hash.update(chunk)
    if compressed_hash.hexdigest() != digest.split(":", 1)[1]:
        raise ValueError("OCI layer descriptor digest mismatch")

    source = archive.extractfile(member)
    if source is None:
        raise ValueError("OCI layer blob cannot be reopened")
    diff_hash = hashlib.sha256()
    try:
        with source, gzip.GzipFile(fileobj=source, mode="rb") as decompressed:
            while chunk := decompressed.read(1024 * 1024):
                expanded_total += len(chunk)
                if expanded_total > MAX_ARCHIVE_BYTES:
                    raise ValueError("OCI expanded layers exceed size limit")
                diff_hash.update(chunk)
    except (EOFError, OSError) as exc:
        raise ValueError("Malformed or truncated gzip OCI layer") from exc
    if "sha256:" + diff_hash.hexdigest() != expected_diff_id:
        raise ValueError("OCI layer DiffID mismatch")
    return expanded_total


def _hash_oci_uncompressed_layer(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    digest: str,
    expected_size: int,
    expected_diff_id: str,
    expanded_total: int,
) -> int:
    source = archive.extractfile(member)
    if source is None:
        raise ValueError("OCI layer blob cannot be read")
    layer_hash = hashlib.sha256()
    layer_size = 0
    with source:
        while chunk := source.read(1024 * 1024):
            layer_hash.update(chunk)
            layer_size += len(chunk)
    actual_digest = "sha256:" + layer_hash.hexdigest()
    if (
        layer_size != expected_size
        or actual_digest != digest
        or actual_digest != expected_diff_id
    ):
        raise ValueError("OCI uncompressed layer digest or DiffID mismatch")
    expanded_total += layer_size
    if expanded_total > MAX_ARCHIVE_BYTES:
        raise ValueError("OCI expanded layers exceed size limit")
    return expanded_total


def _validate_legacy_docker_auxiliary_blobs(
    archive: tarfile.TarFile,
    member_map: dict[str, tarfile.TarInfo],
    paths: set[str],
    expected_layers: int,
) -> None:
    """Recognize only content-addressed Docker layer metadata outside OCI refs."""
    if not paths:
        return
    if len(paths) != expected_layers:
        raise ValueError("Unexpected number of legacy Docker auxiliary blobs")
    allowed = {
        "id",
        "os",
        "created",
        "container_config",
        "parent",
        "architecture",
        "config",
    }
    identities: dict[str, dict[str, Any]] = {}
    for path in paths:
        member = member_map.get(path)
        if (
            not path.startswith("blobs/sha256/")
            or not re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", path)
            or member is None
            or not member.isfile()
            or member.size <= 0
            or member.size > 4096
        ):
            raise ValueError("Unrecognized auxiliary archive entry")
        raw = _read_tar_member(archive, member, 4096)
        if hashlib.sha256(raw).hexdigest() != Path(path).name:
            raise ValueError("Legacy Docker auxiliary blob digest mismatch")
        try:
            metadata = json.loads(raw, object_pairs_hook=signature._pairs_no_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Legacy Docker auxiliary blob is not valid JSON") from exc
        if (
            not isinstance(metadata, dict)
            or not {"id", "os", "created", "container_config"} <= set(metadata)
            or set(metadata) - allowed
            or not isinstance(metadata.get("id"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", metadata["id"])
            or metadata.get("os") != "linux"
            or not isinstance(metadata.get("created"), str)
            or not metadata["created"]
            or len(metadata["created"]) > 128
            or not isinstance(metadata.get("container_config"), dict)
            or (
                "parent" in metadata
                and (
                    not isinstance(metadata["parent"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", metadata["parent"])
                )
            )
            or (
                "architecture" in metadata
                and (
                    not isinstance(metadata["architecture"], str)
                    or not metadata["architecture"]
                    or len(metadata["architecture"]) > 64
                )
            )
            or ("config" in metadata and not isinstance(metadata["config"], dict))
            or metadata["id"] in identities
        ):
            raise ValueError("Unrecognized legacy Docker layer metadata")
        identities[metadata["id"]] = metadata

    roots = [item for item in identities.values() if "parent" not in item]
    if len(roots) != 1 or any(
        item.get("parent") not in identities
        for item in identities.values()
        if "parent" in item
    ):
        raise ValueError("Legacy Docker layer metadata chain is incomplete")
    for identity in identities:
        visited: set[str] = set()
        current = identity
        while "parent" in identities[current]:
            if current in visited:
                raise ValueError("Legacy Docker layer metadata chain has a cycle")
            visited.add(current)
            current = identities[current]["parent"]


def _verify_oci_direct_manifest_archive(
    archive: tarfile.TarFile,
    member_map: dict[str, tarfile.TarInfo],
    root_descriptor: dict[str, Any],
    expected_image_id: str,
    allow_legacy_auxiliary_blobs: bool = False,
) -> set[str]:
    digest, _ = _oci_descriptor(
        root_descriptor,
        "direct root image manifest",
        {"mediaType", "digest", "size"},
        {"annotations"},
    )
    annotations = root_descriptor.get("annotations")
    if (
        not isinstance(annotations, dict)
        or set(annotations)
        != {"io.containerd.image.name", "org.opencontainers.image.ref.name"}
        or any(
            not isinstance(value, str) or not value or len(value) > 512
            for value in annotations.values()
        )
    ):
        raise ValueError("Unsupported direct OCI root annotations")

    manifest_raw, manifest_path = _read_oci_blob(
        archive,
        member_map,
        root_descriptor,
        "direct root image manifest",
        set(),
        MAX_IMAGE_CONFIG_BYTES,
    )
    image_manifest = _oci_object(manifest_raw, "direct OCI image manifest")
    if (
        set(image_manifest) != {"schemaVersion", "mediaType", "config", "layers"}
        or type(image_manifest.get("schemaVersion")) is not int
        or image_manifest["schemaVersion"] != 2
        or image_manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE
        or not isinstance(image_manifest.get("layers"), list)
    ):
        raise ValueError("Unsupported direct OCI image manifest")

    config_descriptor = image_manifest.get("config")
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor.get("mediaType") != OCI_CONFIG_MEDIA_TYPE
    ):
        raise ValueError("Unsupported direct OCI image config descriptor")
    config_digest, _ = _oci_descriptor(
        config_descriptor, "direct image config", {"mediaType", "digest", "size"}
    )
    if config_digest != expected_image_id:
        raise ValueError("OCI config digest differs from expected image ID")

    referenced = {digest}
    config_raw, config_path = _read_oci_blob(
        archive,
        member_map,
        config_descriptor,
        "direct image config",
        referenced,
        MAX_IMAGE_CONFIG_BYTES,
    )
    config = _oci_object(config_raw, "direct OCI image config")
    rootfs = config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    layers = image_manifest["layers"]
    if (
        config.get("os") != "linux"
        or config.get("architecture") != "amd64"
        or not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(diff_ids, list)
        or not isinstance(layers, list)
        or len(diff_ids) != len(layers)
        or not layers
        or any(
            not isinstance(diff_id, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", diff_id)
            for diff_id in diff_ids
        )
    ):
        raise ValueError("Direct OCI config platform or DiffID inventory is invalid")

    expanded_total = 0
    layer_paths: list[str] = []
    expected_layer_sources: dict[str, dict[str, Any]] = {}
    for layer, diff_id in zip(layers, diff_ids, strict=True):
        layer_digest, size = _oci_descriptor(
            layer, "direct OCI layer", {"mediaType", "digest", "size"}
        )
        if layer.get("mediaType") != OCI_TAR_LAYER_MEDIA_TYPE:
            raise ValueError("Unsupported direct OCI layer media type")
        if layer_digest in referenced or size > MAX_ARCHIVE_BYTES:
            raise ValueError("Duplicate or oversized direct OCI layer descriptor")
        referenced.add(layer_digest)
        layer_path = f"blobs/sha256/{layer_digest.split(':', 1)[1]}"
        layer_member = member_map.get(layer_path)
        if (
            layer_member is None
            or not layer_member.isfile()
            or layer_member.size != size
        ):
            raise ValueError("Direct OCI layer path or size mismatch")
        expanded_total = _hash_oci_uncompressed_layer(
            archive,
            layer_member,
            layer_digest,
            size,
            diff_id,
            expanded_total,
        )
        layer_paths.append(layer_path)
        expected_layer_sources[layer_digest] = {
            "mediaType": OCI_TAR_LAYER_MEDIA_TYPE,
            "size": size,
            "digest": layer_digest,
        }

    legacy = json.loads(
        _read_tar_member(archive, member_map["manifest.json"], MAX_IMAGE_CONFIG_BYTES),
        object_pairs_hook=signature._pairs_no_duplicates,
    )
    if (
        not isinstance(legacy, list)
        or len(legacy) != 1
        or not isinstance(legacy[0], dict)
        or set(legacy[0]) != {"Config", "RepoTags", "Layers", "LayerSources"}
        or not isinstance(legacy[0].get("RepoTags"), list)
        or len(legacy[0]["RepoTags"]) != 1
        or any(not isinstance(tag, str) or not tag for tag in legacy[0]["RepoTags"])
        or legacy[0].get("Config") != config_path
        or legacy[0].get("Layers") != layer_paths
        or legacy[0].get("LayerSources") != expected_layer_sources
    ):
        raise ValueError(
            "Docker compatibility manifest differs from direct OCI descriptors"
        )
    repository, separator, tag = legacy[0]["RepoTags"][0].rpartition(":")
    if not separator or not repository or not tag or not layers:
        raise ValueError("Direct OCI Docker tag or layer inventory is invalid")
    repositories_member = member_map.get("repositories")
    if repositories_member is None or not repositories_member.isfile():
        raise ValueError("Direct OCI Docker repositories metadata is missing")
    repositories = json.loads(
        _read_tar_member(archive, repositories_member, MAX_IMAGE_CONFIG_BYTES),
        object_pairs_hook=signature._pairs_no_duplicates,
    )
    if repositories != {repository: {tag: layers[-1]["digest"].split(":", 1)[1]}}:
        raise ValueError("Docker repositories metadata differs from direct OCI layers")

    expected_files = {
        "oci-layout",
        "index.json",
        "manifest.json",
        "repositories",
        manifest_path,
        config_path,
        *(f"blobs/sha256/{item.split(':', 1)[1]}" for item in referenced),
    }
    actual_files = {name for name, member in member_map.items() if member.isfile()}
    actual_directories = {name for name, member in member_map.items() if member.isdir()}
    if actual_directories != {"blobs", "blobs/sha256"} or not (
        expected_files <= actual_files
    ):
        raise ValueError("Direct OCI archive has unreferenced or missing entries")
    auxiliary = actual_files - expected_files
    if auxiliary:
        if not allow_legacy_auxiliary_blobs:
            raise ValueError("Direct OCI archive has unreferenced or missing entries")
        _validate_legacy_docker_auxiliary_blobs(
            archive, member_map, auxiliary, len(layers)
        )
    return expected_files


def _verify_oci_docker_archive(
    archive: tarfile.TarFile,
    member_map: dict[str, tarfile.TarInfo],
    expected_image_id: str,
    allow_legacy_auxiliary_blobs: bool = False,
) -> set[str]:
    if not {"oci-layout", "index.json", "manifest.json"} <= set(member_map):
        raise ValueError("OCI Docker archive is incomplete")
    layout = _oci_object(
        _read_tar_member(archive, member_map["oci-layout"], 1024), "OCI layout"
    )
    if layout != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported OCI image layout version")

    outer = _oci_object(
        _read_tar_member(archive, member_map["index.json"], MAX_IMAGE_CONFIG_BYTES),
        "OCI root index",
    )
    if (
        set(outer) != {"schemaVersion", "mediaType", "manifests"}
        or type(outer.get("schemaVersion")) is not int
        or outer["schemaVersion"] != 2
        or outer.get("mediaType") != OCI_INDEX_MEDIA_TYPE
        or not isinstance(outer.get("manifests"), list)
        or len(outer["manifests"]) != 1
    ):
        raise ValueError("Unsupported or ambiguous OCI root index")
    root_descriptor = outer["manifests"][0]
    if (
        isinstance(root_descriptor, dict)
        and root_descriptor.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
        and "platform" in root_descriptor
    ):
        raise ValueError("Direct OCI manifest descriptor must omit platform")
    digest, size = _oci_descriptor(
        root_descriptor,
        "root index",
        {"mediaType", "digest", "size"},
        {"annotations"},
    )
    if root_descriptor["mediaType"] == OCI_MANIFEST_MEDIA_TYPE:
        return _verify_oci_direct_manifest_archive(
            archive,
            member_map,
            root_descriptor,
            expected_image_id,
            allow_legacy_auxiliary_blobs,
        )
        return
    if (
        digest != expected_image_id
        or root_descriptor["mediaType"] != OCI_INDEX_MEDIA_TYPE
    ):
        raise ValueError("OCI root index digest differs from expected image ID")
    annotations = root_descriptor.get("annotations")
    if annotations is not None and (
        not isinstance(annotations, dict)
        or set(annotations)
        != {"io.containerd.image.name", "org.opencontainers.image.ref.name"}
        or any(
            not isinstance(value, str) or len(value) > 512
            for value in annotations.values()
        )
    ):
        raise ValueError("Unsupported OCI root index annotations")
    root_bytes, root_path = _read_oci_blob(
        archive,
        member_map,
        root_descriptor,
        "root index",
        set(),
        MAX_IMAGE_CONFIG_BYTES,
    )
    nested = _oci_object(root_bytes, "nested OCI index")
    if (
        set(nested) != {"schemaVersion", "mediaType", "manifests"}
        or type(nested.get("schemaVersion")) is not int
        or nested["schemaVersion"] != 2
        or nested.get("mediaType") != OCI_INDEX_MEDIA_TYPE
        or not isinstance(nested.get("manifests"), list)
        or len(nested["manifests"]) != 2
    ):
        raise ValueError("Unsupported or ambiguous nested OCI index")

    image_descriptors: list[dict[str, Any]] = []
    attestation_descriptors: list[dict[str, Any]] = []
    for descriptor in nested["manifests"]:
        if not isinstance(descriptor, dict):
            raise TypeError("Invalid nested OCI descriptor")
        platform = descriptor.get("platform")
        annotations = descriptor.get("annotations")
        if (
            descriptor.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
            and isinstance(platform, dict)
            and set(platform) == {"os", "architecture"}
            and all(isinstance(platform.get(key), str) for key in platform)
            and platform.get("os") != "unknown"
            and platform.get("architecture") != "unknown"
            and "annotations" not in descriptor
        ):
            _oci_descriptor(
                descriptor,
                "platform image",
                {"mediaType", "digest", "size", "platform"},
            )
            image_descriptors.append(descriptor)
        elif (
            descriptor.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
            and platform == {"os": "unknown", "architecture": "unknown"}
            and isinstance(annotations, dict)
            and set(annotations)
            == {"vnd.docker.reference.digest", "vnd.docker.reference.type"}
            and annotations.get("vnd.docker.reference.type") == "attestation-manifest"
        ):
            _oci_descriptor(
                descriptor,
                "Docker attestation",
                {"mediaType", "digest", "size", "platform", "annotations"},
            )
            attestation_descriptors.append(descriptor)
        else:
            raise ValueError("Unsupported nested OCI descriptor")
    if len(image_descriptors) != 1 or len(attestation_descriptors) != 1:
        raise ValueError("OCI image or attestation descriptor is ambiguous")
    image_descriptor = image_descriptors[0]
    attestation_descriptor = attestation_descriptors[0]
    image_digest, _ = _oci_descriptor(
        image_descriptor,
        "platform image",
        {"mediaType", "digest", "size", "platform"},
    )
    if (
        attestation_descriptor["annotations"].get("vnd.docker.reference.digest")
        != image_digest
        or len({image_digest, attestation_descriptor["digest"]}) != 2
    ):
        raise ValueError("OCI attestation does not reference the unique image")

    referenced = {digest}
    image_manifest_raw, image_manifest_path = _read_oci_blob(
        archive,
        member_map,
        image_descriptor,
        "platform image manifest",
        referenced,
        MAX_IMAGE_CONFIG_BYTES,
    )
    image_manifest = _oci_object(image_manifest_raw, "OCI image manifest")
    if (
        set(image_manifest) != {"schemaVersion", "mediaType", "config", "layers"}
        or type(image_manifest.get("schemaVersion")) is not int
        or image_manifest["schemaVersion"] != 2
        or image_manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE
        or not isinstance(image_manifest.get("layers"), list)
    ):
        raise ValueError("Unsupported OCI image manifest")
    config_descriptor = image_manifest.get("config")
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor.get("mediaType") != OCI_CONFIG_MEDIA_TYPE
    ):
        raise ValueError("Unsupported OCI image config descriptor")
    _oci_descriptor(config_descriptor, "image config", {"mediaType", "digest", "size"})
    config_raw, config_path = _read_oci_blob(
        archive,
        member_map,
        config_descriptor,
        "image config",
        referenced,
        MAX_IMAGE_CONFIG_BYTES,
    )
    config = _oci_object(config_raw, "OCI image config")
    platform = image_descriptor["platform"]
    rootfs = config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    layers = image_manifest["layers"]
    if (
        config.get("os") != platform["os"]
        or config.get("architecture") != platform["architecture"]
        or not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(diff_ids, list)
        or not isinstance(layers, list)
        or len(diff_ids) != len(layers)
        or not layers
        or any(
            not isinstance(diff_id, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", diff_id)
            for diff_id in diff_ids
        )
    ):
        raise ValueError("OCI config platform or DiffID inventory is invalid")

    expanded_total = 0
    layer_paths: list[str] = []
    for layer, diff_id in zip(layers, diff_ids, strict=True):
        digest, size = _oci_descriptor(
            layer, "image layer", {"mediaType", "digest", "size"}
        )
        if layer["mediaType"] != OCI_GZIP_LAYER_MEDIA_TYPE:
            raise ValueError("Unsupported OCI layer media type")
        if digest in referenced or size > MAX_ARCHIVE_BYTES:
            raise ValueError("Duplicate or oversized OCI layer descriptor")
        referenced.add(digest)
        layer_path = f"blobs/sha256/{digest.split(':', 1)[1]}"
        layer_member = member_map.get(layer_path)
        if (
            layer_member is None
            or not layer_member.isfile()
            or layer_member.size != size
        ):
            raise ValueError("OCI layer path or size mismatch")
        expanded_total = _hash_oci_layer(
            archive, layer_member, digest, diff_id, expanded_total
        )
        layer_paths.append(layer_path)

    attestation_raw, attestation_path = _read_oci_blob(
        archive,
        member_map,
        attestation_descriptor,
        "Docker attestation manifest",
        referenced,
        MAX_IMAGE_CONFIG_BYTES,
    )
    attestation = _oci_object(attestation_raw, "Docker attestation manifest")
    if (
        set(attestation)
        != {"schemaVersion", "mediaType", "artifactType", "config", "layers", "subject"}
        or type(attestation.get("schemaVersion")) is not int
        or attestation["schemaVersion"] != 2
        or attestation.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE
        or attestation.get("artifactType") != DOCKER_ATTESTATION_MEDIA_TYPE
        or not isinstance(attestation.get("layers"), list)
        or len(attestation["layers"]) != 1
        or attestation.get("subject")
        != {
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "digest": image_digest,
            "size": image_descriptor["size"],
        }
    ):
        raise ValueError("Unsupported Docker attestation manifest")
    empty_descriptor = attestation.get("config")
    if (
        not isinstance(empty_descriptor, dict)
        or set(empty_descriptor) != {"mediaType", "digest", "size", "data"}
        or empty_descriptor.get("mediaType") != OCI_EMPTY_MEDIA_TYPE
        or not isinstance(empty_descriptor.get("data"), str)
        or len(empty_descriptor["data"]) > 128
    ):
        raise ValueError("Unsupported Docker attestation config")
    try:
        empty_data = base64.b64decode(empty_descriptor["data"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Malformed Docker attestation config data") from exc
    if empty_data != b"{}":
        raise ValueError("Docker attestation config is not empty")
    _read_oci_blob(
        archive,
        member_map,
        {key: empty_descriptor[key] for key in ("mediaType", "digest", "size")},
        "Docker attestation config",
        referenced,
        1024,
    )
    attestation_layer = attestation["layers"][0]
    if (
        not isinstance(attestation_layer, dict)
        or set(attestation_layer) != {"mediaType", "digest", "size", "annotations"}
        or attestation_layer.get("mediaType") != IN_TOTO_MEDIA_TYPE
        or attestation_layer.get("annotations")
        != {"in-toto.io/predicate-type": "https://slsa.dev/provenance/v1"}
    ):
        raise ValueError("Unsupported Docker attestation payload descriptor")
    self_attestation, attestation_layer_path = _read_oci_blob(
        archive,
        member_map,
        attestation_layer,
        "Docker attestation payload",
        referenced,
        MAX_IMAGE_CONFIG_BYTES,
    )
    if not self_attestation:
        raise ValueError("Docker attestation payload is empty")

    legacy = json.loads(
        _read_tar_member(archive, member_map["manifest.json"], MAX_IMAGE_CONFIG_BYTES),
        object_pairs_hook=signature._pairs_no_duplicates,
    )
    if (
        not isinstance(legacy, list)
        or len(legacy) != 1
        or not isinstance(legacy[0], dict)
        or set(legacy[0]) != {"Config", "RepoTags", "Layers"}
        or not isinstance(legacy[0].get("RepoTags"), list)
        or not legacy[0]["RepoTags"]
        or any(not isinstance(tag, str) for tag in legacy[0]["RepoTags"])
        or legacy[0].get("Config") != config_path
        or legacy[0].get("Layers") != layer_paths
    ):
        raise ValueError("Docker compatibility manifest differs from OCI descriptors")

    expected_files = {
        "oci-layout",
        "index.json",
        "manifest.json",
        root_path,
        image_manifest_path,
        config_path,
        attestation_path,
        attestation_layer_path,
        *(f"blobs/sha256/{item.split(':', 1)[1]}" for item in referenced),
    }
    actual_files = {name for name, member in member_map.items() if member.isfile()}
    actual_directories = {name for name, member in member_map.items() if member.isdir()}
    if actual_directories != {"blobs", "blobs/sha256"} or not (
        expected_files <= actual_files
    ):
        raise ValueError("OCI archive has unreferenced or missing entries")
    auxiliary = actual_files - expected_files
    if auxiliary:
        if not allow_legacy_auxiliary_blobs:
            raise ValueError("OCI archive has unreferenced or missing entries")
        _validate_legacy_docker_auxiliary_blobs(
            archive, member_map, auxiliary, len(image_manifest["layers"])
        )
    return expected_files


def _verify_classic_docker_archive(
    archive: tarfile.TarFile,
    member_map: dict[str, tarfile.TarInfo],
    expected_image_id: str,
) -> None:
    manifest_member = member_map.get("manifest.json")
    if manifest_member is None:
        raise ValueError("Docker archive manifest is missing")
    if manifest_member.size > MAX_IMAGE_CONFIG_BYTES:
        raise ValueError("Docker archive manifest is too large")
    manifest_data = _read_tar_member(archive, manifest_member, MAX_IMAGE_CONFIG_BYTES)
    try:
        entries = json.loads(
            manifest_data, object_pairs_hook=signature._pairs_no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed Docker archive manifest") from exc
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
    ):
        raise ValueError("Expected one image in Docker archive")
    entry = entries[0]
    if set(entry) not in (
        {"Config", "RepoTags", "Layers"},
        {"Config", "RepoTags", "Layers", "Parent"},
    ):
        raise ValueError("Unsupported Docker archive manifest fields")
    if (
        not isinstance(entry["RepoTags"], list)
        or not entry["RepoTags"]
        or any(not isinstance(tag, str) for tag in entry["RepoTags"])
        or not isinstance(entry["Layers"], list)
        or not isinstance(entry["Config"], str)
        or ("Parent" in entry and not isinstance(entry["Parent"], str))
    ):
        raise ValueError("Invalid Docker archive image record")
    config_name = entry["Config"]
    if not _safe_tar_name(config_name):
        raise ValueError("Unsafe Docker image config path")
    config_member = member_map.get(config_name)
    if config_member is None:
        raise ValueError("Docker image config is missing")
    config_bytes = _read_tar_member(archive, config_member, MAX_IMAGE_CONFIG_BYTES)
    if "sha256:" + hashlib.sha256(config_bytes).hexdigest() != expected_image_id:
        raise ValueError("Docker archive config differs from expected image ID")
    config = _json_object(config_bytes, "Docker image config")
    rootfs = config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    layers = entry["Layers"]
    if (
        not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or any(
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            for digest in diff_ids
        )
    ):
        raise ValueError("Docker config layer digests are invalid")
    if any(not isinstance(layer, str) for layer in layers) or len(set(layers)) != len(
        layers
    ):
        raise ValueError("Duplicate Docker image layer reference")
    for layer_name, expected_diff_id in zip(layers, diff_ids, strict=True):
        if not _safe_tar_name(layer_name):
            raise ValueError("Unsafe Docker image layer path")
        layer_member = member_map.get(layer_name)
        if layer_member is None or not layer_member.isfile():
            raise ValueError("Docker image layer is missing")
        layer_file = archive.extractfile(layer_member)
        if layer_file is None:
            raise ValueError("Docker image layer cannot be read")
        digest = hashlib.sha256()
        with layer_file:
            while chunk := layer_file.read(1024 * 1024):
                digest.update(chunk)
        if "sha256:" + digest.hexdigest() != expected_diff_id:
            raise ValueError("Docker layer digest differs from image config")


def _verify_docker_archive(
    fd: int,
    expected_image_id: str,
    *,
    allow_legacy_auxiliary_blobs: bool = False,
) -> set[str] | None:
    os.lseek(fd, 0, os.SEEK_SET)
    before = os.fstat(fd)
    expected_members = _prevalidate_docker_tar(fd)
    after_prevalidation = os.fstat(fd)
    if _stat_identity(before) != _stat_identity(after_prevalidation):
        raise ValueError("Docker archive changed during TAR pre-validation")
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), "rb") as archive_stream:
        try:
            with tarfile.open(fileobj=archive_stream, mode="r:") as archive:
                members = []
                for member in archive:
                    if len(members) >= MAX_ARCHIVE_MEMBERS:
                        raise ValueError("Docker archive member count is invalid")
                    members.append(member)
                parsed_members = [
                    (
                        member.name.rstrip("/") + "/"
                        if member.isdir()
                        else member.name,
                        member.size,
                        "directory" if member.isdir() else "file",
                    )
                    for member in members
                ]
                if parsed_members != expected_members:
                    raise ValueError("TAR parser disagrees with validated headers")
                if not members:
                    raise ValueError("Docker archive member count is invalid")
                member_map: dict[str, tarfile.TarInfo] = {}
                total_member_bytes = 0
                for member in members:
                    if (
                        not _safe_tar_name(member.name)
                        or member.name in member_map
                        or not (member.isfile() or member.isdir())
                        or getattr(member, "sparse", None)
                    ):
                        raise ValueError("Unsafe or duplicate Docker archive member")
                    total_member_bytes += member.size
                    if total_member_bytes > MAX_ARCHIVE_BYTES:
                        raise ValueError("Docker archive expanded size exceeds limit")
                    member_name = (
                        member.name.rstrip("/") if member.isdir() else member.name
                    )
                    member_map[member_name] = member
                oci_markers = {"oci-layout", "index.json"} & set(member_map)
                reachable_files = None
                if oci_markers:
                    if oci_markers != {"oci-layout", "index.json"}:
                        raise ValueError("Partial OCI image layout is unsupported")
                    reachable_files = _verify_oci_docker_archive(
                        archive,
                        member_map,
                        expected_image_id,
                        allow_legacy_auxiliary_blobs,
                    )
                else:
                    if allow_legacy_auxiliary_blobs:
                        raise ValueError(
                            "Canonical export requires a supported OCI profile"
                        )
                    _verify_classic_docker_archive(
                        archive, member_map, expected_image_id
                    )
        except tarfile.TarError as exc:
            raise ValueError("Malformed Docker image archive") from exc
    if _stat_identity(after_prevalidation) != _stat_identity(os.fstat(fd)):
        raise ValueError("Docker archive changed during TAR parsing")
    return reachable_files


def _read_handoff_manifest(directory_fd: int) -> tuple[dict[str, Any], bytes]:
    fd = os.open(
        "manifest.json",
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise ValueError("Handoff manifest is not an acceptable regular file")
        chunks = bytearray()
        while len(chunks) <= 1024 * 1024:
            block = os.read(fd, min(65536, 1024 * 1024 + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        after = os.fstat(fd)
        current = os.stat("manifest.json", dir_fd=directory_fd, follow_symlinks=False)
        if (
            len(chunks) > 1024 * 1024
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("Handoff manifest changed while being read")
        raw = bytes(chunks)
        return _json_object(raw, "Docker image handoff manifest"), raw
    finally:
        os.close(fd)


def _validate_handoff_archives(
    handoff_dir: Path,
    evidence_manifest: dict[str, Any],
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_image_ids: dict[str, str],
    *,
    evidence_manifest_bytes: bytes,
    canonical_output_dir: Path | None = None,
) -> bytes | None:
    directory_fd = _directory_fd(handoff_dir)
    canonical_directory_fd = -1
    try:
        if canonical_output_dir is not None:
            canonical_directory_fd = _directory_fd(
                canonical_output_dir, require_trusted=True
            )
            directory_info = os.fstat(canonical_directory_fd)
            if directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077:
                raise ValueError(
                    "Canonical workspace must be private and operator-owned"
                )
        expected_names = {
            "manifest.json",
            *(f"{name}.tar" for name in expected_image_ids),
        }
        if set(os.listdir(directory_fd)) != expected_names:
            raise ValueError("Handoff directory has missing or unexpected files")
        handoff, handoff_bytes = _read_handoff_manifest(directory_fd)
        local_identity = vulnerability_ci.identity()
        if local_identity.get("commit") != expected_commit:
            raise ValueError("Checkout does not match expected evidence commit")
        expected_identity = local_identity | {
            "run_id": expected_run_id,
            "run_attempt": str(expected_run_attempt),
        }
        if any(handoff.get(key) != value for key, value in expected_identity.items()):
            raise ValueError("Docker handoff run or material identity mismatch")
        image_records = handoff.get("images")
        if not isinstance(image_records, dict) or set(image_records) != set(
            expected_image_ids
        ):
            raise ValueError("Docker handoff image inventory is incomplete")
        evidence_records = {
            record.get("name"): record
            for record in evidence_manifest.get("components", [])
            if isinstance(record, dict)
        }
        total_bytes = 0
        total_canonical_bytes = 0
        transformations: list[dict[str, Any]] = []
        for name, expected_image_id in sorted(expected_image_ids.items()):
            filename = f"{name}.tar"
            fd = os.open(
                filename,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_size <= 0
                    or before.st_size > MAX_ARCHIVE_BYTES
                ):
                    raise ValueError("Docker handoff archive is missing or too large")
                digest = hashlib.sha256()
                total_bytes += before.st_size
                if total_bytes > MAX_HANDOFF_ARCHIVE_BYTES:
                    raise ValueError("Docker handoff archives exceed total size limit")
                while chunk := os.read(fd, 1024 * 1024):
                    digest.update(chunk)
                archive_sha256 = digest.hexdigest()
                handoff_item = image_records[name]
                component = evidence_records.get(name)
                materials = (
                    component.get("materials") if isinstance(component, dict) else None
                )
                material = (
                    materials[0]
                    if isinstance(materials, list) and len(materials) == 1
                    else None
                )
                if (
                    not isinstance(handoff_item, dict)
                    or set(handoff_item) != {"file", "sha256", "bytes", "image_id"}
                    or handoff_item.get("file") != filename
                    or handoff_item.get("image_id") != expected_image_id
                    or handoff_item.get("sha256") != archive_sha256
                    or type(handoff_item.get("bytes")) is not int
                    or handoff_item["bytes"] != before.st_size
                    or not isinstance(material, dict)
                    or material.get("path") != f"docker-build/{filename}"
                    or material.get("sha256") != archive_sha256
                    or type(material.get("bytes")) is not int
                    or material.get("bytes") != before.st_size
                ):
                    raise ValueError(
                        "Docker archive bytes differ from declared materials"
                    )
                after_hash = os.fstat(fd)
                current = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
                if _stat_identity(before) != _stat_identity(after_hash) or (
                    after_hash.st_dev,
                    after_hash.st_ino,
                ) != (current.st_dev, current.st_ino):
                    raise ValueError("Docker handoff archive changed during hashing")
                if canonical_output_dir is None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _verify_docker_archive(fd, expected_image_id)
                    after_archive = os.fstat(fd)
                    if _stat_identity(after_hash) != _stat_identity(after_archive):
                        raise ValueError(
                            "Docker handoff archive changed during validation"
                        )
                else:
                    import oci_canonicalizer

                    canonical_name = f"{name}.canonical.tar"
                    canonical_path = canonical_output_dir / canonical_name
                    result = oci_canonicalizer.canonicalize_oci_archive(
                        handoff_dir / filename, canonical_path, expected_image_id
                    )
                    if (
                        result.source_sha256 != archive_sha256
                        or result.source_bytes != before.st_size
                        or result.image_id != expected_image_id
                        or result.canonical != canonical_path
                    ):
                        raise ValueError(
                            "Canonical transformation does not match original material"
                        )
                    source_after_transform = os.fstat(fd)
                    source_path_after_transform = os.stat(
                        filename, dir_fd=directory_fd, follow_symlinks=False
                    )
                    if _stat_identity(after_hash) != _stat_identity(
                        source_after_transform
                    ) or (
                        source_after_transform.st_dev,
                        source_after_transform.st_ino,
                    ) != (
                        source_path_after_transform.st_dev,
                        source_path_after_transform.st_ino,
                    ):
                        raise ValueError(
                            "Original handoff archive changed during canonicalization"
                        )
                    canonical_fd = os.open(
                        canonical_name,
                        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=canonical_directory_fd,
                    )
                    try:
                        canonical_before = os.fstat(canonical_fd)
                        if (
                            not stat.S_ISREG(canonical_before.st_mode)
                            or canonical_before.st_mode & 0o077
                            or canonical_before.st_size <= 0
                            or canonical_before.st_size > MAX_ARCHIVE_BYTES
                        ):
                            raise ValueError(
                                "Canonical archive is missing or too large"
                            )
                        canonical_digest = hashlib.sha256()
                        while chunk := os.read(canonical_fd, 1024 * 1024):
                            canonical_digest.update(chunk)
                        canonical_sha256 = canonical_digest.hexdigest()
                        if (
                            canonical_sha256 != result.canonical_sha256
                            or canonical_before.st_size != result.canonical_bytes
                        ):
                            raise ValueError(
                                "Canonical archive bytes differ from exporter result"
                            )
                        canonical_after = os.fstat(canonical_fd)
                        canonical_current = os.stat(
                            canonical_name,
                            dir_fd=canonical_directory_fd,
                            follow_symlinks=False,
                        )
                        if _stat_identity(canonical_before) != _stat_identity(
                            canonical_after
                        ) or (canonical_after.st_dev, canonical_after.st_ino) != (
                            canonical_current.st_dev,
                            canonical_current.st_ino,
                        ):
                            raise ValueError(
                                "Canonical archive changed during verification"
                            )
                    finally:
                        os.close(canonical_fd)
                    total_canonical_bytes += result.canonical_bytes
                    if total_canonical_bytes > MAX_HANDOFF_ARCHIVE_BYTES:
                        raise ValueError("Canonical archives exceed total size limit")
                    transformations.append(
                        {
                            "image": name,
                            "image_id": expected_image_id,
                            "profile": result.profile,
                            "transformation": OCI_TRANSFORM_ID,
                            "original": {
                                "path": f"docker-build/{filename}",
                                "sha256": archive_sha256,
                                "bytes": before.st_size,
                            },
                            "canonical": {
                                "sha256": canonical_sha256,
                                "bytes": canonical_before.st_size,
                            },
                            "removed_blobs": list(result.removed_blobs),
                        }
                    )
            finally:
                os.close(fd)
        if canonical_output_dir is None:
            return None
        if (
            not isinstance(evidence_manifest_bytes, bytes)
            or evidence_manifest.get("commit") != expected_commit
            or evidence_manifest.get("workflow_run_id") != expected_run_id
            or evidence_manifest.get("run_attempt") != expected_run_attempt
        ):
            raise ValueError("Evidence identity is inconsistent with transformation")
        receipt = {
            "schema_version": 1,
            "transformation": OCI_TRANSFORM_ID,
            "commit": expected_commit,
            "workflow_run_id": expected_run_id,
            "run_attempt": expected_run_attempt,
            "evidence_manifest_sha256": hashlib.sha256(
                evidence_manifest_bytes
            ).hexdigest(),
            "handoff_manifest_sha256": hashlib.sha256(handoff_bytes).hexdigest(),
            "images": transformations,
        }
        return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    finally:
        if canonical_directory_fd >= 0:
            os.close(canonical_directory_fd)
        os.close(directory_fd)


def _validate_manifest(
    data: dict[str, bytes],
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_image_ids: dict[str, str],
) -> dict[str, Any]:
    manifest_bytes = data["evidence-manifest.json"]
    manifest = _json_object(manifest_bytes, "evidence manifest")
    if set(manifest) != MANIFEST_KEYS or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported evidence manifest schema")
    if (
        manifest.get("repository") != expected_repository
        or manifest.get("workflow") != "CI"
        or manifest.get("workflow_run_id") != expected_run_id
        or manifest.get("run_attempt") != expected_run_attempt
        or manifest.get("commit") != expected_commit
    ):
        raise ValueError("Evidence run identity mismatch")
    if not re.fullmatch(r"[a-f0-9]{40}", expected_commit):
        raise ValueError("Invalid expected commit SHA")
    if (
        not re.fullmatch(r"[0-9]+", expected_run_id)
        or not isinstance(expected_run_attempt, int)
        or isinstance(expected_run_attempt, bool)
        or expected_run_attempt < 1
        or type(manifest.get("run_attempt")) is not int
    ):
        raise ValueError("Invalid expected workflow run identity")
    if (
        manifest.get("signature_status") != "NOT_GENERATED"
        or manifest.get("authenticity") != "NOT_VERIFIED"
        or manifest.get("builder_identity") != "NOT_ASSERTED"
        or manifest.get("slsa_level") != "NOT_CLAIMED"
        or manifest.get("overall_coverage") != "COVERAGE_PARTIAL"
        or manifest.get("limitations") != F3C2_LIMITATIONS
        or not isinstance(manifest.get("generated_at"), str)
        or not isinstance(manifest.get("duration_seconds"), (int, float))
        or isinstance(manifest.get("duration_seconds"), bool)
        or manifest["duration_seconds"] < 0
        or type(manifest.get("bytes")) is not int
    ):
        raise ValueError("Unexpected evidence trust or coverage claims")

    sidecar = data["evidence-manifest.sha256"].decode("ascii", errors="strict").strip()
    sidecar_match = re.fullmatch(r"([a-f0-9]{64})  evidence-manifest\.json", sidecar)
    if (
        not sidecar_match
        or sidecar_match.group(1) != hashlib.sha256(manifest_bytes).hexdigest()
    ):
        raise ValueError("Evidence manifest sidecar digest mismatch")

    records = manifest.get("files")
    if not isinstance(records, list) or len(records) != 12:
        raise ValueError("Evidence file inventory is incomplete")
    file_records: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}:
            raise ValueError("Invalid evidence file record")
        name = record["path"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name not in _expected_evidence_files()
            or name in file_records
            or type(record["bytes"]) is not int
            or record["bytes"] < 0
            or not isinstance(record["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
        ):
            raise ValueError("Invalid or unexpected evidence file path")
        raw = data[name]
        if (
            len(raw) != record["bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError("Evidence file digest or size mismatch")
        file_records[name] = record
    expected_listed = _expected_evidence_files() - {
        "evidence-manifest.json",
        "evidence-manifest.sha256",
    }
    if set(file_records) != expected_listed or manifest.get("bytes") != sum(
        item["bytes"] for item in records
    ):
        raise ValueError("Evidence file inventory mismatch")

    tools = manifest.get("tools")
    if not isinstance(tools, dict) or set(tools) != {"trivy", "sbom_tools_lock_sha256"}:
        raise ValueError("Invalid tool provenance")
    trivy = tools.get("trivy")
    if (
        not isinstance(trivy, dict)
        or trivy.get("version") != sbom.PINS["trivy"]
        or trivy.get("sha256") != sbom.PINS["trivy_linux_x64_binary_sha256"]
        or tools.get("sbom_tools_lock_sha256")
        != sbom.digest(sbom.ROOT / "security/sbom-tools.lock")
    ):
        raise ValueError("Evidence tool inventory differs from local locks")

    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        raise ValueError("Evidence component inventory is incomplete")
    component_records: dict[str, dict[str, Any]] = {}
    for component in components:
        if not isinstance(component, dict):
            raise TypeError("Invalid component record")
        name = component.get("name")
        if name not in COMPONENTS or name in component_records:
            raise ValueError("Unexpected or duplicate evidence component")
        kind, coverage = COMPONENTS[name]
        if (
            component.get("kind") != kind
            or component.get("coverage_status") != coverage
        ):
            raise ValueError("Component type or coverage state mismatch")
        expected_source = {
            "python": "backend/requirements.lock",
            "npm": "frontend/package-lock.json",
            "backend-image": "docker-build image archive",
            "nginx-image": "docker-build image archive",
        }[name]
        if component.get("inventory_source") != expected_source:
            raise ValueError("Component inventory source mismatch")
        materials = component.get("materials")
        if not isinstance(materials, list) or len(materials) != 1:
            raise ValueError("Component material inventory is invalid")
        material = materials[0]
        if (
            not isinstance(material, dict)
            or set(material) != {"path", "sha256", "bytes"}
            or not isinstance(material.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", material["sha256"])
            or not isinstance(material.get("bytes"), int)
            or isinstance(material.get("bytes"), bool)
            or material["bytes"] < 0
        ):
            raise ValueError("Component material record is invalid")
        if name in ("python", "npm"):
            if material["path"] != expected_source:
                raise ValueError("Lockfile material path mismatch")
        elif not isinstance(material.get("path"), str) or not re.fullmatch(
            r"docker-build/[A-Za-z0-9._-]+\.tar", material["path"]
        ):
            raise ValueError("Image archive material path is invalid")
        expected_artifacts = {
            f"{name}.cdx.json",
            f"{name}.manifest.json",
            f"{name}.provenance.json",
        }
        artifacts = component.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) != 3
            or any(not isinstance(item, dict) for item in artifacts)
            or {item.get("path") for item in artifacts} != expected_artifacts
        ):
            raise ValueError("Component artifact list mismatch")
        for artifact in artifacts:
            if artifact != file_records.get(artifact.get("path")):
                raise ValueError("Component artifact record mismatch")
        sbom_doc = _json_object(data[f"{name}.cdx.json"], f"{name} SBOM")
        metadata = sbom_doc.get("metadata")
        root = metadata.get("component") if isinstance(metadata, dict) else None
        if (
            sbom_doc.get("bomFormat") != "CycloneDX"
            or sbom_doc.get("specVersion") != "1.7"
            or not isinstance(root, dict)
        ):
            raise ValueError("Unsupported CycloneDX SBOM")
        component_manifest = _json_object(
            data[f"{name}.manifest.json"], f"{name} component manifest"
        )
        if (
            component_manifest.get("generation_status") != "SUCCESS"
            or component_manifest.get("commit") != expected_commit
            or component_manifest.get("status") != coverage
            or component_manifest.get("sha256")
            != file_records[f"{name}.cdx.json"]["sha256"]
            or component_manifest.get("bytes")
            != file_records[f"{name}.cdx.json"]["bytes"]
        ):
            raise ValueError("Component manifest does not match SBOM")
        if component.get("coverage") != component_manifest.get("coverage"):
            raise ValueError("Component coverage record differs from its manifest")
        if name in ("python", "npm"):
            if component_manifest.get("source_sha256") != material["sha256"]:
                raise ValueError(
                    "Lockfile material digest differs from component manifest"
                )
            cov = component_manifest.get("coverage")
            expected_count = 49 if name == "python" else 392
            if (
                not isinstance(cov, dict)
                or cov.get("status") != "COVERAGE_PASS"
                or cov.get("expected") != expected_count
                or cov.get("found") != expected_count
                or any(
                    cov.get(field)
                    for field in (
                        "missing",
                        "unexpected",
                        "unclassified",
                        "version_mismatches",
                    )
                )
            ):
                raise ValueError("Lockfile coverage is incomplete or inconsistent")
        else:
            image_id = expected_image_ids.get(name)
            if not isinstance(image_id, str) or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", image_id
            ):
                raise ValueError("Independent image ID is required")
            identity = component.get("image_identity")
            if (
                not isinstance(identity, dict)
                or identity != {"kind": "docker-local-image-id", "value": image_id}
                or component_manifest.get("image_id") != image_id
                or root.get("name") != image_id
                or root.get("purl") != f"pkg:oci/{name}@{image_id}"
            ):
                raise ValueError("Independent image identity does not match evidence")
            if component.get("coverage") is not None:
                raise ValueError("Image coverage must remain explicitly partial")
        statement = _json_object(
            data[f"{name}.provenance.json"], f"{name} provenance statement"
        )
        subject = statement.get("subject")
        predicate = statement.get("predicate")
        definition = (
            predicate.get("buildDefinition") if isinstance(predicate, dict) else None
        )
        parameters = (
            definition.get("externalParameters")
            if isinstance(definition, dict)
            else None
        )
        run_details = (
            predicate.get("runDetails") if isinstance(predicate, dict) else None
        )
        subject_entry = (
            subject[0] if isinstance(subject, list) and len(subject) == 1 else None
        )
        subject_digest = (
            subject_entry.get("digest") if isinstance(subject_entry, dict) else None
        )
        if (
            not isinstance(subject_entry, dict)
            or not isinstance(subject_digest, dict)
            or not isinstance(parameters, dict)
            or not isinstance(run_details, dict)
            or not isinstance(run_details.get("builder"), dict)
        ):
            raise TypeError("Malformed provenance statement structure")
        if (
            statement.get("_type") != provenance.TYPE
            or statement.get("predicateType") != provenance.PREDICATE
            or subject_entry.get("name") != f"{name}.cdx.json"
            or subject_digest.get("sha256")
            != file_records[f"{name}.cdx.json"]["sha256"]
            or parameters.get("commit") != expected_commit
            or parameters.get("manifestSha256")
            != file_records[f"{name}.manifest.json"]["sha256"]
            or parameters.get("coverageStatus") != coverage
            or parameters.get("context") != "local-untrusted"
            or run_details.get("builder") != {}
        ):
            raise ValueError("Provenance statement binding mismatch")
        if (
            name in expected_image_ids
            and parameters.get("imageId") != expected_image_ids[name]
        ):
            raise ValueError("Statement image identity mismatch")
        component_records[name] = component

    return manifest


def _snapshot_files(data: dict[str, bytes], directory: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, contents in data.items():
        path = directory / name
        with path.open("xb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        paths[name] = path
    return paths


def _validate_signature_output(
    signature_dir: Path,
    evidence_dir: Path,
    protected_inputs: list[Path],
) -> tuple[int, str, Path]:
    destination_name = signature_dir.name
    if (
        not destination_name
        or destination_name in {".", ".."}
        or "/" in destination_name
    ):
        raise ValueError("Signature output name is invalid")
    destination = Path(os.path.abspath(signature_dir))
    evidence = Path(os.path.realpath(evidence_dir))
    if (
        destination == sbom.ROOT
        or sbom.ROOT in destination.parents
        or destination == evidence
        or destination in evidence.parents
        or evidence in destination.parents
    ):
        raise ValueError(
            "Signature output must be separate from the checkout and evidence"
        )
    for source in protected_inputs:
        resolved = source.resolve(strict=True)
        if (
            destination == resolved
            or destination in resolved.parents
            or resolved in destination.parents
        ):
            raise ValueError("Signature output overlaps a protected input")
    sbom.check_output(signature_dir, protected_inputs)
    parent_fd = _directory_fd(signature_dir.parent, require_trusted=True)
    try:
        opened = os.fstat(parent_fd)
        current = os.stat(signature_dir.parent)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Signature output parent changed during validation")
        try:
            os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Signature output directory already exists")
        return parent_fd, destination_name, signature_dir.absolute().parent
    except BaseException:
        os.close(parent_fd)
        raise


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(
            errno.ENOSYS, "renameat2 is required for safe publication"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_fd,
        os.fsencode(source),
        parent_fd,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _verify_output_parent(parent_fd: int, parent_path: Path) -> None:
    _assert_trusted_directory(parent_fd)
    try:
        current_fd = _directory_fd(parent_path, require_trusted=True)
    except OSError as exc:
        raise ValueError("Signature output parent was substituted") from exc
    try:
        current = os.fstat(current_fd)
        opened = os.fstat(parent_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Signature output parent was substituted")
    finally:
        os.close(current_fd)


def _publish_bundle_directory(
    parent_fd: int,
    destination_name: str,
    bundles: dict[str, bytes],
    parent_path: Path,
    canonicalize_oci: bool = False,
) -> None:
    allowed_names = _bundle_names(canonicalize_oci)
    if set(bundles) != allowed_names:
        raise ValueError("Signature bundle set is incomplete")
    _verify_output_parent(parent_fd, parent_path)
    temporary_name = f".ci-evidence-signatures-{secrets.token_hex(16)}"
    os.mkdir(temporary_name, mode=0o700, dir_fd=parent_fd)
    created = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except BaseException:
        try:
            current = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
            if (created.st_dev, created.st_ino) == (current.st_dev, current.st_ino):
                os.rmdir(temporary_name, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    published = False
    created_files: set[str] = set()
    try:
        for name, contents in bundles.items():
            if (
                name not in allowed_names
                or not isinstance(contents, bytes)
                or not contents
                or len(contents) > signature.MAX_BUNDLE_BYTES
            ):
                raise ValueError("Invalid signature bundle output")
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=temporary_fd,
            )
            created_files.add(name)
            try:
                view = memoryview(contents)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("Short write while publishing signature bundle")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(temporary_fd)
        opened = os.fstat(temporary_fd)
        current = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (opened.st_dev, opened.st_ino) != (
            current.st_dev,
            current.st_ino,
        ):
            raise ValueError("Temporary signature directory was substituted")
        _verify_output_parent(parent_fd, parent_path)
        _rename_noreplace(parent_fd, temporary_name, destination_name)
        published = True
        os.fsync(parent_fd)
        _verify_output_parent(parent_fd, parent_path)
        destination = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (destination.st_dev, destination.st_ino):
            raise ValueError("Published signature directory was substituted")
    except BaseException:
        if published:
            try:
                current = os.stat(
                    destination_name, dir_fd=parent_fd, follow_symlinks=False
                )
                opened = os.fstat(temporary_fd)
                if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                    _rename_noreplace(parent_fd, destination_name, temporary_name)
                    published = False
                else:
                    # RENAME_NOREPLACE moved an unexpected source entry; restore
                    # it without following or deleting the third-party object.
                    _rename_noreplace(parent_fd, destination_name, temporary_name)
                    published = False
            except OSError:
                pass
        for name in created_files:
            try:
                os.unlink(name, dir_fd=temporary_fd)
            except OSError:
                pass
        try:
            os.fsync(temporary_fd)
            current = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(temporary_fd)
            if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                os.close(temporary_fd)
                temporary_fd = -1
                os.rmdir(temporary_name, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)


def _load_and_validate(
    evidence_dir: Path,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_image_ids: dict[str, str],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    data = _read_exact_directory(evidence_dir, _expected_evidence_files())
    manifest = _validate_manifest(
        data,
        expected_commit,
        expected_run_id,
        expected_run_attempt,
        expected_repository,
        expected_image_ids,
    )
    return data, manifest


def sign_evidence(
    evidence_dir: Path,
    handoff_dir: Path,
    signature_dir: Path,
    private_key: Path,
    cosign: Path,
    trivy: Path,
    policy_path: Path,
    policy_sha256: str,
    key_id: str,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_image_ids: dict[str, str],
    canonicalize_oci: bool = False,
) -> dict[str, Any]:
    data, manifest = _load_and_validate(
        evidence_dir,
        expected_commit,
        expected_run_id,
        expected_run_attempt,
        expected_repository,
        expected_image_ids,
    )
    canonical_context = (
        tempfile.TemporaryDirectory(prefix="infrapulse-oci-canonical-")
        if canonicalize_oci
        else nullcontext(None)
    )
    with canonical_context as canonical_root:
        transformation_receipt = _validate_handoff_archives(
            handoff_dir,
            manifest,
            expected_commit,
            expected_run_id,
            expected_run_attempt,
            expected_image_ids,
            evidence_manifest_bytes=data["evidence-manifest.json"],
            canonical_output_dir=Path(canonical_root)
            if canonical_root is not None
            else None,
        )
    if canonicalize_oci and transformation_receipt is None:
        raise ValueError("Canonical transformation receipt was not produced")
    signature._trusted_key(policy_path, policy_sha256, key_id)
    protected_inputs = [private_key, cosign, trivy, policy_path]
    parent_fd, destination_name, parent_path = _validate_signature_output(
        signature_dir, evidence_dir, protected_inputs
    )
    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="infrapulse-evidence-validated-"
            ) as snapshot_root,
            tempfile.TemporaryDirectory(
                prefix="infrapulse-signature-bundles-"
            ) as bundle_root,
        ):
            snapshot_paths = _snapshot_files(data, Path(snapshot_root))
            for name in COMPONENTS:
                image_id = (
                    expected_image_ids.get(name) if name.endswith("-image") else None
                )
                signature._validate_provenance(
                    snapshot_paths[f"{name}.provenance.json"],
                    snapshot_paths[f"{name}.cdx.json"],
                    snapshot_paths[f"{name}.manifest.json"],
                    trivy,
                    image_id,
                )
            bundles: dict[str, bytes] = {}
            bundle_manifest = Path(bundle_root) / "evidence-manifest.sigstore.json"
            signature.sign_blob_bytes(
                data["evidence-manifest.json"],
                bundle_manifest,
                private_key,
                cosign,
                protected_inputs=[*snapshot_paths.values()],
            )
            signature.verify_blob_bytes(
                data["evidence-manifest.json"],
                bundle_manifest.read_bytes(),
                policy_path,
                policy_sha256,
                key_id,
                cosign,
            )
            bundles[bundle_manifest.name] = bundle_manifest.read_bytes()
            for name in COMPONENTS:
                bundle = Path(bundle_root) / f"{name}.provenance.sigstore.json"
                statement_bytes = data[f"{name}.provenance.json"]
                signature.sign_blob_bytes(
                    statement_bytes,
                    bundle,
                    private_key,
                    cosign,
                    protected_inputs=[*snapshot_paths.values()],
                )
                signature.verify_blob_bytes(
                    statement_bytes,
                    bundle.read_bytes(),
                    policy_path,
                    policy_sha256,
                    key_id,
                    cosign,
                )
                bundles[bundle.name] = bundle.read_bytes()
            if transformation_receipt is not None:
                receipt_path = Path(bundle_root) / OCI_TRANSFORM_RECEIPT
                receipt_path.write_bytes(transformation_receipt)
                receipt_bundle = Path(bundle_root) / OCI_TRANSFORM_SIGNATURE
                signature.sign_blob_bytes(
                    transformation_receipt,
                    receipt_bundle,
                    private_key,
                    cosign,
                    protected_inputs=[*snapshot_paths.values()],
                )
                signature.verify_blob_bytes(
                    transformation_receipt,
                    receipt_bundle.read_bytes(),
                    policy_path,
                    policy_sha256,
                    key_id,
                    cosign,
                )
                bundles[OCI_TRANSFORM_RECEIPT] = transformation_receipt
                bundles[OCI_TRANSFORM_SIGNATURE] = receipt_bundle.read_bytes()
        _publish_bundle_directory(
            parent_fd,
            destination_name,
            bundles,
            parent_path,
            canonicalize_oci,
        )
        return {
            "status": "SIGNED",
            "integrity": "INTEGRITY_VERIFIED",
            "signature": "SIGNATURE_VALID",
            "signer": "TRUSTED_SIGNER",
            "commit": manifest["commit"],
            "workflow_run_id": manifest["workflow_run_id"],
            "run_attempt": manifest["run_attempt"],
            "bundles": sorted(
                name
                for name in _bundle_names(canonicalize_oci)
                if name.endswith(".sigstore.json")
            ),
            **(
                {
                    "canonicalization": "SIGNED_AND_REVALIDATED",
                    "transformation_receipt": OCI_TRANSFORM_RECEIPT,
                }
                if canonicalize_oci
                else {}
            ),
        }
    finally:
        os.close(parent_fd)


def verify_evidence(
    evidence_dir: Path,
    handoff_dir: Path,
    signature_dir: Path,
    cosign: Path,
    trivy: Path,
    policy_path: Path,
    policy_sha256: str,
    key_id: str,
    expected_commit: str,
    expected_run_id: str,
    expected_run_attempt: int,
    expected_repository: str,
    expected_image_ids: dict[str, str],
    canonicalize_oci: bool = False,
) -> dict[str, Any]:
    data, manifest = _load_and_validate(
        evidence_dir,
        expected_commit,
        expected_run_id,
        expected_run_attempt,
        expected_repository,
        expected_image_ids,
    )
    canonical_context = (
        tempfile.TemporaryDirectory(prefix="infrapulse-oci-verify-")
        if canonicalize_oci
        else nullcontext(None)
    )
    with canonical_context as canonical_root:
        transformation_receipt = _validate_handoff_archives(
            handoff_dir,
            manifest,
            expected_commit,
            expected_run_id,
            expected_run_attempt,
            expected_image_ids,
            evidence_manifest_bytes=data["evidence-manifest.json"],
            canonical_output_dir=Path(canonical_root)
            if canonical_root is not None
            else None,
        )
    if canonicalize_oci and transformation_receipt is None:
        raise ValueError("Canonical transformation receipt was not revalidated")
    bundles = _read_exact_directory(signature_dir, _bundle_names(canonicalize_oci))
    if (
        transformation_receipt is not None
        and bundles.get(OCI_TRANSFORM_RECEIPT) != transformation_receipt
    ):
        raise ValueError("Signed canonical transformation receipt mismatch")
    signature._trusted_key(policy_path, policy_sha256, key_id)
    if transformation_receipt is not None:
        signature.verify_blob_bytes(
            transformation_receipt,
            bundles[OCI_TRANSFORM_SIGNATURE],
            policy_path,
            policy_sha256,
            key_id,
            cosign,
        )
    signature.verify_blob_bytes(
        data["evidence-manifest.json"],
        bundles["evidence-manifest.sigstore.json"],
        policy_path,
        policy_sha256,
        key_id,
        cosign,
    )
    with tempfile.TemporaryDirectory(
        prefix="infrapulse-evidence-verify-"
    ) as snapshot_root:
        snapshot_root_path = Path(snapshot_root)
        snapshot_paths = _snapshot_files(data, snapshot_root_path)
        bundle_snapshot_root = snapshot_root_path / "bundles"
        bundle_snapshot_root.mkdir(mode=0o700)
        bundle_paths = _snapshot_files(bundles, bundle_snapshot_root)
        for name in COMPONENTS:
            signature.verify_signature(
                snapshot_paths[f"{name}.provenance.json"],
                bundle_paths[f"{name}.provenance.sigstore.json"],
                policy_path,
                policy_sha256,
                key_id,
                cosign,
                snapshot_paths[f"{name}.cdx.json"],
                snapshot_paths[f"{name}.manifest.json"],
                trivy,
                expected_image_ids.get(name) if name.endswith("-image") else None,
            )
    return {
        "status": "VERIFIED",
        "integrity": "INTEGRITY_VERIFIED",
        "signature": "SIGNATURE_VALID",
        "signer": "TRUSTED_SIGNER",
        "commit": manifest["commit"],
        "workflow_run_id": manifest["workflow_run_id"],
        "run_attempt": manifest["run_attempt"],
        "bundles": sorted(
            name
            for name in _bundle_names(canonicalize_oci)
            if name.endswith(".sigstore.json")
        ),
        **(
            {
                "canonicalization": "REVALIDATED_AND_SIGNATURE_VERIFIED",
                "transformation_receipt": OCI_TRANSFORM_RECEIPT,
            }
            if canonicalize_oci
            else {}
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("sign", "verify"):
        command = sub.add_parser(action)
        command.add_argument("--evidence-dir", type=Path, required=True)
        command.add_argument("--handoff-dir", type=Path, required=True)
        command.add_argument("--signature-dir", type=Path, required=True)
        command.add_argument("--cosign", type=Path, required=True)
        command.add_argument("--trivy", type=Path, required=True)
        command.add_argument("--policy", type=Path, required=True)
        command.add_argument("--policy-sha256", required=True)
        command.add_argument("--key-id", required=True)
        command.add_argument("--expected-commit", required=True)
        command.add_argument("--expected-run-id", required=True)
        command.add_argument("--expected-run-attempt", type=int, required=True)
        command.add_argument("--expected-repository", required=True)
        command.add_argument("--expected-backend-image-id", required=True)
        command.add_argument("--expected-nginx-image-id", required=True)
        command.add_argument(
            "--canonicalize-oci",
            action="store_true",
            help=(
                "explicitly canonicalize supported OCI handoff archives and "
                "sign the original-to-canonical transformation receipt"
            ),
        )
        if action == "sign":
            command.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()
    expected_image_ids = {
        "backend-image": args.expected_backend_image_id,
        "nginx-image": args.expected_nginx_image_id,
    }
    try:
        if args.action == "sign":
            result = sign_evidence(
                args.evidence_dir,
                args.handoff_dir,
                args.signature_dir,
                args.private_key,
                args.cosign,
                args.trivy,
                args.policy,
                args.policy_sha256,
                args.key_id,
                args.expected_commit,
                args.expected_run_id,
                args.expected_run_attempt,
                args.expected_repository,
                expected_image_ids,
                args.canonicalize_oci,
            )
        else:
            result = verify_evidence(
                args.evidence_dir,
                args.handoff_dir,
                args.signature_dir,
                args.cosign,
                args.trivy,
                args.policy,
                args.policy_sha256,
                args.key_id,
                args.expected_commit,
                args.expected_run_id,
                args.expected_run_attempt,
                args.expected_repository,
                expected_image_ids,
                args.canonicalize_oci,
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
        provenance.ValidationError,
        provenance.RefResolutionError,
    ) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
