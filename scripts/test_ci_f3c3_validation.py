from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ci_f3c3_validation as validation


def _artifact_records() -> list[dict[str, object]]:
    records = []
    for index, artifact in enumerate(validation.ARTIFACTS, start=1):
        records.append(
            {
                "id": index,
                "name": artifact.name,
                "size_in_bytes": 128,
                "digest": "sha256:" + "a" * 64,
                "expired": False,
                "workflow_run": {
                    "id": int(validation.RUN_ID),
                    "head_sha": validation.EVIDENCE_COMMIT,
                },
            }
        )
    return records


def _image_record(image_id: str) -> dict[str, object]:
    return {
        "id": image_id,
        "os": "linux",
        "architecture": "amd64",
        "user": "10001",
        "entrypoint": None,
        "cmd": [],
        "ports": {},
        "healthcheck": None,
        "environment_keys": [],
        "layers": 1,
        "rootfs": [],
        "bytes": 1024,
        "permissions": {},
        "application_build_residue_files": [],
    }


class RunnerValidationTests(unittest.TestCase):
    def test_execute_phase_failures_are_sanitized_and_nonzero(self) -> None:
        for phase in ("checkout", "tools", "extract"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".git").mkdir()
                sensitive = "secret /private/path payload"
                evidence = SimpleNamespace()
                signature = SimpleNamespace(verify_cosign=lambda _: None)
                sbom = SimpleNamespace(verify_tool=lambda _: None)
                output, errors = io.StringIO(), io.StringIO()
                with (
                    patch.object(
                        validation,
                        "_work_dir",
                        side_effect=OSError(sensitive) if phase == "checkout" else None,
                        return_value=root,
                    ),
                    patch.object(
                        validation,
                        "_safe_subprocess",
                        return_value=validation.EVIDENCE_COMMIT.encode(),
                    ),
                    patch.object(
                        validation,
                        "_load_modules",
                        side_effect=ValueError(sensitive) if phase == "tools" else None,
                        return_value=(evidence, signature, None, sbom),
                    ),
                    patch.object(
                        validation, "extract_artifacts", side_effect=KeyError(sensitive)
                    ),
                    patch.dict(
                        os.environ,
                        {
                            "GITHUB_ACTIONS": "true",
                            "TRIVY_BINARY": "trivy",
                            "COSIGN_BINARY": "cosign",
                        },
                    ),
                    patch(
                        "sys.argv",
                        [
                            "validation",
                            "execute",
                            "--work-dir",
                            str(root),
                            "--project-root",
                            str(root),
                        ],
                    ),
                    redirect_stdout(output),
                    redirect_stderr(errors),
                ):
                    self.assertEqual(validation.main(), 1)
                log = output.getvalue() + errors.getvalue()
                self.assertIn(f"PHASE {phase} START", log)
                self.assertIn(f"PHASE {phase} FAIL", log)
                self.assertNotIn(sensitive, log)

    def test_workflow_overlays_validators_before_installing_cosign(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/f3c3-real-validation.yml"
        ).read_text()
        self.assertLess(
            workflow.index("- name: Verify pinned checkouts and overlay"),
            workflow.index("- name: Install pinned Trivy and Cosign"),
        )

    def test_module_import_errors_are_distinguished_without_exception_text(
        self,
    ) -> None:
        cases = (
            (
                ModuleNotFoundError("private path", name="ci_evidence_signature"),
                "module missing",
            ),
            (
                ModuleNotFoundError("private path", name="dependency"),
                "dependency missing",
            ),
            (ImportError("private path"), "internal import error"),
        )
        for error, expected in cases:
            with (
                self.subTest(expected=expected),
                patch.object(validation.importlib, "import_module", side_effect=error),
            ):
                with self.assertRaises(validation.ValidationError) as caught:
                    validation._load_modules(Path("."))
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("private path", str(caught.exception))

    def test_cosign_download_requires_exact_size_and_hash(self) -> None:
        expected = b"abcd"
        lock = {
            "schema_version": 1,
            "name": "cosign",
            "platform": "linux-amd64",
            "version": "3.1.3",
            "release_url": "https://github.com/sigstore/cosign/releases/download/v3.1.3/cosign-linux-amd64",
            "sha256": hashlib.sha256(expected).hexdigest(),
        }
        cases = (
            (expected, None),
            (b"abc", "size mismatch"),
            (b"abcde", "size limit"),
            (b"wxyz", "SHA-256 mismatch"),
        )
        for payload, error in cases:
            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                output = root / "cosign"
                verifier = unittest.mock.Mock()
                with (
                    patch.object(
                        validation, "COSIGN_V313_LINUX_AMD64_BYTES", len(expected)
                    ),
                    patch.object(validation, "_read_json_file", return_value=lock),
                    patch.object(
                        validation.urllib.request,
                        "urlopen",
                        return_value=io.BytesIO(payload),
                    ),
                    patch.object(
                        validation,
                        "_load_modules",
                        return_value=(
                            None,
                            SimpleNamespace(verify_cosign=verifier),
                            None,
                            None,
                        ),
                    ),
                ):
                    if error:
                        with self.assertRaisesRegex(validation.ValidationError, error):
                            validation.install_cosign(root, output)
                        self.assertFalse(output.exists())
                        verifier.assert_not_called()
                    else:
                        validation.install_cosign(root, output)
                        self.assertEqual(output.read_bytes(), expected)
                        verifier.assert_called_once_with(output)

    def test_unauthorized_signer_must_be_valid_then_rejected_by_original_policy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_policy = root / "original-policy.json"
            second_policy = root / "second-policy.json"
            second_private = root / "second.key"
            original_policy_sha = "a" * 64
            second_policy_sha = "b" * 64
            second_key_id = "sha256:" + "c" * 64
            policy_error = "Signer is not authorized by trust policy"
            calls: list[tuple[bytes, bytes, Path, str, str]] = []

            def sign_blob(
                payload: bytes, bundle_path: Path, private_key: Path, cosign: Path
            ) -> None:
                self.assertEqual(payload, b"test payload")
                self.assertEqual(private_key, second_private)
                bundle_path.write_bytes(b"valid second-key bundle")

            def verify_blob(
                payload: bytes,
                bundle: bytes,
                policy_path: Path,
                policy_sha256: str,
                key_id: str,
                cosign: Path,
            ) -> dict[str, str]:
                calls.append((payload, bundle, policy_path, policy_sha256, key_id))
                if bundle == b"valid second-key bundle":
                    if policy_path == second_policy and key_id == second_key_id:
                        return {
                            "signature": "SIGNATURE_VALID",
                            "signer": "TRUSTED_SIGNER",
                        }
                    if policy_path == original_policy and key_id == second_key_id:
                        raise ValueError(policy_error)
                if bundle == b'{"tampered":true}':
                    raise ValueError("Malformed signature bundle")
                if payload == b"test payloadtampered":
                    raise ValueError("Signature does not verify")
                if key_id == "sha256:" + "0" * 64:
                    raise ValueError("Trusted key fingerprint mismatch")
                raise AssertionError("Unexpected signature verification request")

            signature_module = SimpleNamespace(
                sign_blob_bytes=sign_blob, verify_blob_bytes=verify_blob
            )
            generated_key = (
                second_private,
                root / "second.pub",
                second_policy,
                second_policy_sha,
                second_key_id,
            )
            with patch.object(
                validation, "_generate_test_key", return_value=generated_key
            ):
                validation._negative_crypto_checks(
                    signature_module,
                    root / "cosign",
                    original_policy,
                    original_policy_sha,
                    "sha256:" + "d" * 64,
                    b"test payload",
                    b"valid original bundle",
                    root,
                )

            self.assertEqual(len(calls), 5)
            self.assertEqual(calls[2][1], b"valid second-key bundle")
            self.assertEqual(
                calls[2][2:], (second_policy, second_policy_sha, second_key_id)
            )
            self.assertEqual(calls[3][1], b"valid second-key bundle")
            self.assertEqual(
                calls[3][2:], (original_policy, original_policy_sha, second_key_id)
            )

            policy_error = "Malformed signature bundle"
            retry_root = root / "retry"
            retry_root.mkdir()
            with (
                patch.object(
                    validation, "_generate_test_key", return_value=generated_key
                ),
                self.assertRaisesRegex(validation.ValidationError, "unexpected reason"),
            ):
                validation._negative_crypto_checks(
                    signature_module,
                    root / "cosign",
                    original_policy,
                    original_policy_sha,
                    "sha256:" + "d" * 64,
                    b"test payload",
                    b"valid original bundle",
                    retry_root,
                )

    def test_exact_source_run_identity_is_required(self) -> None:
        run = {
            "id": int(validation.RUN_ID),
            "run_attempt": validation.RUN_ATTEMPT,
            "head_sha": validation.EVIDENCE_COMMIT,
            "event": "push",
            "head_branch": "develop",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": validation.REPOSITORY},
            "head_repository": {"full_name": validation.REPOSITORY},
        }
        validation._require_run(run)
        run["run_attempt"] = validation.RUN_ATTEMPT + 1
        with self.assertRaises(validation.ValidationError):
            validation._require_run(run)

    def test_exact_artifact_set_and_transport_metadata_are_required(self) -> None:
        found = validation._require_artifacts(_artifact_records())
        self.assertEqual(
            set(found), {artifact.key for artifact in validation.ARTIFACTS}
        )
        records = _artifact_records()
        records[0]["workflow_run"] = {"id": int(validation.RUN_ID) + 1}
        with self.assertRaises(validation.ValidationError):
            validation._require_artifacts(records)
        with self.assertRaises(validation.ValidationError):
            validation._require_artifacts(_artifact_records()[:-1])
        records = _artifact_records()
        records[0]["digest"] = "sha256:bad"
        with self.assertRaises(validation.ValidationError):
            validation._require_artifacts(records)
        records = _artifact_records()
        records[0]["id"] = "https://invalid.example/artifact"
        with self.assertRaises(validation.ValidationError):
            validation._require_artifacts(records)

    def test_safe_zip_extracts_exact_regular_member_allowlist(self) -> None:
        spec = validation.ARTIFACTS[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "report.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("report.json", b"{}")
            destination = root / "out"
            validation._safe_extract_zip(archive_path, destination, spec)
            self.assertEqual((destination / "report.json").read_bytes(), b"{}")

    def test_zip_rejects_traversal_symlinks_and_duplicate_names(self) -> None:
        spec = validation.ARTIFACTS[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../report.json", b"{}")
            with self.assertRaises(validation.ValidationError):
                validation._safe_extract_zip(traversal, root / "out-traversal", spec)

            symlink = root / "symlink.zip"
            info = zipfile.ZipInfo("report.json")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr(info, b"target")
            with self.assertRaises(validation.ValidationError):
                validation._safe_extract_zip(symlink, root / "out-symlink", spec)

            duplicate = root / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as archive:
                    archive.writestr("report.json", b"{}")
                    archive.writestr("report.json", b"{}")
            with self.assertRaises(validation.ValidationError):
                validation._safe_extract_zip(duplicate, root / "out-duplicate", spec)

    def test_independent_image_ids_must_match_handoff_exactly(self) -> None:
        backend = "sha256:" + "1" * 64
        nginx = "sha256:" + "2" * 64
        report = {
            "contract_sha256": "a" * 64,
            "types": {"status": "PASS"},
            "api": _image_record(backend),
            "frontend": _image_record(nginx),
            "smoke": "PASS",
        }
        handoff = {
            "images": {
                "backend-image": {"image_id": backend},
                "nginx-image": {"image_id": nginx},
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text(json.dumps(report))
            self.assertEqual(
                validation._validate_image_report(path, handoff),
                {"backend-image": backend, "nginx-image": nginx},
            )
            handoff["images"]["backend-image"]["image_id"] = "sha256:" + "3" * 64
            with self.assertRaises(validation.ValidationError):
                validation._validate_image_report(path, handoff)

    def test_work_directory_must_be_private_and_exact_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}):
                expected = root / f"infrapulse-f3c3-runner-{validation.RUN_ID}"
                self.assertEqual(
                    validation._work_dir(expected, must_exist=False), expected
                )
                unexpected = root / "other"
                with self.assertRaises(validation.ValidationError):
                    validation._work_dir(unexpected, must_exist=False)
                expected.mkdir(mode=0o700)
                self.assertEqual(
                    validation._work_dir(expected, must_exist=True), expected
                )

    def test_disk_capacity_failure_is_controlled(self) -> None:
        with (
            patch.object(validation, "_free_bytes", return_value=10),
            self.assertRaises(validation.ValidationError),
        ):
            validation.require_free_space(Path("/tmp"), 11, "test")

    def test_local_shared_docker_is_rejected_before_subprocess(self) -> None:
        with (
            patch.dict(os.environ, {"RUNNER_ENVIRONMENT": "self-hosted"}, clear=False),
            patch.object(validation, "_safe_subprocess") as run,
            self.assertRaises(validation.ValidationError) as raised,
        ):
            validation._docker_root()
        self.assertIsNotNone(raised.exception)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
