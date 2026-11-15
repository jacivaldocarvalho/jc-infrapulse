"""Tests for reconciliation using F3C evidence verification snapshots."""

import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

import oci_f3c3_reconciliation as integration
from test_oci_reconciliation_pipeline import fixture


class F3CReconciliationTests(unittest.TestCase):
    def arguments(self) -> dict:
        config, layers, _, image_id = fixture()
        return {
            "evidence_dir": Path("evidence"),
            "handoff_dir": Path("handoff"),
            "signature_dir": Path("signatures"),
            "cosign": Path("cosign"),
            "trivy": Path("trivy"),
            "policy_path": Path("policy"),
            "policy_sha256": "a" * 64,
            "key_id": "test-key",
            "expected_commit": "b" * 40,
            "expected_run_id": "123",
            "expected_run_attempt": 1,
            "expected_repository": "example/project",
            "expected_image_ids": {
                "backend-image": image_id,
                "nginx-image": "sha256:" + "c" * 64,
            },
            "image_name": "backend-image",
            "config": config,
            "layers": layers,
            "platform": "linux/amd64",
            "distro": "debian-13.7",
            "os_kind": "debian",
        }

    def test_passes_exact_verified_sbom_snapshot_to_comparison(self) -> None:
        arguments = self.arguments()
        expected = b"verified sbom bytes"
        verification = {
            "status": "VERIFIED",
            "signature": "SIGNATURE_VALID",
            "signer": "TRUSTED_SIGNER",
        }
        with (
            patch.object(
                integration.ci_evidence_signature,
                "verify_evidence_with_sboms",
                return_value=(verification, {"backend-image": expected}),
            ),
            patch.object(
                integration.oci_reconciliation_pipeline,
                "compare_image",
                return_value={"coverage": "COVERAGE_PARTIAL"},
            ) as compare,
        ):
            result = integration.reconcile_f3c3(**arguments)
        self.assertEqual(compare.call_args.kwargs["sbom"], expected)
        self.assertEqual(
            compare.call_args.kwargs["sbom_sha256"],
            hashlib.sha256(expected).hexdigest(),
        )
        self.assertEqual(result["evidence_verification"], verification)
        self.assertIn(
            "TRUSTED_SIGNER_DOES_NOT_PROVE_BUILDER_IDENTITY", result["limitations"]
        )

    def test_full_orchestration_with_verified_snapshots_and_real_comparison(
        self,
    ) -> None:
        arguments = self.arguments()
        config, layers, sbom, image_id = fixture()
        arguments.update(
            config=config,
            layers=layers,
            expected_image_ids={
                "backend-image": image_id,
                "nginx-image": "sha256:" + "c" * 64,
            },
        )
        evidence = integration.ci_evidence_signature
        data = {"evidence-manifest.json": b"signed-manifest"}
        for name in evidence.COMPONENTS:
            data[f"{name}.cdx.json"] = sbom
            data[f"{name}.manifest.json"] = b"{}"
            data[f"{name}.provenance.json"] = b"{}"
        bundles = {name: b"signed-bundle" for name in evidence._bundle_names()}
        manifest = {"commit": "b" * 40, "workflow_run_id": "123", "run_attempt": 1}
        with (
            patch.object(evidence, "_load_and_validate", return_value=(data, manifest)),
            patch.object(evidence, "_validate_handoff_archives", return_value=None),
            patch.object(evidence, "_read_exact_directory", return_value=bundles),
            patch.object(evidence.signature, "_trusted_key", return_value=b"key"),
            patch.object(evidence.signature, "verify_blob_bytes") as verify_manifest,
            patch.object(evidence.signature, "verify_signature") as verify_statements,
        ):
            result = integration.reconcile_f3c3(**arguments)
        verify_manifest.assert_called_once()
        self.assertEqual(verify_statements.call_count, 4)
        self.assertEqual(
            result["reconciliation"]["independent_only"][0]["version"], "1.0"
        )
        self.assertEqual(result["reconciliation"]["sbom_only"][0]["version"], "2.0")
        self.assertEqual(result["reconciliation"]["coverage"], "COVERAGE_PARTIAL")

    def test_signature_failure_prevents_comparison(self) -> None:
        with (
            patch.object(
                integration.ci_evidence_signature,
                "verify_evidence_with_sboms",
                side_effect=ValueError("invalid signature"),
            ),
            patch.object(
                integration.oci_reconciliation_pipeline, "compare_image"
            ) as compare,
            self.assertRaises(ValueError),
        ):
            integration.reconcile_f3c3(**self.arguments())
        compare.assert_not_called()

    def test_config_mismatch_after_verification_fails_closed(self) -> None:
        arguments = self.arguments()
        config, layers, sbom, _ = fixture()
        arguments["config"] = config + b" "
        arguments["layers"] = layers
        with (
            patch.object(
                integration.ci_evidence_signature,
                "verify_evidence_with_sboms",
                return_value=({"status": "VERIFIED"}, {"backend-image": sbom}),
            ),
            self.assertRaisesRegex(ValueError, "config digest mismatch"),
        ):
            integration.reconcile_f3c3(**arguments)


if __name__ == "__main__":
    unittest.main()
