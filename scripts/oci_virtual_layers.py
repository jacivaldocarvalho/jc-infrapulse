"""Bounded virtual reader for independently identified uncompressed OCI layers.

The caller must verify the manifest and ordered descriptors independently and
allocate layer bytes before calling. Limits cannot prevent that allocation.
This module never extracts entries, reconstructs a rootfs, or asserts coverage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

BLOCK = 512
MAX_LAYERS = 64
MAX_LAYER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_PATH_BYTES = 255
MAX_DEPTH = 32
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Layer:
    data: bytes
    digest: str
    diff_id: str
    size: int


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    size: int
    mode: int
    uid: int
    gid: int
    mtime: int
    link_target: str | None


def _text(field: bytes) -> str:
    value, separator, padding = field.partition(b"\0")
    if separator and any(padding):
        raise ValueError("Invalid TAR string padding")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Unsupported TAR text encoding") from exc


def _number(field: bytes) -> int:
    if not re.fullmatch(rb" *[0-7]+(?:\x00 *| *)", field):
        raise ValueError("Invalid TAR numeric field")
    return int(field.rstrip(b" \0").lstrip(b" "), 8)


def _device_number(field: bytes) -> int:
    # USTAR leaves unused device fields entirely NUL; no digits follow the NUL.
    return 0 if not any(field) else _number(field)


def _path(value: str) -> str:
    if not value or len(value.encode("ascii")) > MAX_PATH_BYTES:
        raise ValueError("Invalid path length")
    if any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in value):
        raise ValueError("Unsupported path characters")
    parts = value.split("/")
    if len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Path must be normalized and relative")
    if any(part.startswith(".wh.") for part in parts):
        raise ValueError("Whiteouts are not supported")
    return value


def _read_layer(data: bytes, entry_budget: int) -> tuple[Entry, ...]:
    if len(data) < BLOCK * 2 or len(data) % BLOCK:
        raise ValueError("Invalid TAR length")
    entries: list[Entry] = []
    paths: dict[str, str] = {}
    parents: set[str] = set()
    offset = 0
    while offset + BLOCK <= len(data):
        header = data[offset : offset + BLOCK]
        if not any(header):
            if offset + BLOCK * 2 > len(data) or any(data[offset:]):
                raise ValueError("Invalid TAR terminator")
            return tuple(entries)
        if len(entries) >= entry_budget:
            raise ValueError("Total entry limit exceeded")
        if header[257:263] != b"ustar\0" or header[263:265] != b"00":
            raise ValueError("Only USTAR layers are supported")
        if _number(header[148:156]) != sum(header[:148]) + 256 + sum(header[156:]):
            raise ValueError("Invalid TAR header checksum")
        kind = {b"0": "file", b"\0": "file", b"5": "directory", b"2": "symlink"}.get(
            header[156:157]
        )
        if kind is None:
            raise ValueError("Unsupported TAR entry type")
        name = _text(header[:100])
        prefix = _text(header[345:500])
        name = f"{prefix}/{name}" if prefix else name
        if kind == "directory" and name.endswith("/"):
            name = name[:-1]
        name = _path(name)
        size = _number(header[124:136])
        mode = _number(header[100:108])
        uid = _number(header[108:116])
        gid = _number(header[116:124])
        mtime = _number(header[136:148])
        _text(header[265:297])
        _text(header[297:329])
        if (
            mode > 0o7777
            or uid > 2**32 - 1
            or gid > 2**32 - 1
            or _device_number(header[329:337])
            or _device_number(header[337:345])
            or any(header[500:512])
        ):
            raise ValueError("Unsupported TAR metadata")
        link = _text(header[157:257])
        if kind == "symlink":
            link = _path(link)
        elif link:
            raise ValueError("Unexpected link target")
        if kind != "file" and size:
            raise ValueError("Non-file entry has content")
        parent_paths = {
            p.as_posix() for p in PurePosixPath(name).parents if p.as_posix() != "."
        }
        if name in paths or any(
            paths.get(p) in {"file", "symlink"} for p in parent_paths
        ):
            raise ValueError("Duplicate path or non-directory parent")
        if kind != "directory" and name in parents:
            raise ValueError("Entry collides with a parent directory")
        paths[name] = kind
        parents.update(parent_paths)
        end = offset + BLOCK + ((size + BLOCK - 1) // BLOCK) * BLOCK
        if end > len(data) or any(data[offset + BLOCK + size : end]):
            raise ValueError("Truncated TAR member or invalid padding")
        entries.append(Entry(name, kind, size, mode, uid, gid, mtime, link or None))
        offset = end
    raise ValueError("Missing TAR terminator")


def read_layers(layers: list[Layer]) -> tuple[tuple[Entry, ...], ...]:
    """Validate ordered raw layers against caller-supplied digests and sizes."""
    if not layers or len(layers) > MAX_LAYERS:
        raise ValueError("Invalid layer count")
    total = 0
    entry_count = 0
    result = []
    for layer in layers:
        if not isinstance(layer, Layer) or not isinstance(layer.data, bytes):
            raise TypeError("Layer bytes and descriptor required")
        size = len(layer.data)
        total += size
        if size > MAX_LAYER_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Layer byte limit exceeded")
        if type(layer.size) is not int or layer.size != size:
            raise ValueError("Layer descriptor size mismatch")
        if not SHA256.fullmatch(layer.digest) or not SHA256.fullmatch(layer.diff_id):
            raise ValueError("Invalid digest")
        digest = "sha256:" + hashlib.sha256(layer.data).hexdigest()
        if digest != layer.digest or digest != layer.diff_id:
            raise ValueError("Layer digest or DiffID mismatch")
        entries = _read_layer(layer.data, MAX_ENTRIES - entry_count)
        entry_count += len(entries)
        result.append(entries)
    return tuple(result)
