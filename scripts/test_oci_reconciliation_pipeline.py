"""End-to-end tests for bounded OCI package reconciliation."""

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import oci_reconciliation_pipeline as pipeline
import oci_virtual_layers as virtual
from jsonschema import Draft7Validator
from test_oci_virtual_layers import layer, tar_bytes


def fixture() -> tuple[bytes, list[virtual.Layer], bytes, str]:
    status = b"Package: demo\nStatus: install ok installed\nVersion: 1.0\nArchitecture: amd64\n"
    layers = [layer(tar_bytes([("var/lib/dpkg/status", "file", status)]))]
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [item.diff_id for item in layers]},
        }
    ).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "component": {
                "type": "container",
                "name": image_id,
                "bom-ref": "root",
                "purl": f"pkg:oci/backend-image@{image_id}",
            },
            "tools": {
                "components": [
                    {"type": "application", "name": "trivy", "version": "0.76.1"}
                ]
            },
        },
        "components": [
            {
                "type": "library",
                "name": "demo",
                "version": "2.0",
                "bom-ref": "pkg",
                "purl": "pkg:deb/debian/demo@2.0?arch=amd64&distro=debian-13.7",
            }
        ],
        "dependencies": [{"ref": "root", "dependsOn": ["pkg"]}],
    }
    return config, layers, json.dumps(document).encode(), image_id


def run(config: bytes, layers: list[virtual.Layer], sbom: bytes, image_id: str) -> dict:
    return pipeline.compare_image(
        config=config,
        layers=layers,
        sbom=sbom,
        sbom_sha256=hashlib.sha256(sbom).hexdigest(),
        image_id=image_id,
        image_name="backend-image",
        platform="linux/amd64",
        distro="debian-13.7",
        os_kind="debian",
    )


class PipelineTests(unittest.TestCase):
    def test_end_to_end_keeps_trust_gaps_and_version_differences(self) -> None:
        config, layers, sbom, image_id = fixture()
        result = run(config, layers, sbom, image_id)
        self.assertEqual(result["independent_only"][0]["version"], "1.0")
        self.assertEqual(result["sbom_only"][0]["version"], "2.0")
        self.assertEqual(result["coverage"], "COVERAGE_PARTIAL")
        self.assertEqual(result["source_authentication"], "UNVERIFIED")
        self.assertIn("MANIFEST_AUTHENTICITY_UNVERIFIED", result["gaps"])
        document = json.loads(json.dumps(result))
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "security/oci-reconciliation-report-v1.schema.json"
            ).read_text()
        )
        Draft7Validator(schema).validate(document)
        document["source_authentication"] = "VERIFIED"
        self.assertFalse(Draft7Validator(schema).is_valid(document))
        document["source_authentication"] = "UNVERIFIED"
        document["gaps"].remove("SOURCE_AUTHENTICITY_UNVERIFIED")
        self.assertFalse(Draft7Validator(schema).is_valid(document))

    def test_signed_manifest_and_sbom_bind_exact_inputs(self) -> None:
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
            }
        ).encode()
        kwargs = {
            "manifest": manifest,
            "manifest_bundle": b"manifest-bundle",
            "sbom_bundle": b"sbom-bundle",
            "policy_path": Path("policy"),
            "policy_sha256": "a" * 64,
            "key_id": "key",
            "cosign": Path("cosign"),
            "config": config,
            "layers": layers,
            "sbom": sbom,
            "sbom_sha256": hashlib.sha256(sbom).hexdigest(),
            "image_id": image_id,
            "image_name": "backend-image",
            "platform": "linux/amd64",
            "distro": "debian-13.7",
            "os_kind": "debian",
        }
        with patch.object(pipeline.signature, "verify_blob_bytes") as verify:
            result = pipeline.compare_signed_image(**kwargs)
        self.assertEqual(
            [call.args[0] for call in verify.call_args_list], [manifest, sbom]
        )
        self.assertEqual(result["signature_verification"]["signer"], "TRUSTED_SIGNER")
        self.assertEqual(result["source_authentication"], "UNVERIFIED")
        self.assertEqual(result["coverage"], "COVERAGE_PARTIAL")
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "security/oci-reconciliation-report-v1.schema.json"
            ).read_text()
        )
        Draft7Validator(schema).validate(json.loads(json.dumps(result)))
        with (
            patch.object(
                pipeline.signature,
                "verify_blob_bytes",
                side_effect=ValueError("signature invalid"),
            ),
            self.assertRaises(ValueError),
        ):
            pipeline.compare_signed_image(**kwargs)
        changed = json.loads(manifest)
        changed["layers"][0]["size"] += 1
        kwargs["manifest"] = json.dumps(changed).encode()
        with (
            patch.object(pipeline.signature, "verify_blob_bytes"),
            self.assertRaisesRegex(ValueError, "descriptor mismatch"),
        ):
            pipeline.compare_signed_image(**kwargs)

    def test_rejects_tampered_config_wrong_platform_and_layers(self) -> None:
        config, layers, _, image_id = fixture()
        with self.assertRaises(ValueError):
            pipeline.verify_config(
                config + b" ", layers, image_id=image_id, platform="linux/amd64"
            )
        with self.assertRaises(ValueError):
            pipeline.verify_config(
                config, layers, image_id=image_id, platform="linux/arm64"
            )
        changed = layer(tar_bytes([("other", "file", b"other")]))
        with self.assertRaises(ValueError):
            pipeline.verify_config(
                config, [changed], image_id=image_id, platform="linux/amd64"
            )
        with self.assertRaises(ValueError):
            pipeline.verify_config(
                config,
                [layers[0], layers[0]],
                image_id=image_id,
                platform="linux/amd64",
            )

    def test_duplicate_config_and_wrong_config_architecture_fail(self) -> None:
        _, layers, _, _ = fixture()
        for raw in (
            b'{"os":"linux","os":"linux"}',
            b'{"os":"linux","invalid":NaN}',
            json.dumps(
                {
                    "os": "linux",
                    "architecture": "arm64",
                    "rootfs": {"type": "layers", "diff_ids": [layers[0].diff_id]},
                }
            ).encode(),
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                pipeline.verify_config(
                    raw,
                    layers,
                    image_id="sha256:" + hashlib.sha256(raw).hexdigest(),
                    platform="linux/amd64",
                )


if __name__ == "__main__":
    unittest.main()
