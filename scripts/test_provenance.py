"""Offline provenance verification regressions."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import provenance
import sbom


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.artifact, self.tool, self.manifest = (
            root / x for x in ("bom", "trivy", "manifest")
        )
        (root / "backend").mkdir()
        (root / "frontend").mkdir()
        self.source = root / "backend/requirements.lock"
        self.artifact.write_text("sbom")
        self.source.write_text("example==1 --hash=sha256:" + "a" * 64 + "\n")
        self.tool.write_text("binary")
        self.doc = {
            "metadata": {"component": {"name": "requirements.txt"}},
            "components": [
                {"name": "example", "version": "1", "purl": "pkg:pypi/example@1"}
            ],
        }
        expected, excluded = sbom.inventory(self.source, "python")
        coverage = sbom.coverage(self.doc, expected, excluded, "python")
        self.data = {
            "generation_status": "SUCCESS",
            "sha256": sbom.digest(self.artifact),
            "bytes": self.artifact.stat().st_size,
            "commit": "a" * 40,
            "tool": "trivy",
            "tool_version": sbom.PINS["trivy"],
            "source": str(self.source),
            "source_sha256": sbom.digest(self.source),
            "status": "COVERAGE_PASS",
            "coverage": coverage,
            "generated_at": "2026-01-01T00:00:00Z",
        }
        self.save()
        self.enterContext(patch.object(sbom, "ROOT", root))
        self.enterContext(
            patch.object(
                sbom,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "a" * 40, ""),
            )
        )
        self.enterContext(patch.object(sbom, "verify_tool"))

        def validate_fixture(path: Path, expected: str) -> dict:
            if sbom.digest(path) != expected:
                raise ValueError("SBOM digest mismatch")
            return self.doc

        self.enterContext(patch.object(sbom, "validate", side_effect=validate_fixture))

    def save(self) -> None:
        self.manifest.write_text(json.dumps(self.data))

    def create(self) -> dict:
        return provenance.statement(self.artifact, self.manifest, self.tool)

    def test_valid_and_builder_identity(self) -> None:
        doc = self.create()
        provenance.verify(doc, self.artifact, self.manifest, self.tool)
        doc["predicate"]["runDetails"]["builder"]["id"] = "trusted"
        with self.assertRaises(ValueError):
            provenance.verify(doc, self.artifact, self.manifest, self.tool)

    def test_tampered_input(self) -> None:
        doc = self.create()
        self.source.write_text("changed")
        with self.assertRaises(ValueError):
            provenance.verify(doc, self.artifact, self.manifest, self.tool)

    def test_artifact_tampering(self) -> None:
        doc = self.create()
        self.artifact.write_text("evil")
        with self.assertRaises(ValueError):
            provenance.verify(doc, self.artifact, self.manifest, self.tool)

    def test_bad_manifest(self) -> None:
        for field, value in (
            ("commit", "b" * 40),
            ("sha256", "oops"),
            ("source_sha256", None),
            ("generation_status", "ERROR"),
        ):
            with self.subTest(field=field):
                original = self.data[field]
                self.data[field] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.create()
                self.data[field] = original
        self.manifest.write_text("{")
        with self.assertRaises(ValueError):
            self.create()

    def test_predicate_and_io_errors(self) -> None:
        doc = self.create()
        doc["predicateType"] = "bad"
        with self.assertRaises(ValueError):
            provenance.verify(doc, self.artifact, self.manifest, self.tool)
        with patch.object(sbom, "check_output"), self.assertRaises(OSError):
            provenance.write_statement(
                self.artifact.parent / "missing" / "out", doc, [self.artifact]
            )
        self.manifest.unlink()
        with self.assertRaises(OSError):
            self.create()

    def test_rejects_manifest_for_different_inventory(self) -> None:
        unrelated = self.source.parent.parent / "frontend/package-lock.json"
        unrelated.write_text(
            '{"lockfileVersion":3,"packages":{"node_modules/example":{"version":"1"}}}'
        )
        self.data["source"] = str(unrelated)
        self.data["source_sha256"] = sbom.digest(unrelated)
        self.save()
        python_sbom = {"metadata": {"component": {"name": "requirements.txt"}}}
        with (
            patch.object(sbom, "validate", return_value=python_sbom),
            self.assertRaises(ValueError),
        ):
            self.create()

    def use_npm(self) -> None:
        source = self.source.parent.parent / "frontend/package-lock.json"
        source.write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {"node_modules/example": {"version": "1"}},
                }
            )
        )
        self.doc = {
            "metadata": {"component": {"name": "package-lock.json"}},
            "components": [
                {"name": "example", "version": "1", "purl": "pkg:npm/example@1"}
            ],
        }
        expected, excluded = sbom.inventory(source, "npm")
        self.data.update(
            source=str(source),
            source_sha256=sbom.digest(source),
            coverage=sbom.coverage(self.doc, expected, excluded, "npm"),
        )
        self.save()

    def test_npm_valid_and_swapped_python(self) -> None:
        self.use_npm()
        self.create()
        self.data.update(
            source=str(self.source), source_sha256=sbom.digest(self.source)
        )
        self.save()
        with self.assertRaises(ValueError):
            self.create()

    def test_coverage_discrepancies(self) -> None:
        self.create()
        self.doc["components"].append(
            {"name": "other", "version": "2", "purl": "pkg:pypi/other@2"}
        )
        with self.assertRaises(ValueError):
            self.create()
        self.doc["components"].clear()
        with self.assertRaises(ValueError):
            self.create()
        self.doc["components"].append(
            {"name": "example", "version": "1", "purl": "pkg:pypi/example@1"}
        )
        self.data["coverage"]["found"] = 99
        self.save()
        with self.assertRaises(ValueError):
            self.create()

    def test_source_path_traversal_and_special_files(self) -> None:
        self.data["source"] = str(
            self.source.parent / ".." / "backend" / "requirements.lock"
        )
        self.save()
        with self.assertRaises(ValueError):
            self.create()
        self.data["source"] = str(self.source)
        self.source.unlink()
        self.source.symlink_to(self.artifact)
        self.save()
        with self.assertRaises(OSError):
            self.create()
        self.source.unlink()
        self.source.mkdir()
        with self.assertRaises((OSError, ValueError)):
            self.create()
        self.source.rmdir()
        os.mkfifo(self.source)
        with self.assertRaises(ValueError):
            self.create()

    def test_local_image_identity(self) -> None:
        image_id = "sha256:" + "a" * 64
        self.data.pop("source")
        self.data.pop("source_sha256")
        self.data.update(
            image_id=image_id,
            reference="image",
            status="COVERAGE_PARTIAL",
            components=1,
        )
        self.doc = {
            "metadata": {"component": {"name": image_id}},
            "components": [{"name": "example"}],
        }
        self.save()
        with self.assertRaisesRegex(ValueError, "Independent full image ID"):
            self.create()

    def test_disguised_lock_inventories_are_not_classified_as_images(self) -> None:
        image_id = "sha256:" + "a" * 64
        for kind in ("python", "npm"):
            with self.subTest(kind=kind):
                if kind == "npm":
                    self.use_npm()
                self.data.pop("source", None)
                self.data.pop("source_sha256", None)
                self.data.update(
                    image_id=image_id,
                    reference="image",
                    status="COVERAGE_PARTIAL",
                    components=1,
                )
                self.doc["metadata"]["component"].update(
                    name=image_id,
                    type="container",
                    properties=[
                        {"name": "aquasecurity:trivy:ImageID", "value": image_id}
                    ],
                )
                self.save()
                with self.assertRaisesRegex(ValueError, "Independent full image ID"):
                    self.create()

    def test_image_inventory_comparison(self) -> None:
        image_id = "sha256:" + "a" * 64
        doc = {
            "metadata": {
                "component": {
                    "type": "container",
                    "name": image_id,
                    "bom-ref": "root",
                    "purl": f"pkg:oci/local@{image_id}?arch=amd64",
                }
            },
            "components": [
                {
                    "bom-ref": "one",
                    "type": "library",
                    "name": "a",
                    "version": "1",
                    "purl": "pkg:pypi/a@1",
                },
                {
                    "bom-ref": "two",
                    "type": "library",
                    "name": "b",
                    "version": "1",
                    "purl": "pkg:pypi/b@1",
                },
            ],
            "dependencies": [
                {"ref": "root", "dependsOn": ["one", "two"]},
                {"ref": "one", "dependsOn": ["two"]},
                {"ref": "two", "dependsOn": []},
            ],
        }
        manifest = {"image_id": image_id, "status": "COVERAGE_PARTIAL", "components": 2}
        report = {
            "generation_status": "SUCCESS",
            "status": "COVERAGE_PARTIAL",
            "image_id": image_id,
            "sha256": "x",
            "components": 2,
        }
        with (
            patch.object(sbom, "resolve_image", return_value={"image_id": image_id}),
            patch.object(sbom, "generate_image", return_value=report),
            patch.object(sbom, "validate", return_value=doc),
        ):
            provenance.verify_image(doc, manifest, self.tool, image_id)
            for change in (
                "removed",
                "extra",
                "version",
                "purl",
                "duplicate",
                "dependency",
                "python",
                "npm",
                "root-purl",
                "ordered-list",
            ):
                altered = json.loads(json.dumps(doc))
                if change == "removed":
                    altered["components"].pop()
                elif change == "extra":
                    altered["components"].append(
                        {"bom-ref": "extra", "type": "library", "name": "extra"}
                    )
                elif change == "version":
                    altered["components"][0]["version"] = "2"
                elif change == "purl":
                    altered["components"][0]["purl"] = "pkg:pypi/a@2"
                elif change == "duplicate":
                    altered["components"][1] = {
                        **altered["components"][0],
                        "bom-ref": "two",
                    }
                elif change == "dependency":
                    altered["dependencies"][1]["dependsOn"] = []
                elif change == "root-purl":
                    altered["metadata"]["component"]["purl"] = (
                        "pkg:oci/other@sha256:" + "0" * 64
                    )
                elif change == "ordered-list":
                    altered["components"][0]["properties"] = [
                        {"name": "first", "value": "1"},
                        {"name": "second", "value": "2"},
                    ]
                    doc["components"][0]["properties"] = list(
                        reversed(altered["components"][0]["properties"])
                    )
                else:
                    altered["metadata"]["component"]["type"] = "application"
                changed_manifest = {
                    **manifest,
                    "components": len(altered["components"]),
                }
                with self.subTest(change=change), self.assertRaises(ValueError):
                    provenance.verify_image(
                        altered, changed_manifest, self.tool, image_id
                    )
            for invalid in (None, "sha256:" + "b" * 64):
                with self.assertRaises(ValueError):
                    provenance.verify_image(doc, manifest, self.tool, invalid)
        with (
            patch.object(
                sbom, "resolve_image", return_value={"status": "COVERAGE_PENDING"}
            ),
            self.assertRaises(ValueError),
        ):
            provenance.verify_image(doc, manifest, self.tool, image_id)
        with (
            patch.object(sbom, "resolve_image", return_value={"image_id": image_id}),
            patch.object(
                sbom, "generate_image", side_effect=OSError("scanner unavailable")
            ),
            self.assertRaises(OSError),
        ):
            provenance.verify_image(doc, manifest, self.tool, image_id)
        with (
            patch.object(sbom, "resolve_image", return_value={"image_id": image_id}),
            patch.object(
                sbom, "generate_image", return_value={"status": "COVERAGE_PENDING"}
            ),
            self.assertRaises(ValueError),
        ):
            provenance.verify_image(doc, manifest, self.tool, image_id)

    def test_image_root_diagnostics_identify_failed_condition(self) -> None:
        image_id = "sha256:" + "a" * 64
        observed_purl = f"pkg:docker/private.registry/internal/image@{image_id}"
        valid_root = {
            "type": "container",
            "name": image_id,
            "bom-ref": "root",
            "purl": f"pkg:oci/local@{image_id}?arch=amd64",
        }
        cases = (
            (
                "type",
                {**valid_root, "type": "application"},
                "SBOM_ROOT_TYPE_INVALID",
                "type",
            ),
            (
                "bom-ref",
                {**valid_root, "bom-ref": ""},
                "SBOM_ROOT_BOM_REF_MISSING",
                "bom-ref",
            ),
            (
                "purl",
                {**valid_root, "purl": observed_purl},
                "SBOM_ROOT_PURL_INVALID",
                "purl",
            ),
            (
                "malformed-digest",
                {**valid_root, "purl": "pkg:oci/local@sha256:abc"},
                "SBOM_ROOT_PURL_DIGEST_INVALID",
                "purl-digest",
            ),
            (
                "digest",
                {**valid_root, "purl": "pkg:oci/local@sha256:" + "b" * 64},
                "SBOM_ROOT_PURL_DIGEST_MISMATCH",
                "purl-digest",
            ),
            (
                "identity",
                {**valid_root, "name": "private-registry/image:tag"},
                "SBOM_ROOT_LOCAL_IDENTITY_MISMATCH",
                "name",
            ),
        )
        for _label, root, code, condition in cases:
            with (
                self.subTest(condition=condition),
                self.assertRaises(provenance.ImageRootValidationError) as raised,
            ):
                provenance.normalized_inventory(
                    {
                        "metadata": {"component": root},
                        "components": [],
                        "dependencies": [],
                    },
                    image_id,
                    image_name="backend-image",
                    sbom_source="ci-evidence-sbom",
                )
            diagnostic = raised.exception.diagnostic()
            self.assertEqual(diagnostic["code"], code)
            self.assertEqual(diagnostic["condition"], condition)
            self.assertEqual(diagnostic["image"], "backend-image")
            self.assertEqual(diagnostic["expected_local_image_id"], image_id)
            self.assertEqual(diagnostic["sbom_source"], "ci-evidence-sbom")
            serialized = json.dumps(diagnostic)
            self.assertNotIn(observed_purl, serialized)
            self.assertNotIn("private.registry", serialized)
            if condition == "purl":
                self.assertEqual(diagnostic["root"]["purl"]["scheme"], "pkg")
                self.assertEqual(diagnostic["root"]["purl"]["package_type"], "docker")
                self.assertEqual(
                    diagnostic["root"]["purl"]["digest_format"], "sha256-valid"
                )
                self.assertTrue(diagnostic["root"]["purl"]["digest_matches_expected"])


if __name__ == "__main__":
    unittest.main()
