"""Exercise signed OCI reconciliation on a disposable GitHub-hosted runner.

The image, keys and signatures are generated solely for this test and never
published. A valid test signer does not establish builder identity or coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import ci_f3c3_validation as existing
import oci_reconciliation_pipeline as pipeline
import provenance_signature as signature
from test_oci_reconciliation_pipeline import fixture


def _expect_failure(arguments: dict[str, Any]) -> None:
    try:
        pipeline.compare_signed_image(**arguments)
    except (ValueError, RuntimeError):
        return
    raise ValueError("Invalid signed fixture was unexpectedly accepted")


def validate(cosign: Path) -> None:
    """Run positive and negative checks with two temporary signing keys."""
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise ValueError("A disposable GitHub-hosted runner is required")
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        os.environ.pop(name, None)
    os.environ["COSIGN_PASSWORD"] = ""
    config, layers, sbom, image_id = fixture()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": image_id,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": item.digest,
                    "size": item.size,
                }
                for item in layers
            ],
        },
        separators=(",", ":"),
    ).encode()
    with tempfile.TemporaryDirectory(prefix="infrapulse-signed-fixture-") as temporary:
        root = Path(temporary)
        private, _, policy, policy_hash, key_id = existing._generate_test_key(
            cosign, root
        )
        manifest_bundle = root / "manifest.sigstore.json"
        sbom_bundle = root / "sbom.sigstore.json"
        signature.sign_blob_bytes(manifest, manifest_bundle, private, cosign)
        signature.sign_blob_bytes(sbom, sbom_bundle, private, cosign)
        arguments: dict[str, Any] = {
            "manifest": manifest,
            "manifest_bundle": manifest_bundle.read_bytes(),
            "sbom": sbom,
            "sbom_bundle": sbom_bundle.read_bytes(),
            "policy_path": policy,
            "policy_sha256": policy_hash,
            "key_id": key_id,
            "cosign": cosign,
            "config": config,
            "layers": layers,
            "sbom_sha256": hashlib.sha256(sbom).hexdigest(),
            "image_id": image_id,
            "image_name": "backend-image",
            "platform": "linux/amd64",
            "distro": "debian-13.7",
            "os_kind": "debian",
        }
        result = pipeline.compare_signed_image(**arguments)
        if (
            result["coverage"] != "COVERAGE_PARTIAL"
            or result["source_authentication"] != "UNVERIFIED"
            or result["signature_verification"]["signer"] != "TRUSTED_SIGNER"
            or result["independent_only"][0]["version"] != "1.0"
            or result["sbom_only"][0]["version"] != "2.0"
        ):
            raise ValueError("Signed fixture produced an unexpected result")
        _expect_failure({**arguments, "manifest": manifest + b" "})
        _expect_failure({**arguments, "sbom": sbom + b" "})
        _expect_failure({**arguments, "manifest_bundle": arguments["sbom_bundle"]})
        _expect_failure({**arguments, "policy_sha256": "0" * 64})
        _expect_failure({**arguments, "config": config + b" "})
        _expect_failure({**arguments, "platform": "linux/arm64"})
        other = root / "other"
        other.mkdir(mode=0o700)
        other_key, _, _, _, _ = existing._generate_test_key(cosign, other)
        unauthorized_bundle = other / "manifest.sigstore.json"
        signature.sign_blob_bytes(manifest, unauthorized_bundle, other_key, cosign)
        _expect_failure(
            {**arguments, "manifest_bundle": unauthorized_bundle.read_bytes()}
        )
    print(
        "Signed OCI fixture and seven negative checks passed; coverage remains partial"
    )


if __name__ == "__main__":
    binary = os.environ.get("COSIGN_BINARY")
    if not binary:
        raise SystemExit("COSIGN_BINARY is required")
    validate(Path(binary))
