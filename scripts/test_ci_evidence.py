"""Focused regressions for CI evidence assembly and fail-closed coverage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_evidence
import sbom


class CiEvidenceTests(unittest.TestCase):
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
