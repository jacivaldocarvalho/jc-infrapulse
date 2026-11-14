"""Versioned, explicitly untrusted summary of a package reconciliation.

This report does not verify producer identity, source independence, OCI platform,
or signatures. It cannot be used as evidence of complete image coverage.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from oci_package_reconciliation import Comparison

MAX_INPUT = 4 * 1024 * 1024
MANDATORY_GAPS = (
    "SOURCE_AUTHENTICITY_UNVERIFIED",
    "SOURCE_INDEPENDENCE_UNVERIFIED",
    "INPUT_BINDING_UNVERIFIED",
    "PLATFORM_BINDING_UNVERIFIED",
    "COVERAGE_COMPLETENESS_UNPROVEN",
)


def report(
    comparison: Comparison, *, independent_bytes: bytes, sbom_bytes: bytes
) -> dict:
    """Summarize caller-supplied objects; byte-to-comparison binding is unverified."""
    if not isinstance(comparison, Comparison):
        raise TypeError("Comparison required")
    if comparison.coverage != "COVERAGE_PARTIAL":
        raise ValueError("Only partial coverage is supported")
    for content in (independent_bytes, sbom_bytes):
        if not isinstance(content, bytes) or len(content) > MAX_INPUT:
            raise ValueError("Invalid or oversized evidence")
    return {
        "schema_version": 1,
        "image_id": comparison.image_id,
        "platform": comparison.platform,
        "coverage": "COVERAGE_PARTIAL",
        "source_authentication": "UNVERIFIED",
        "unverified_independent_sha256": hashlib.sha256(independent_bytes).hexdigest(),
        "unverified_sbom_sha256": hashlib.sha256(sbom_bytes).hexdigest(),
        "independent_only": [
            asdict(package) for package in comparison.independent_only
        ],
        "sbom_only": [asdict(package) for package in comparison.sbom_only],
        "gaps": list(MANDATORY_GAPS) + list(comparison.unknown),
    }
