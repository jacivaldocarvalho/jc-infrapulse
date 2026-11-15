"""Focused regressions for CI evidence assembly and fail-closed coverage."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_evidence
import sbom


class CiEvidenceTests(unittest.TestCase):
    def test_root_diagnostic_is_persisted_before_failure_without_raw_purl(self) -> None:
        image_id = "sha256:" + "a" * 64
        raw_purl = f"pkg:docker/private.registry/internal/image@{image_id}"
        root = {
            "type": "container",
            "name": image_id,
            "bom-ref": "root",
            "purl": raw_purl,
        }
        error = ci_evidence.provenance.ImageRootValidationError(
            "SBOM_ROOT_PURL_INVALID",
            "purl",
            "pkg:oci/<name>@sha256:<digest>",
            "not-pkg-oci",
            "backend-image",
            image_id,
            root_component=root,
            sbom_source="ci-evidence-sbom",
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            tool = Path(temporary) / "trivy"
            tool.write_text("synthetic")
            with (
                patch.object(
                    sbom,
                    "generate_image",
                    return_value={
                        "generation_status": "SUCCESS",
                        "status": "COVERAGE_PARTIAL",
                        "commit": "b" * 40,
                    },
                ),
                patch.object(ci_evidence.provenance, "statement", side_effect=error),
                self.assertRaises(ci_evidence.provenance.ImageRootValidationError),
            ):
                ci_evidence.generate_one(
                    "backend-image",
                    "image",
                    None,
                    image_id,
                    tool,
                    output,
                    "b" * 40,
                    {},
                )
            report_path = output / "backend-image.root-diagnostic.json"
            report = json.loads(report_path.read_text())
            serialized = report_path.read_text()
            self.assertEqual(report["image"], "backend-image")
            self.assertEqual(report["sbom_source"], "ci-evidence-sbom")
            self.assertEqual(report["root"]["type_category"], "container")
            self.assertTrue(report["root"]["bom_ref_conformant"])
            self.assertEqual(report["root"]["purl"]["package_type"], "docker")
            self.assertTrue(report["root"]["purl"]["digest_matches_expected"])
            self.assertNotIn(raw_purl, serialized)
            self.assertNotIn("private.registry", serialized)
            self.assertFalse((output / "backend-image.provenance.json").exists())

    def test_image_root_failure_emits_compatible_structured_diagnostic(self) -> None:
        error = ci_evidence.provenance.ImageRootValidationError(
            "SBOM_ROOT_PURL_DIGEST_MISMATCH",
            "purl-digest",
            "expected-local-image-id",
            "different-sha256-digest",
            "backend-image",
            "sha256:" + "a" * 64,
        )
        stderr = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "ci_evidence",
                    "--output",
                    "out",
                    "--handoff",
                    "handoff",
                    "--trivy",
                    "trivy",
                    "--commit",
                    "a" * 40,
                    "--run-id",
                    "1",
                    "--run-attempt",
                    "1",
                    "--repository",
                    "example/repo",
                    "--workflow",
                    "ci",
                ],
            ),
            patch.object(ci_evidence, "generate_evidence", side_effect=error),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(ci_evidence.main(), 1)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"], "Invalid image SBOM root")
        self.assertEqual(result["diagnostic"]["image"], "backend-image")
        self.assertEqual(result["diagnostic"]["code"], "SBOM_ROOT_PURL_DIGEST_MISMATCH")

    def _generate_one_with_persisted_statement(
        self,
        persisted: bytes,
        verify_side_effect: object = None,
        read_side_effect: object = None,
    ) -> tuple[dict, object]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        output = root / "evidence"
        output.mkdir()
        source = root / "backend/requirements.lock"
        source.parent.mkdir()
        source.write_text("example==1\n")
        tool = root / "trivy"
        tool.write_text("trivy")
        artifact_hash = ci_evidence.sha256(source)
        expected_statement = {"valid": True, "subject": "synthetic-sbom"}

        def generate(_tool: Path, _source: Path, path: Path, _kind: str) -> dict:
            path.write_text("synthetic sbom")
            return {
                "generation_status": "SUCCESS",
                "status": "COVERAGE_PASS",
                "commit": "b" * 40,
                "sha256": sbom.digest(path),
                "source_sha256": ci_evidence.sha256(_source),
                "coverage": {"status": "COVERAGE_PASS", "expected": 1},
                "duration_seconds": 0.01,
            }

        def write_statement(path: Path, _doc: dict, _inputs: list[Path]) -> None:
            path.write_bytes(persisted)

        with (
            patch.object(ci_evidence, "ROOT", root),
            patch.object(sbom, "generate", side_effect=generate),
            patch.object(
                ci_evidence.provenance,
                "statement",
                return_value=expected_statement,
            ),
            patch.object(
                ci_evidence.provenance,
                "write_statement",
                side_effect=write_statement,
            ),
            patch.object(
                ci_evidence.provenance,
                "verify",
                side_effect=verify_side_effect,
            ) as verify,
            patch.object(
                ci_evidence,
                "_read_persisted_statement",
                side_effect=read_side_effect,
            )
            if read_side_effect is not None
            else unittest.mock.patch.object(
                ci_evidence,
                "_read_persisted_statement",
                wraps=ci_evidence._read_persisted_statement,
            ),
        ):
            record = ci_evidence.generate_one(
                "python",
                "python",
                source,
                None,
                tool,
                output,
                "b" * 40,
                {"path": "backend/requirements.lock", "sha256": artifact_hash},
            )
        return record, verify.call_args

    def test_valid_persisted_statement_is_verified_and_hashed_as_written(self) -> None:
        persisted = b'{"valid": true, "subject": "synthetic-sbom"}\n'
        record, verify_call = self._generate_one_with_persisted_statement(persisted)
        statement_record = record["artifacts"][2]
        self.assertEqual(
            verify_call.args[0], {"valid": True, "subject": "synthetic-sbom"}
        )
        self.assertEqual(
            statement_record["sha256"],
            ci_evidence.hashlib.sha256(persisted).hexdigest(),
        )
        self.assertEqual(statement_record["bytes"], len(persisted))

    def test_corrupt_persisted_json_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._generate_one_with_persisted_statement(b"{not-json")

    def test_semantically_adulterated_statement_is_rejected(self) -> None:
        def reject_changed_statement(
            document: dict, *_args: object, **_kwargs: object
        ) -> None:
            if document != {"valid": True, "subject": "synthetic-sbom"}:
                raise ValueError("Persisted statement differs from generated statement")

        with self.assertRaisesRegex(ValueError, "differs from generated"):
            self._generate_one_with_persisted_statement(
                b'{"valid": false, "subject": "synthetic-sbom"}\n',
                verify_side_effect=reject_changed_statement,
            )

    def test_persisted_statement_read_failure_is_rejected(self) -> None:
        with self.assertRaisesRegex(OSError, "read failure"):
            self._generate_one_with_persisted_statement(
                b'{"valid": true, "subject": "synthetic-sbom"}\n',
                read_side_effect=OSError("read failure"),
            )

    def test_incomplete_declared_lock_coverage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence"
            output.mkdir()
            source = root / "backend/requirements.lock"
            source.parent.mkdir()
            source.write_text("example==1\n")
            tool = root / "trivy"
            tool.write_text("trivy")

            def generate(_tool: Path, _source: Path, path: Path, _kind: str) -> dict:
                path.write_text("synthetic sbom")
                return {
                    "generation_status": "SUCCESS",
                    "status": "COVERAGE_INCOMPLETE",
                    "commit": "b" * 40,
                    "sha256": sbom.digest(path),
                }

            with (
                patch.object(ci_evidence, "ROOT", root),
                patch.object(sbom, "generate", side_effect=generate),
                patch.object(ci_evidence.provenance, "statement") as statement,
                self.assertRaisesRegex(ValueError, "unexpected coverage state"),
            ):
                ci_evidence.generate_one(
                    "python",
                    "python",
                    source,
                    None,
                    tool,
                    output,
                    "b" * 40,
                    {"path": "backend/requirements.lock", "sha256": "a" * 64},
                )
            statement.assert_not_called()

    def test_handoff_commit_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "manifest.json").write_text("{}")
            with (
                patch.object(
                    ci_evidence.vulnerability_ci,
                    "verify_handoff",
                    return_value={
                        "commit": "a" * 40,
                        "images": {"backend-image": {}, "nginx-image": {}},
                    },
                ),
                self.assertRaisesRegex(ValueError, "commit mismatch"),
            ):
                ci_evidence.verify_handoff(directory, "b" * 40)


if __name__ == "__main__":
    unittest.main()
