"""Bounded virtual reader for independently identified uncompressed OCI layers.

The caller must verify the manifest and ordered descriptors independently and
allocate layer bytes before calling. Limits cannot prevent that allocation.
This module never extracts entries or asserts coverage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

BLOCK = 512
MAX_LAYERS = 64
MAX_LAYER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_PAX_BYTES = 1024 * 1024
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
    content_sha256: str | None = None
    content_offset: int | None = field(default=None, compare=False, repr=False)


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


def _path(value: str, *, allow_whiteout: bool = False) -> str:
    if not value:
        raise ValueError("Invalid path length")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid path encoding") from exc
    if len(encoded) > MAX_PATH_BYTES:
        raise ValueError("Invalid path length")
    if any(
        ord(char) < 32 or 0x7F <= ord(char) <= 0x9F or char == "\\"
        for char in value
    ):
        raise ValueError("Unsupported path characters")
    parts = value.split("/")
    if len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Path must be normalized and relative")
    if not allow_whiteout and any(part.startswith(".wh.") for part in parts):
        raise ValueError("Whiteouts are not supported")
    return value


def _symlink_target(value: str) -> str:
    """Validate a link value without resolving it or treating it as a host path."""
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid symlink target encoding") from exc
    if (
        not encoded
        or len(encoded) > 4096
        or any(
            ord(char) < 32 or 0x7F <= ord(char) <= 0x9F or char == "\\"
            for char in value
        )
    ):
        raise ValueError("Invalid symlink target")
    return value


def _pax_records(payload: bytes) -> dict[str, str]:
    """Parse the restricted local PAX path metadata used by observed images."""
    if not payload or len(payload) > MAX_PAX_BYTES:
        raise ValueError("Invalid PAX metadata size")
    result: dict[str, str] = {}
    offset = 0
    while offset < len(payload):
        separator = payload.find(b" ", offset)
        if separator <= offset or separator - offset > 10:
            raise ValueError("Invalid PAX record length")
        length_bytes = payload[offset:separator]
        if not length_bytes.isdigit():
            raise ValueError("Invalid PAX record length")
        length = int(length_bytes)
        end = offset + length
        if length <= separator - offset + 3 or end > len(payload):
            raise ValueError("Invalid PAX record bounds")
        record = payload[separator + 1 : end]
        if not record.endswith(b"\n") or record.count(b"=") < 1:
            raise ValueError("Malformed PAX record")
        key_bytes, value_bytes = record[:-1].split(b"=", 1)
        if key_bytes not in {b"path", b"linkpath"} or not value_bytes:
            raise ValueError("Unsupported PAX metadata")
        try:
            key = key_bytes.decode("ascii")
            value = value_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid PAX metadata encoding") from exc
        if key in result:
            raise ValueError("Duplicate PAX metadata key")
        result[key] = value
        offset = end
    return result


def _read_layer(
    data: bytes, entry_budget: int, *, rootfs: bool = False
) -> tuple[Entry, ...]:
    if len(data) < BLOCK * 2 or len(data) % BLOCK:
        raise ValueError("Invalid TAR length")
    entries: list[Entry] = []
    paths: dict[str, str] = {}
    parents: set[str] = set()
    offset = 0
    pax: dict[str, str] | None = None
    while offset + BLOCK <= len(data):
        header = data[offset : offset + BLOCK]
        if not any(header):
            if offset + BLOCK * 2 > len(data) or any(data[offset:]):
                raise ValueError("Invalid TAR terminator")
            if pax is not None:
                raise ValueError("Dangling PAX metadata")
            return tuple(entries)
        if len(entries) >= entry_budget:
            raise ValueError("Total entry limit exceeded")
        ustar = header[257:263] == b"ustar\0" and header[263:265] == b"00"
        gnu = header[257:263] == b"ustar " and header[263:265] == b" \0"
        if not (ustar or gnu):
            raise ValueError("Unsupported TAR header profile")
        if _number(header[148:156]) != sum(header[:148]) + 256 + sum(header[156:]):
            raise ValueError("Invalid TAR header checksum")
        tar_type = header[156:157]
        size = _number(header[124:136])
        if tar_type == b"x":
            if pax is not None or size > MAX_PAX_BYTES:
                raise ValueError("Nested or oversized PAX metadata")
            pax_name = _text(header[:100])
            if ustar:
                pax_prefix = _text(header[345:500])
                pax_name = f"{pax_prefix}/{pax_name}" if pax_prefix else pax_name
            if gnu and any(header[345:500]):
                raise ValueError("Unsupported GNU TAR extension metadata")
            if pax_name != "././@PaxHeader":
                _path(pax_name)
            pax_mode = _number(header[100:108])
            pax_uid = _number(header[108:116])
            pax_gid = _number(header[116:124])
            _number(header[136:148])
            _text(header[157:257])
            _text(header[265:297])
            _text(header[297:329])
            if (
                pax_mode > 0o7777
                or pax_uid > 2**32 - 1
                or pax_gid > 2**32 - 1
                or _device_number(header[329:337])
                or _device_number(header[337:345])
                or any(header[500:512])
            ):
                raise ValueError("Unsupported PAX TAR metadata")
            end = offset + BLOCK + ((size + BLOCK - 1) // BLOCK) * BLOCK
            if end > len(data) or any(data[offset + BLOCK + size : end]):
                raise ValueError("Truncated PAX metadata or invalid padding")
            pax = _pax_records(data[offset + BLOCK : offset + BLOCK + size])
            offset = end
            continue
        if tar_type in {b"g", b"L", b"K", b"S"}:
            raise ValueError("Unsupported TAR extension type")
        kind = {
            b"0": "file",
            b"\0": "file",
            b"5": "directory",
            b"2": "symlink",
            b"1": "hardlink",
        }.get(tar_type)
        if kind is None:
            raise ValueError("Unsupported TAR entry type")
        name = _text(header[:100])
        prefix = _text(header[345:500]) if ustar else ""
        if gnu and any(header[345:500]):
            raise ValueError("Unsupported GNU TAR extension metadata")
        name = f"{prefix}/{name}" if prefix else name
        if pax and "path" in pax:
            name = pax["path"]
        if kind == "directory" and name.endswith("/"):
            name = name[:-1]
        if name != ".":
            name = _path(name, allow_whiteout=rootfs)
        components = name.split("/") if name != "." else ["."]
        if name != "." and any(
            part.startswith(".wh.") for part in components[:-1]
        ):
            raise ValueError("Whiteout in parent path")
        if not rootfs and kind == "hardlink":
            raise ValueError("Unsupported TAR entry type")
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
        if pax and "linkpath" in pax:
            link = pax["linkpath"]
        if kind in {"symlink", "hardlink"}:
            if kind == "symlink":
                link = _symlink_target(link)
            else:
                link = _path(link)
        elif link:
            raise ValueError("Unexpected link target")
        if name == ".":
            if kind != "directory" or size:
                raise ValueError("Invalid TAR root directory entry")
            end = offset + BLOCK + ((size + BLOCK - 1) // BLOCK) * BLOCK
            if end > len(data) or any(data[offset + BLOCK + size : end]):
                raise ValueError("Truncated TAR member or invalid padding")
            offset = end
            pax = None
            continue
        if components[-1].startswith(".wh.") and (
            kind != "file" or size or components[-1] in {".wh.", ".wh..", ".wh..."}
        ):
            raise ValueError("Invalid whiteout")
        if kind != "file" and size:
            raise ValueError("Non-file entry has content")
        parent_paths = {
            p.as_posix() for p in PurePosixPath(name).parents if p.as_posix() != "."
        }
        if name in paths or any(
            paths.get(p) in {"file", "symlink", "hardlink"} for p in parent_paths
        ):
            raise ValueError("Duplicate path or non-directory parent")
        if kind != "directory" and name in parents:
            raise ValueError("Entry collides with a parent directory")
        paths[name] = kind
        parents.update(parent_paths)
        end = offset + BLOCK + ((size + BLOCK - 1) // BLOCK) * BLOCK
        if end > len(data) or any(data[offset + BLOCK + size : end]):
            raise ValueError("Truncated TAR member or invalid padding")
        content_hash = (
            hashlib.sha256(data[offset + BLOCK : offset + BLOCK + size]).hexdigest()
            if kind == "file"
            else None
        )
        entries.append(
            Entry(
                name,
                kind,
                size,
                mode,
                uid,
                gid,
                mtime,
                link or None,
                content_hash,
                offset + BLOCK if kind == "file" else None,
            )
        )
        offset = end
        pax = None
    raise ValueError("Missing TAR terminator")


def read_layers(
    layers: list[Layer], *, rootfs: bool = False
) -> tuple[tuple[Entry, ...], ...]:
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
        entries = _read_layer(layer.data, MAX_ENTRIES - entry_count, rootfs=rootfs)
        entry_count += len(entries)
        result.append(entries)
    return tuple(result)


@dataclass(frozen=True)
class RootEntry:
    entry: Entry
    layer: int
    # Hardlinks retain the referenced inode even if its pathname is replaced later.
    content: tuple[int, str] | None


def reconstruct_rootfs(layers: list[Layer]) -> dict[str, RootEntry]:
    """Apply verified layers in memory; never follow a symlink on the host.

    Implicit directories are permitted, but traversing a non-directory is rejected.
    Hardlinks must resolve to an already available regular file; forward references
    and links through symlinks are rejected. Coverage is not established here.
    """
    return _reconstruct_groups(read_layers(layers, rootfs=True))


def _reconstruct_groups(
    groups: tuple[tuple[Entry, ...], ...],
) -> dict[str, RootEntry]:
    tree: dict[str, RootEntry] = {}

    def remove(path: str, *, children_only: bool = False) -> None:
        for existing in tuple(tree):
            if existing.startswith(path + "/") or (
                not children_only and existing == path
            ):
                del tree[existing]

    for index, entries in enumerate(groups):
        # Validate every whiteout against the same lower-layer snapshot.
        lower = tree.copy()
        for entry in entries:
            parent, _, base = entry.path.rpartition("/")
            if not base.startswith(".wh."):
                continue
            for part in PurePosixPath(parent).parents if parent else ():
                name = part.as_posix()
                if (
                    name != "."
                    and name in lower
                    and lower[name].entry.kind != "directory"
                ):
                    raise ValueError("Whiteout traverses non-directory")
            if parent and parent in lower and lower[parent].entry.kind != "directory":
                raise ValueError("Whiteout traverses non-directory")
        # Apply whiteouts only after all parent checks; they affect lower layers.
        for entry in entries:
            parent, _, base = entry.path.rpartition("/")
            if not base.startswith(".wh."):
                continue
            if base == ".wh..wh..opq":
                if parent:
                    remove(parent, children_only=True)
                else:
                    tree.clear()
            else:
                target = base[4:]
                if target.startswith(".wh.") or target in {"", ".", ".."}:
                    raise ValueError("Invalid whiteout target")
                remove(f"{parent}/{target}" if parent else target)
        for entry in entries:
            if entry.path.rpartition("/")[2].startswith(".wh."):
                continue
            parents = [
                p.as_posix()
                for p in PurePosixPath(entry.path).parents
                if p.as_posix() != "."
            ]
            if any(p in tree and tree[p].entry.kind != "directory" for p in parents):
                raise ValueError("Entry traverses non-directory")
            content = (index, entry.path) if entry.kind == "file" else None
            if entry.kind == "hardlink":
                target = entry.link_target
                assert target is not None
                target_parents = [
                    p.as_posix()
                    for p in PurePosixPath(target).parents
                    if p.as_posix() != "."
                ]
                if any(
                    p in tree and tree[p].entry.kind != "directory"
                    for p in target_parents
                ):
                    raise ValueError("Hardlink traverses non-directory")
                if (
                    target == entry.path
                    or target not in tree
                    or tree[target].content is None
                ):
                    raise ValueError("Invalid hardlink target")
                content = tree[target].content
            old = tree.get(entry.path)
            if entry.kind != "directory" or (old and old.entry.kind != "directory"):
                remove(entry.path)
            tree[entry.path] = RootEntry(entry, index, content)
    return dict(sorted(tree.items()))
