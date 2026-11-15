"""Create a strict, independently identified OCI image archive copy."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path

import ci_evidence_signature as evidence

_IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")


@dataclass(frozen=True)
class CanonicalArchiveResult:
    source: Path
    canonical: Path
    image_id: str
    profile: str
    source_sha256: str
    source_bytes: int
    canonical_sha256: str
    canonical_bytes: int
    removed_blobs: tuple[str, ...]


def _hash_fd(fd: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not chunk:
            raise ValueError("Archive changed or ended while hashing")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Short write while creating canonical archive")
        view = view[written:]


def _copy_range(source_fd: int, destination_fd: int, start: int, size: int) -> None:
    offset = start
    remaining = size
    while remaining:
        chunk = os.pread(source_fd, min(1024 * 1024, remaining), offset)
        if not chunk:
            raise ValueError("Source archive changed while copying TAR records")
        _write_all(destination_fd, chunk)
        offset += len(chunk)
        remaining -= len(chunk)


def _record_hash(fd: int, start: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = start
    remaining = size
    while remaining:
        chunk = os.pread(fd, min(1024 * 1024, remaining), offset)
        if not chunk:
            raise ValueError("Short TAR record while checking preserved bytes")
        digest.update(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _private_directory(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError(
            "Canonical output directory must be private and operator-owned"
        )


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def canonicalize_oci_archive(
    source: Path,
    destination: Path,
    expected_image_id: str,
) -> CanonicalArchiveResult:
    """Validate and atomically publish an OCI archive with no unreferenced blobs.

    ``expected_image_id`` must come from an independent image inspection. Source
    and destination are never modified in place; the destination must not exist.
    """
    if not isinstance(expected_image_id, str) or not _IMAGE_ID.fullmatch(
        expected_image_id
    ):
        raise ValueError("Expected image ID must be a full SHA-256 identifier")

    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if (
        source.name in {"", ".", ".."}
        or destination.name in {"", ".", ".."}
        or source == destination
    ):
        raise ValueError("Source and destination must be distinct files")

    source_parent_path = source.parent.resolve(strict=True)
    destination_parent_path = destination.parent.resolve(strict=True)
    source = source_parent_path / source.name
    destination = destination_parent_path / destination.name
    if source == destination:
        raise ValueError("Source and destination must be distinct files")
    source_parent_fd = evidence._directory_fd(source_parent_path, require_trusted=True)
    try:
        destination_parent_fd = evidence._directory_fd(
            destination_parent_path, require_trusted=True
        )
    except BaseException:
        os.close(source_parent_fd)
        raise
    source_fd = -1
    temporary_fd = -1
    temporary_name: str | None = None
    published = False
    published_identity: tuple[int, int] | None = None
    try:
        _private_directory(destination_parent_fd)
        source_fd = os.open(
            source.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=source_parent_fd,
        )
        source_before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_before.st_mode)
            or source_before.st_size <= 0
            or source_before.st_size > evidence.MAX_ARCHIVE_BYTES
        ):
            raise ValueError("Source must be a bounded regular OCI archive")
        source_sha256 = _hash_fd(source_fd, source_before.st_size)

        reachable = evidence._verify_docker_archive(
            source_fd,
            expected_image_id,
            allow_legacy_auxiliary_blobs=True,
        )
        if not isinstance(reachable, set):
            raise TypeError("Canonical export accepts only supported OCI profiles")

        os.lseek(source_fd, 0, os.SEEK_SET)
        with (
            os.fdopen(os.dup(source_fd), "rb") as source_stream,
            tarfile.open(fileobj=source_stream, mode="r:") as archive,
        ):
            members = archive.getmembers()
            validated_headers = evidence._prevalidate_docker_tar(source_fd)
            parsed_headers = [
                (
                    member.name.rstrip("/") + "/" if member.isdir() else member.name,
                    member.size,
                    "directory" if member.isdir() else "file",
                )
                for member in members
            ]
            if parsed_headers != validated_headers:
                raise ValueError("TAR parser disagrees with validated source headers")
            member_map: dict[str, tarfile.TarInfo] = {}
            for member in members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                if name in member_map:
                    raise ValueError("Duplicate TAR member during canonical export")
                member_map[name] = member
            actual_files = {
                name for name, member in member_map.items() if member.isfile()
            }
            if not reachable <= actual_files:
                raise ValueError("Validated OCI member disappeared before export")
            removed_blobs = tuple(sorted(actual_files - reachable))
            if any(not name.startswith("blobs/sha256/") for name in removed_blobs):
                raise ValueError("Unexpected non-blob member outside OCI references")

            root_index = evidence._oci_object(
                evidence._read_tar_member(
                    archive,
                    member_map["index.json"],
                    evidence.MAX_IMAGE_CONFIG_BYTES,
                ),
                "OCI root index",
            )
            descriptor_media = root_index["manifests"][0]["mediaType"]
            if descriptor_media == evidence.OCI_MANIFEST_MEDIA_TYPE:
                profile = "oci-direct-manifest-linux-amd64"
            elif descriptor_media == evidence.OCI_INDEX_MEDIA_TYPE:
                profile = "oci-nested-index-linux-amd64"
            else:
                raise ValueError("Unsupported OCI root descriptor profile")

            block = evidence.TAR_BLOCK_BYTES
            record_end = max(
                member.offset + block + ((member.size + block - 1) // block) * block
                for member in members
            )
            archive_size = source_before.st_size
            trailer = os.pread(source_fd, archive_size - record_end, record_end)
            if len(trailer) < 2 * block or len(trailer) % block or any(trailer):
                raise ValueError("Source TAR terminator or trailing padding is invalid")

            destination_name = destination.name
            try:
                os.stat(
                    destination_name,
                    dir_fd=destination_parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("Canonical destination already exists")

            temporary_name = f".{destination_name}.{secrets.token_hex(16)}.tmp"
            temporary_fd = os.open(
                temporary_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=destination_parent_fd,
            )
            os.fchmod(temporary_fd, 0o600)
            preserved_records: list[tuple[int, int, int]] = []
            output_offset = 0
            for member in members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                keep = member.isdir() or name in reachable
                record_size = block + ((member.size + block - 1) // block) * block
                if keep:
                    _copy_range(source_fd, temporary_fd, member.offset, record_size)
                    preserved_records.append(
                        (member.offset, output_offset, record_size)
                    )
                    output_offset += record_size
            _write_all(temporary_fd, trailer)
            os.fsync(temporary_fd)

        source_after = os.fstat(source_fd)
        source_path_after = os.stat(
            source.name, dir_fd=source_parent_fd, follow_symlinks=False
        )
        if (
            _path_identity(source_before) != _path_identity(source_after)
            or (source_after.st_dev, source_after.st_ino)
            != (source_path_after.st_dev, source_path_after.st_ino)
            or _hash_fd(source_fd, source_after.st_size) != source_sha256
        ):
            raise ValueError("Source archive changed during canonicalization")

        temporary_stat = os.fstat(temporary_fd)
        if (
            not stat.S_ISREG(temporary_stat.st_mode)
            or temporary_stat.st_mode & 0o077
            or temporary_stat.st_size
            != archive_size
            - sum(
                block + ((member.size + block - 1) // block) * block
                for member in members
                if not member.isdir() and member.name not in reachable
            )
        ):
            raise ValueError("Canonical archive size or permissions are invalid")
        for source_offset, output_offset, size in preserved_records:
            if _record_hash(source_fd, source_offset, size) != _record_hash(
                temporary_fd, output_offset, size
            ):
                raise ValueError(
                    "A retained TAR record changed during canonicalization"
                )

        canonical_sha256 = _hash_fd(temporary_fd, temporary_stat.st_size)
        strict_reachable = evidence._verify_docker_archive(
            temporary_fd, expected_image_id
        )
        if strict_reachable != reachable:
            raise ValueError(
                "Canonical archive inventory differs from validated source"
            )

        evidence._verify_output_parent(destination_parent_fd, destination_parent_path)
        published_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        evidence._rename_noreplace(
            destination_parent_fd, temporary_name, destination_name
        )
        published = True
        final_stat = os.stat(
            destination_name,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(final_stat.st_mode) or (
            final_stat.st_dev,
            final_stat.st_ino,
        ) != (temporary_stat.st_dev, temporary_stat.st_ino):
            raise ValueError("Published canonical archive identity changed")
        os.fsync(destination_parent_fd)
        return CanonicalArchiveResult(
            source=source,
            canonical=destination,
            image_id=expected_image_id,
            profile=profile,
            source_sha256=source_sha256,
            source_bytes=source_before.st_size,
            canonical_sha256=canonical_sha256,
            canonical_bytes=temporary_stat.st_size,
            removed_blobs=removed_blobs,
        )
    except BaseException:
        if published and published_identity is not None:
            try:
                current = os.stat(
                    destination.name,
                    dir_fd=destination_parent_fd,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == published_identity:
                    os.unlink(destination.name, dir_fd=destination_parent_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if temporary_name is not None and not published:
            try:
                os.unlink(temporary_name, dir_fd=destination_parent_fd)
            except FileNotFoundError:
                pass
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if source_fd >= 0:
            os.close(source_fd)
        os.close(destination_parent_fd)
        os.close(source_parent_fd)
