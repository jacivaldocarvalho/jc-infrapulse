"""Restricted package adapters; callers authenticate source and image scope."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote

import provenance
import sbom
from oci_package_reconciliation import IMAGE_ID, Inventory, Package
from oci_virtual_layers import Layer, _reconstruct_groups, read_layers

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 10000
DEB_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
ARCH = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
VERSION = re.compile(r"[0-9][A-Za-z0-9.+:~\-]*\Z")
APK_VERSION = re.compile(r"[0-9][A-Za-z0-9.+_~\-]*\Z")
DPKG_STATUS = "var/lib/dpkg/status"
APK_INSTALLED = "lib/apk/db/installed"
APK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*\Z")
APK_ARCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _verified(raw: bytes, expected_sha256: str) -> str:
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("Invalid or oversized input")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected SHA-256 required")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Input digest mismatch")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Input is not UTF-8") from exc


def debian_status(
    raw: bytes, *, expected_sha256: str, image_id: str, platform: str
) -> Inventory:
    """Read a bounded dpkg status file; caller binds its hash to verified rootfs."""
    text = _verified(raw, expected_sha256)
    if "\r" in text or "\x00" in text:
        raise ValueError("Invalid status encoding")
    records = re.split(r"\n[ \t]*\n", text.strip()) if text.strip() else []
    if len(records) > MAX_RECORDS:
        raise ValueError("Too many records")
    packages: list[Package] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        fields: dict[str, str] = {}
        current = ""
        for line in record.splitlines():
            if line.startswith((" ", "\t")):
                if not current or current in {
                    "package",
                    "status",
                    "version",
                    "architecture",
                }:
                    raise ValueError("Invalid continuation")
                continue
            key, separator, value = line.partition(":")
            if (
                not separator
                or not re.fullmatch(r"[A-Za-z0-9-]+", key)
                or key.lower() in fields
            ):
                raise ValueError("Invalid or duplicate status field")
            current = key.lower()
            fields[current] = value.strip()
        name, status = fields.get("package", ""), fields.get("status", "")
        version, arch = fields.get("version", ""), fields.get("architecture", "")
        if not DEB_NAME.fullmatch(name) or not status:
            raise ValueError("Missing or invalid package status")
        parts = status.split()
        if (
            len(parts) != 3
            or parts[0] not in {"unknown", "install", "hold", "deinstall", "purge"}
            or parts[1] not in {"ok", "reinstreq"}
            or parts[2]
            not in {
                "not-installed",
                "config-files",
                "half-installed",
                "unpacked",
                "half-configured",
                "triggers-awaited",
                "triggers-pending",
                "installed",
            }
        ):
            raise ValueError("Invalid dpkg status")
        if parts[2] != "installed":
            continue
        if not VERSION.fullmatch(version) or not ARCH.fullmatch(arch):
            raise ValueError("Missing installed version or architecture")
        key = (name, arch)
        if key in seen:
            raise ValueError("Duplicate installed package")
        seen.add(key)
        packages.append(Package("deb", name, version, "debian", arch))
    return Inventory(
        image_id,
        platform,
        "dpkg-status",
        tuple(packages),
        ("dpkg-status covers installed Debian packages only",),
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def trivy_cyclonedx(
    raw: bytes,
    *,
    expected_sha256: str,
    image_id: str,
    platform: str,
    image_name: str,
    expected_distro: str,
) -> Inventory:
    """Validate pinned CycloneDX structure and independently supplied root identity."""
    text = _verified(raw, expected_sha256)
    document = json.loads(text, object_pairs_hook=_unique)
    if not isinstance(document, dict) or document.get("bomFormat") != "CycloneDX":
        raise ValueError("CycloneDX document required")
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise ValueError("Independent image ID required")
    if not isinstance(expected_distro, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,100}", expected_distro
    ):
        raise ValueError("Independent distro identity required")
    if image_name not in {"backend-image", "nginx-image"}:
        raise ValueError("Independent logical image name required")
    with tempfile.TemporaryDirectory(prefix="infrapulse-sbom-validate-") as directory:
        path = Path(directory) / "sbom.json"
        path.write_bytes(raw)
        checked = sbom.validate(path, expected_sha256)
    if checked != document:
        raise ValueError("SBOM parser disagreement")
    provenance.normalized_inventory(
        document, image_id, image_name, "package-reconciliation"
    )
    root = document["metadata"]["component"]
    if root["purl"].split("?", 1)[0] != f"pkg:oci/{image_name}@{image_id}":
        raise ValueError("Logical image name differs from SBOM root")
    components = document.get("components")
    if not isinstance(components, list) or len(components) > MAX_RECORDS:
        raise ValueError("Invalid components")
    packages: list[Package] = []
    unknown: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise TypeError("Invalid component")
        purl = component.get("purl")
        if not isinstance(purl, str) or len(purl) > 1024:
            unknown.add("component without supported Debian, Alpine, PyPI or npm purl")
            continue
        base, separator, query = purl.partition("?")
        qualifiers: dict[str, str] = {}
        if separator:
            try:
                for key, value in parse_qsl(
                    query, keep_blank_values=True, strict_parsing=True
                ):
                    if key in qualifiers or not value:
                        raise ValueError("Ambiguous purl qualifier")
                    qualifiers[key] = value
            except ValueError as exc:
                raise ValueError("Malformed purl qualifiers") from exc
        match = re.fullmatch(
            r"pkg:(deb/debian|apk/alpine)/([A-Za-z0-9][A-Za-z0-9+_.-]*)@([^?#]+)", base
        )
        python_match = re.fullmatch(
            r"pkg:pypi/([A-Za-z0-9][A-Za-z0-9._-]*)@([^?#]+)", base
        )
        npm_match = re.fullmatch(
            r"pkg:npm/(?:(?:%40|@)([a-z0-9][a-z0-9._-]*)/)?([a-z0-9][a-z0-9._-]*)@([^?#]+)",
            base,
        )
        if npm_match and not qualifiers:
            scope, name, encoded_version = npm_match.groups()
            ecosystem, namespace, arch = "npm", scope or "", ""
        elif python_match and not qualifiers:
            name, encoded_version = python_match.groups()
            ecosystem, namespace, arch = "pypi", "", ""
            name = re.sub(r"[-_.]+", "-", name).lower()
        elif match and set(qualifiers) in (
            {"arch"},
            {"arch", "distro"},
            {"arch", "distro", "epoch"},
        ):
            kind, name, encoded_version = match.groups()
            ecosystem, namespace = kind.split("/")
            arch = qualifiers["arch"]
            if not APK_ARCH.fullmatch(arch) or (
                ecosystem == "deb" and not ARCH.fullmatch(arch)
            ):
                raise ValueError("Invalid package architecture")
            if "epoch" in qualifiers and (
                ecosystem != "deb" or not qualifiers["epoch"].isdigit()
            ):
                raise ValueError("Invalid epoch")
            # Distro is validated separately against an independently supplied value.
            if "distro" not in qualifiers:
                unknown.add("component lacks distro qualifier")
            if "distro" in qualifiers and qualifiers["distro"] != expected_distro:
                unknown.add(
                    "component distro differs from independently expected distro"
                )
                continue
        else:
            unknown.add("component without supported Debian, Alpine, PyPI or npm purl")
            continue
        if re.search(r"%(?![0-9A-Fa-f]{2})", encoded_version):
            raise ValueError("Invalid purl encoding")
        try:
            version = unquote(encoded_version, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid purl version") from exc
        version_pattern = APK_VERSION if ecosystem == "apk" else VERSION
        if not version_pattern.fullmatch(version):
            raise ValueError("Invalid purl version")
        if "epoch" in qualifiers:
            if ":" in version:
                raise ValueError("Ambiguous package epoch")
            version = qualifiers["epoch"] + ":" + version
        packages.append(Package(ecosystem, name, version, namespace, arch))
    return Inventory(
        image_id, platform, "trivy-cyclonedx", tuple(packages), tuple(sorted(unknown))
    )


def _file_from_layers(layers: list[Layer], path: str) -> bytes:
    """Read a regular virtual-rootfs file; never follow host symlinks."""
    groups = read_layers(layers, rootfs=True)
    tree = _reconstruct_groups(groups)
    item = tree.get(path)
    if (
        item is None
        or item.entry.kind not in {"file", "hardlink"}
        or item.content is None
    ):
        raise ValueError("A regular dpkg status file is required")
    layer_index, content_path = item.content
    source = next(
        (entry for entry in groups[layer_index] if entry.path == content_path), None
    )
    if (
        source is None
        or source.kind != "file"
        or source.content_offset is None
        or source.content_sha256 is None
        or source.size > MAX_BYTES
    ):
        raise ValueError("Invalid dpkg content reference")
    raw = layers[layer_index].data[
        source.content_offset : source.content_offset + source.size
    ]
    if hashlib.sha256(raw).hexdigest() != source.content_sha256:
        raise ValueError("Virtual rootfs content mismatch")
    return raw


def debian_from_layers(
    layers: list[Layer], *, image_id: str, platform: str
) -> Inventory:
    """Read dpkg status from verified layers; caller validates image identity."""
    raw = _file_from_layers(layers, DPKG_STATUS)
    return debian_status(
        raw,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        image_id=image_id,
        platform=platform,
    )


def alpine_installed(
    raw: bytes, *, expected_sha256: str, image_id: str, platform: str
) -> Inventory:
    """Read bounded apk installed records; unsupported fields do not imply coverage."""
    text = _verified(raw, expected_sha256)
    if "\r" in text or "\x00" in text:
        raise ValueError("Invalid apk encoding")
    records = text.strip().split("\n\n") if text.strip() else []
    if len(records) > MAX_RECORDS:
        raise ValueError("Too many records")
    packages: list[Package] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        fields: dict[str, str] = {}
        for line in record.splitlines():
            key, separator, value = line.partition(":")
            if not separator or len(key) != 1 or not key.isascii() or not key.isalpha():
                raise ValueError("Invalid apk field")
            if key in {"P", "V", "A"}:
                if key in fields:
                    raise ValueError("Duplicate apk identity field")
                fields[key] = value
        name, version, arch = (fields.get(key, "") for key in ("P", "V", "A"))
        if (
            not APK_NAME.fullmatch(name)
            or not APK_VERSION.fullmatch(version)
            or not APK_ARCH.fullmatch(arch)
        ):
            raise ValueError("Invalid apk package identity")
        if (name, arch) in seen:
            raise ValueError("Duplicate installed apk package")
        seen.add((name, arch))
        packages.append(Package("apk", name, version, "alpine", arch))
    return Inventory(
        image_id,
        platform,
        "apk-installed",
        tuple(packages),
        ("apk-installed covers Alpine OS packages only",),
    )


def alpine_from_layers(
    layers: list[Layer], *, image_id: str, platform: str
) -> Inventory:
    """Read the apk installed database from validated OCI layers."""
    raw = _file_from_layers(layers, APK_INSTALLED)
    return alpine_installed(
        raw,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        image_id=image_id,
        platform=platform,
    )


def python_from_layers(
    layers: list[Layer], *, image_id: str, platform: str
) -> Inventory:
    """Inventory explicit Python dist-info METADATA files from a virtual rootfs."""
    groups = read_layers(layers, rootfs=True)
    tree = _reconstruct_groups(groups)
    sources = {
        (index, entry.path): entry
        for index, entries in enumerate(groups)
        for entry in entries
    }
    packages: list[Package] = []
    unknown = {"python METADATA covers explicit dist-info files only"}
    for path, item in tree.items():
        if not re.fullmatch(r".+/site-packages/[^/]+\.dist-info/METADATA", path):
            continue
        if item.entry.kind not in {"file", "hardlink"} or item.content is None:
            raise ValueError("Invalid METADATA file type")
        layer_index, _ = item.content
        entry = sources[item.content]
        if (
            entry.content_offset is None
            or entry.content_sha256 is None
            or entry.size > MAX_BYTES
        ):
            raise ValueError("Invalid METADATA source")
        data = layers[layer_index].data[
            entry.content_offset : entry.content_offset + entry.size
        ]
        text = _verified(data, entry.content_sha256)
        header, separator, _ = text.partition("\n\n")
        if not separator or "\r" in header or "\x00" in header:
            raise ValueError("Malformed Python METADATA")
        fields: dict[str, str] = {}
        current = ""
        for line in header.split("\n"):
            if line.startswith((" ", "\t")):
                if not current or current in {"name", "version"}:
                    raise ValueError("Invalid Python metadata continuation")
                continue
            key, sep, value = line.partition(":")
            if not sep:
                raise ValueError("Malformed Python metadata header")
            key = key.lower()
            current = key
            if key in {"name", "version"}:
                if key in fields:
                    raise ValueError("Duplicate Python metadata identity")
                fields[key] = value.strip()
        name, version = fields.get("name", ""), fields.get("version", "")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*", name
        ) or not VERSION.fullmatch(version):
            raise ValueError("Unsupported Python metadata identity")
        packages.append(Package("pypi", re.sub(r"[-_.]+", "-", name).lower(), version))
    return Inventory(
        image_id, platform, "python-dist-info", tuple(packages), tuple(sorted(unknown))
    )


def npm_from_layers(layers: list[Layer], *, image_id: str, platform: str) -> Inventory:
    """Read explicit node_modules package.json files from validated virtual layers."""
    groups = read_layers(layers, rootfs=True)
    tree = _reconstruct_groups(groups)
    sources = {
        (index, entry.path): entry
        for index, entries in enumerate(groups)
        for entry in entries
    }
    packages: list[Package] = []
    for path, item in tree.items():
        match = re.fullmatch(
            r"(?:.*/)?node_modules/(?:@([a-z0-9][a-z0-9._-]*)/)?([a-z0-9][a-z0-9._-]*)/package\.json",
            path,
        )
        if not match:
            continue
        if item.entry.kind not in {"file", "hardlink"} or item.content is None:
            raise ValueError("Invalid npm package metadata file type")
        layer_index, _ = item.content
        entry = sources[item.content]
        if (
            entry.content_offset is None
            or entry.content_sha256 is None
            or entry.size > MAX_BYTES
        ):
            raise ValueError("Invalid npm package metadata source")
        raw = layers[layer_index].data[
            entry.content_offset : entry.content_offset + entry.size
        ]
        document = json.loads(
            _verified(raw, entry.content_sha256), object_pairs_hook=_unique
        )
        scope, name = match.groups()
        expected = f"@{scope}/{name}" if scope else name
        if not isinstance(document, dict) or document.get("name") != expected:
            raise ValueError("Npm package identity differs from path")
        version = document.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("Unsupported npm package version")
        packages.append(Package("npm", name, version, scope or ""))
    return Inventory(
        image_id,
        platform,
        "npm-package-json",
        tuple(packages),
        ("npm package.json covers explicit node_modules packages only",),
    )
