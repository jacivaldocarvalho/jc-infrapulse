"""Offline trust-policy and Cosign wrapper regressions."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import provenance_signature as signature


class SignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.statement = self.root / "statement.json"
        self.statement_bytes = b'{ "_type": "test", "subject": [] }\n'
        self.statement.write_bytes(self.statement_bytes)
        self.bundle = self.root / "bundle.json"
        self.bundle.write_text('{"test":"bundle"}')
        self.private = self.root / "test-only.key"
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "EC",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-out",
                str(self.private),
            ],
            check=True,
            capture_output=True,
        )
        self.sbom = self.root / "sbom.json"
        self.sbom.write_text("{}")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text("{}")
        self.trivy = self.root / "trivy"
        self.trivy.write_text("fixture")
        self.cosign = self.root / "cosign"
        self.cosign.write_text("fixture")
        public = subprocess.run(
            ["openssl", "pkey", "-in", str(self.private), "-pubout"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.pem = public.stdout
        fingerprint = hashlib.sha256(self.pem.encode()).hexdigest()
        self.key_id = f"sha256:{fingerprint}"
        self.policy = self.root / "trust-policy.json"
        self.policy_data = {
            "schema_version": 1,
            "keys": {
                self.key_id: {
                    "public_key": self.pem,
                    "sha256": fingerprint,
                    "status": "ACTIVE",
                }
            },
        }
        self.write_policy()

    def write_policy(self) -> str:
        raw = json.dumps(self.policy_data, separators=(",", ":")).encode()
        self.policy.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def verifier_mocks(self):
        stack = ExitStack()
        stack.enter_context(patch.object(signature, "verify_cosign"))
        stack.enter_context(patch.object(signature.provenance, "verify"))
        return stack

    def verify(self, policy_hash: str | None = None) -> dict[str, str]:
        with (
            self.verifier_mocks(),
            patch.object(signature, "_run_cosign") as run_cosign,
        ):
            result = signature.verify_signature(
                self.statement,
                self.bundle,
                self.policy,
                policy_hash or hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                self.key_id,
                self.cosign,
                self.sbom,
                self.manifest,
                self.trivy,
            )
            args = run_cosign.call_args.args[1]
            self.assertIn("--offline", args)
            self.assertEqual(args[args.index("--key") - 1], "--offline")
            return result

    def test_valid_signature_reports_separate_states_and_uses_policy_key(self) -> None:
        with (
            self.verifier_mocks(),
            patch.object(signature, "_run_cosign") as run_cosign,
        ):
            result = signature.verify_signature(
                self.statement,
                self.bundle,
                self.policy,
                hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                self.key_id,
                self.cosign,
                self.sbom,
                self.manifest,
                self.trivy,
            )
            self.assertEqual(
                result,
                {
                    "integrity": "INTEGRITY_VERIFIED",
                    "signature": "SIGNATURE_VALID",
                    "signer": "TRUSTED_SIGNER",
                },
            )
            args = run_cosign.call_args.args[1]
            self.assertEqual(args[0], "verify-blob")
            self.assertIn("--offline", args)
            self.assertNotIn(str(self.key_id), args)

    def test_policy_tampering_and_unknown_key_fail_closed(self) -> None:
        with (
            patch.object(signature, "verify_cosign"),
            patch.object(signature.provenance, "verify"),
            patch.object(signature, "_run_cosign") as run_cosign,
        ):
            with self.assertRaisesRegex(ValueError, "policy SHA-256"):
                signature.verify_signature(
                    self.statement,
                    self.bundle,
                    self.policy,
                    "0" * 64,
                    self.key_id,
                    self.cosign,
                    self.sbom,
                    self.manifest,
                    self.trivy,
                )
            run_cosign.assert_not_called()
            with self.assertRaisesRegex(ValueError, "not authorized"):
                signature.verify_signature(
                    self.statement,
                    self.bundle,
                    self.policy,
                    hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                    "sha256:" + "0" * 64,
                    self.cosign,
                    self.sbom,
                    self.manifest,
                    self.trivy,
                )

    def test_revoked_key_and_policy_key_mutation_rejected(self) -> None:
        self.policy_data["keys"][self.key_id]["status"] = "REVOKED"
        digest = self.write_policy()
        with self.assertRaisesRegex(ValueError, "revoked or inactive"):
            signature._trusted_key(self.policy, digest, self.key_id)
        self.policy_data["keys"][self.key_id]["status"] = "ACTIVE"
        self.policy_data["keys"][self.key_id]["public_key"] += "changed"
        digest = self.write_policy()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            signature._trusted_key(self.policy, digest, self.key_id)

    def test_statement_change_is_rejected_by_integrity_before_cosign(self) -> None:
        with (
            patch.object(signature, "verify_cosign"),
            patch.object(
                signature.provenance,
                "verify",
                side_effect=ValueError("statement does not match manifest"),
            ),
            patch.object(signature, "_run_cosign") as run_cosign,
        ):
            self.statement.write_text('{"_type":"tampered"}')
            with self.assertRaisesRegex(ValueError, "statement does not match"):
                signature.verify_signature(
                    self.statement,
                    self.bundle,
                    self.policy,
                    hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                    self.key_id,
                    self.cosign,
                    self.sbom,
                    self.manifest,
                    self.trivy,
                )
            run_cosign.assert_not_called()

    def test_byte_tampering_of_statement_or_bundle_fails_cosign_verification(
        self,
    ) -> None:
        for target, changed in (
            (self.statement, b'{  "_type": "test", "subject": [] }\n'),
            (self.bundle, b'{"test":"replaced bundle"}'),
        ):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                target.write_bytes(changed)
                with (
                    self.verifier_mocks(),
                    patch.object(
                        signature,
                        "_run_cosign",
                        side_effect=ValueError("Cosign signature verification failed"),
                    ),
                    self.assertRaisesRegex(
                        ValueError, "Cosign signature verification failed"
                    ),
                ):
                    signature.verify_signature(
                        self.statement,
                        self.bundle,
                        self.policy,
                        hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                        self.key_id,
                        self.cosign,
                        self.sbom,
                        self.manifest,
                        self.trivy,
                    )
                target.write_bytes(original)

    def test_cosign_failure_never_falls_back_to_hash_only(self) -> None:
        with (
            self.verifier_mocks(),
            patch.object(
                signature,
                "_run_cosign",
                side_effect=ValueError("Cosign operation failed"),
            ),
            self.assertRaisesRegex(ValueError, "Cosign operation failed"),
        ):
            signature.verify_signature(
                self.statement,
                self.bundle,
                self.policy,
                hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                self.key_id,
                self.cosign,
                self.sbom,
                self.manifest,
                self.trivy,
            )

    def test_malformed_statement_bundle_policy_and_symlink_rejected(self) -> None:
        self.statement.write_text("{")
        with self.assertRaisesRegex(ValueError, "Malformed JSON"):
            signature._json_file(self.statement, 100)
        self.statement.write_bytes(self.statement_bytes)
        self.bundle.write_text("not json")
        with self.assertRaisesRegex(ValueError, "Malformed JSON"):
            signature._json_file(self.bundle, 100)
        self.bundle.write_text("{}")
        symlink = self.root / "policy-link.json"
        symlink.symlink_to(self.policy)
        with self.assertRaises(OSError):
            signature._json_file(symlink, 1000)
        with self.assertRaises(ValueError):
            signature._trusted_key(self.policy, "bad", self.key_id)

    def test_signs_exact_verified_statement_and_preserves_output_on_failure(
        self,
    ) -> None:
        bundle = self.root / "new-bundle.json"
        observed: list[bytes] = []

        def fake_cosign(_tool: Path, arguments: list[str], timeout: int = 120) -> bytes:
            if arguments:
                statement_path = Path(arguments[-1])
                observed.append(statement_path.read_bytes())
                bundle_temp = Path(arguments[arguments.index("--bundle") + 1])
                bundle_temp.write_text('{"mediaType":"test"}')
            return b"GitVersion: v3.1.3\n"

        with (
            patch.object(signature, "verify_cosign"),
            patch.object(signature.provenance, "verify"),
            patch.object(signature.sbom, "check_output"),
            patch.object(signature, "_run_cosign", side_effect=fake_cosign),
        ):
            signature.sign_statement(
                self.statement,
                bundle,
                self.private,
                self.cosign,
                self.sbom,
                self.manifest,
                self.trivy,
            )
        self.assertEqual(observed, [self.statement_bytes])
        self.assertEqual(json.loads(bundle.read_text()), {"mediaType": "test"})

        bundle.write_text("keep")
        with (
            patch.object(signature, "verify_cosign"),
            patch.object(signature.provenance, "verify"),
            patch.object(signature.sbom, "check_output"),
            patch.object(
                signature,
                "_run_cosign",
                side_effect=ValueError("Cosign operation failed"),
            ),
            self.assertRaises(ValueError),
        ):
            signature.sign_statement(
                self.statement,
                bundle,
                self.private,
                self.cosign,
                self.sbom,
                self.manifest,
                self.trivy,
            )
        self.assertEqual(bundle.read_text(), "keep")

    def test_binary_hash_and_version_are_both_required(self) -> None:
        digest = hashlib.sha256(self.cosign.read_bytes()).hexdigest()
        lock = {
            "schema_version": 1,
            "name": "cosign",
            "version": "3.1.3",
            "platform": "linux-amd64",
            "sha256": digest,
        }
        lock_path = self.root / "lock.json"
        lock_path.write_text(json.dumps(lock))
        with (
            patch.object(signature, "COSIGN_LOCK", lock_path),
            patch.object(
                signature.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "GitVersion: v3.1.3\n", ""
                ),
            ) as run,
        ):
            signature.verify_cosign(self.cosign)
            self.assertIn("version", run.call_args.args[0])
        lock["sha256"] = "0" * 64
        lock_path.write_text(json.dumps(lock))
        with (
            patch.object(signature, "COSIGN_LOCK", lock_path),
            self.assertRaisesRegex(ValueError, "SHA-256"),
        ):
            signature.verify_cosign(self.cosign)
        lock["sha256"] = digest
        lock["version"] = "3.1.2"
        lock_path.write_text(json.dumps(lock))
        with (
            patch.object(signature, "COSIGN_LOCK", lock_path),
            patch.object(
                signature.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "GitVersion: v3.1.3\n", ""
                ),
            ),
            self.assertRaisesRegex(ValueError, "version mismatch"),
        ):
            signature.verify_cosign(self.cosign)


if __name__ == "__main__":
    unittest.main()
