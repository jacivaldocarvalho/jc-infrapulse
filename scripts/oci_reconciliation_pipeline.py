"""Bounded reconciliation pipeline; caller authenticates external identities.

Digest consistency is not source authentication. The OCI manifest and SBOM
signatures, independent expected image ID, distro, platform, and descriptor order
must be verified by the caller. Output does not establish complete coverage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import provenance_signature as signature
from oci_package_adapters import (
    alpine_from_layers,
    debian_from_layers,
    npm_from_layers,
    python_from_layers,
    trivy_cyclonedx,
)
from oci_package_reconciliation import IMAGE_ID, Inventory, reconcile
from oci_virtual_layers import Layer, read_layers

MAX_CONFIG_BYTES = 1024 * 1024


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid JSON constant")


def verify_config(
    raw: bytes, layers: list[Layer], *, image_id: str, platform: str
) -> None:
    """Verify config digest, exact supported platform and ordered layer DiffIDs.

    This checks consistency with an externally trusted image ID; it does not
    authenticate a manifest, compressed layer descriptors or the producer.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Invalid or oversized OCI config")
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise ValueError("Independent image ID required")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != image_id:
        raise ValueError("Image config digest mismatch")
    if platform != "linux/amd64":
        raise ValueError("Unsupported expected platform")
    try:
        config = json.loads(
            raw, object_pairs_hook=_unique, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid OCI configuration JSON") from exc
    if (
        not isinstance(config, dict)
        or config.get("os") != "linux"
        or config.get("architecture") != "amd64"
        or config.get("variant") not in (None, "")
    ):
        raise ValueError("OCI configuration platform mismatch")
    rootfs = config.get("rootfs")
    if (
        not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(rootfs.get("diff_ids"), list)
    ):
        raise ValueError("Invalid OCI rootfs config")
    read_layers(layers, rootfs=True)
    if rootfs["diff_ids"] != [layer.diff_id for layer in layers]:
        raise ValueError("OCI config layer order or DiffID mismatch")


def compare_image(
    *,
    config: bytes,
    layers: list[Layer],
    sbom: bytes,
    sbom_sha256: str,
    image_id: str,
    image_name: str,
    platform: str,
    distro: str,
    os_kind: str,
) -> dict:
    """Compare verified-in-memory bytes; inputs remain unauthenticated here."""
    verify_config(config, layers, image_id=image_id, platform=platform)
    if os_kind == "debian":
        os_inventory = debian_from_layers(layers, image_id=image_id, platform=platform)
    elif os_kind == "alpine":
        os_inventory = alpine_from_layers(layers, image_id=image_id, platform=platform)
    else:
        raise ValueError("Unsupported OS package database")
    python = python_from_layers(layers, image_id=image_id, platform=platform)
    npm = npm_from_layers(layers, image_id=image_id, platform=platform)
    independent = Inventory(
        image_id,
        platform,
        "virtual-rootfs",
        os_inventory.packages + python.packages + npm.packages,
        os_inventory.unknown + python.unknown + npm.unknown,
    )
    scanned = trivy_cyclonedx(
        sbom,
        expected_sha256=sbom_sha256,
        image_id=image_id,
        platform=platform,
        image_name=image_name,
        expected_distro=distro,
    )
    result = reconcile(
        independent, scanned, expected_image_id=image_id, expected_platform=platform
    )
    return {
        "schema_version": 1,
        "image_id": image_id,
        "platform": platform,
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "sbom_sha256": hashlib.sha256(sbom).hexdigest(),
        "coverage": "COVERAGE_PARTIAL",
        "source_authentication": "UNVERIFIED",
        "source_independence": "UNVERIFIED",
        "independent_only": [asdict(item) for item in result.independent_only],
        "sbom_only": [asdict(item) for item in result.sbom_only],
        "gaps": result.unknown
        + (
            "SOURCE_AUTHENTICITY_UNVERIFIED",
            "SOURCE_INDEPENDENCE_UNVERIFIED",
            "MANIFEST_AUTHENTICITY_UNVERIFIED",
            "DISTRO_BINDING_UNVERIFIED",
            "COVERAGE_COMPLETENESS_UNPROVEN",
        ),
    }


def compare_signed_image(
    *,
    manifest: bytes,
    manifest_bundle: bytes,
    sbom_bundle: bytes,
    policy_path: Path,
    policy_sha256: str,
    key_id: str,
    cosign: Path,
    config: bytes,
    layers: list[Layer],
    sbom: bytes,
    sbom_sha256: str,
    image_id: str,
    image_name: str,
    platform: str,
    distro: str,
    os_kind: str,
) -> dict:
    """Verify signatures of exact manifest and SBOM bytes before reconciliation.

    Policy pin, keys, expected image identity and platform are independent caller
    inputs. This verifies the authorized signing key, not the builder identity
    or independence of the two sources. Only direct uncompressed OCI is accepted.
    """
    if (
        not isinstance(manifest, bytes)
        or not manifest
        or len(manifest) > MAX_CONFIG_BYTES
    ):
        raise ValueError("Invalid or oversized OCI manifest")
    signature.verify_blob_bytes(
        manifest, manifest_bundle, policy_path, policy_sha256, key_id, cosign
    )
    signature.verify_blob_bytes(
        sbom, sbom_bundle, policy_path, policy_sha256, key_id, cosign
    )
    try:
        document = json.loads(
            manifest, object_pairs_hook=_unique, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid OCI manifest JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schemaVersion", "mediaType", "config", "layers"}
        or type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 2
        or document["mediaType"] != "application/vnd.oci.image.manifest.v1+json"
    ):
        raise ValueError("Unsupported OCI manifest")
    config_descriptor = document["config"]
    expected_config = {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": image_id,
        "size": len(config),
    }
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor != expected_config
        or type(config_descriptor.get("size")) is not int
    ):
        raise ValueError("Manifest config descriptor mismatch")
    descriptors = document["layers"]
    if not isinstance(descriptors, list) or len(descriptors) != len(layers):
        raise ValueError("Manifest layer count mismatch")
    for descriptor, layer in zip(descriptors, layers, strict=True):
        if not isinstance(layer, Layer) or not isinstance(descriptor, dict):
            raise TypeError("Invalid manifest layer descriptor")
        expected = {
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": layer.digest,
            "size": layer.size,
        }
        if descriptor != expected or type(descriptor.get("size")) is not int:
            raise ValueError("Manifest layer descriptor mismatch")
    result = compare_image(
        config=config,
        layers=layers,
        sbom=sbom,
        sbom_sha256=sbom_sha256,
        image_id=image_id,
        image_name=image_name,
        platform=platform,
        distro=distro,
        os_kind=os_kind,
    )
    result["manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    result["signature_verification"] = {
        "manifest": "SIGNATURE_VALID",
        "sbom": "SIGNATURE_VALID",
        "signer": "TRUSTED_SIGNER",
        "policy_sha256": policy_sha256,
        "key_id": key_id,
    }
    return result
