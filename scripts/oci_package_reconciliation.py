"""Bounded comparison of independently supplied package inventories.

Caller authenticates inputs and ensures source independence. This module does not
parse package databases, verify SBOM bytes or establish coverage/completeness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PACKAGES = 10000
MAX_TEXT = 512
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
SUPPORTED = frozenset({"apk", "deb", "rpm", "pypi", "npm"})


@dataclass(frozen=True, order=True)
class Package:
    ecosystem: str
    name: str
    version: str
    namespace: str = ""
    architecture: str = ""


@dataclass(frozen=True)
class Inventory:
    image_id: str
    platform: str
    source: str
    packages: tuple[Package, ...]
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class Comparison:
    image_id: str
    platform: str
    independent_only: tuple[Package, ...]
    sbom_only: tuple[Package, ...]
    unknown: tuple[str, ...]
    coverage: str = "COVERAGE_PARTIAL"


def _text(value: object, *, empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (bool(value) or empty)
        and len(value) <= MAX_TEXT
        and value == value.strip()
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def _validate(inventory: Inventory, image_id: str, platform: str) -> None:
    if not isinstance(inventory, Inventory):
        raise TypeError("Inventory required")
    if inventory.image_id != image_id or inventory.platform != platform:
        raise ValueError("Inventory scope mismatch")
    if not _text(inventory.source):
        raise ValueError("Invalid inventory source")
    if (
        not isinstance(inventory.packages, tuple)
        or len(inventory.packages) > MAX_PACKAGES
    ):
        raise ValueError("Invalid package collection")
    if (
        not isinstance(inventory.unknown, tuple)
        or len(inventory.unknown) > MAX_PACKAGES
    ):
        raise ValueError("Invalid unknown collection")
    if any(not _text(value) for value in inventory.unknown):
        raise ValueError("Invalid unknown reason")
    seen: set[Package] = set()
    for package in inventory.packages:
        if not isinstance(package, Package) or not all(
            _text(value, empty=optional)
            for value, optional in (
                (package.ecosystem, False),
                (package.name, False),
                (package.version, False),
                (package.namespace, True),
                (package.architecture, True),
            )
        ):
            raise ValueError("Invalid package coordinate")
        if package in seen:
            raise ValueError("Duplicate package coordinate")
        seen.add(package)


def reconcile(
    independent: Inventory,
    sbom: Inventory,
    *,
    expected_image_id: str,
    expected_platform: str,
) -> Comparison:
    """Compare exact coordinates; never infer independence or complete coverage."""
    if not isinstance(expected_image_id, str) or not IMAGE_ID.fullmatch(
        expected_image_id
    ):
        raise ValueError("Independent expected image ID required")
    if not _text(expected_platform):
        raise ValueError("Independent expected platform required")
    _validate(independent, expected_image_id, expected_platform)
    _validate(sbom, expected_image_id, expected_platform)
    if independent.source == sbom.source:
        raise ValueError("Distinct source identifiers required")
    left = set(independent.packages)
    right = set(sbom.packages)
    unknown = {f"independent: {reason}" for reason in independent.unknown}
    unknown.update(f"sbom: {reason}" for reason in sbom.unknown)
    unknown.update(
        f"unsupported ecosystem: {package.ecosystem}"
        for package in left | right
        if package.ecosystem not in SUPPORTED
    )
    return Comparison(
        expected_image_id,
        expected_platform,
        tuple(sorted(left - right)),
        tuple(sorted(right - left)),
        tuple(sorted(unknown)),
    )
