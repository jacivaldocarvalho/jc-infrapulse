"""Reconcile an image using verified F3C.3.2 evidence snapshots.

The existing verifier authenticates a signer for the package, not the builder.
Expected identities, policy hash and key must come from independent channels.
The direct OCI config-digest image ID profile and bounded USTAR reader apply.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import ci_evidence_signature
import oci_reconciliation_pipeline
from oci_virtual_layers import Layer


def reconcile_f3c3(
    *,
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
    image_name: str,
    config: bytes,
    layers: list[Layer],
    platform: str,
    distro: str,
    os_kind: str,
    canonicalize_oci: bool = False,
) -> dict[str, Any]:
    """Verify F3C package then compare only the exact verified SBOM bytes.

    Image config digest and its ordered DiffIDs bind the supplied raw layers to
    the independent image ID; profiles whose image ID is not the config digest
    are rejected by the underlying verifier. Source independence and builder
    authenticity remain unproven even after a trusted signature.
    """
    if image_name not in {"backend-image", "nginx-image"}:
        raise ValueError("Unsupported image name")
    if not isinstance(expected_image_ids, dict):
        raise TypeError("Independent image IDs required")
    identities = dict(expected_image_ids)
    if image_name not in identities:
        raise ValueError("Independent image identity required")
    if not isinstance(config, bytes) or not isinstance(layers, list):
        raise TypeError("Immutable config bytes and layer list required")
    layer_snapshot = list(layers)
    verification, snapshots = ci_evidence_signature.verify_evidence_with_sboms(
        evidence_dir,
        handoff_dir,
        signature_dir,
        cosign,
        trivy,
        policy_path,
        policy_sha256,
        key_id,
        expected_commit,
        expected_run_id,
        expected_run_attempt,
        expected_repository,
        identities,
        canonicalize_oci,
    )
    sbom = snapshots[image_name]
    comparison = oci_reconciliation_pipeline.compare_image(
        config=config,
        layers=layer_snapshot,
        sbom=sbom,
        sbom_sha256=hashlib.sha256(sbom).hexdigest(),
        image_id=identities[image_name],
        image_name=image_name,
        platform=platform,
        distro=distro,
        os_kind=os_kind,
    )
    return {
        "schema_version": 1,
        "evidence_verification": verification,
        "policy_sha256": policy_sha256,
        "key_id": key_id,
        "reconciliation": comparison,
        "limitations": [
            "TRUSTED_SIGNER_DOES_NOT_PROVE_BUILDER_IDENTITY",
            "SOURCE_INDEPENDENCE_UNVERIFIED",
            "COVERAGE_COMPLETENESS_UNPROVEN",
        ],
    }
