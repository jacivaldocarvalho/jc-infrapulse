"""Deterministic inventory of a bounded, independently identified OCI rootfs."""

from __future__ import annotations

from dataclasses import dataclass

from oci_virtual_layers import Layer, _reconstruct_groups, read_layers


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    kind: str
    size: int
    mode: int
    uid: int
    gid: int
    link_target: str | None
    layer: int
    content_layer: int | None
    content_path: str | None
    sha256: str | None


def inventory(layers: list[Layer]) -> tuple[InventoryEntry, ...]:
    """Inventory explicit entries without disclosing contents or claiming coverage.

    The caller must independently verify the manifest, descriptors and layer order.
    Hardlink size, ownership and mode refer to the linked regular-file inode;
    layer and link_target describe the hardlink entry itself. Implicit parent
    directories have no TAR metadata and are not reported.
    """
    groups = read_layers(layers, rootfs=True)
    tree = _reconstruct_groups(groups)
    source = {
        (index, entry.path): entry
        for index, entries in enumerate(groups)
        for entry in entries
    }
    result = []
    for path, item in tree.items():
        entry = item.entry
        content_layer = None
        content_path = None
        content_hash = None
        metadata = entry
        if item.content is not None:
            content_layer, content_path = item.content
            metadata = source[item.content]
            if metadata.kind != "file" or metadata.content_sha256 is None:
                raise ValueError("Invalid regular-file content reference")
            content_hash = metadata.content_sha256
        result.append(
            InventoryEntry(
                path=path,
                kind=entry.kind,
                size=metadata.size,
                mode=metadata.mode,
                uid=metadata.uid,
                gid=metadata.gid,
                link_target=entry.link_target,
                layer=item.layer,
                content_layer=content_layer,
                content_path=content_path,
                sha256=content_hash,
            )
        )
    return tuple(result)
