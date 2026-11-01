"""Security policy regressions use synthetic data and no Docker or network."""

import base64
import copy
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import check_secrets as secrets
import check_supply_chain as policy


class DependencyPolicyTests(unittest.TestCase):
    def npm(self) -> tuple[dict, dict]:
        package = {"dependencies": {"demo": "1.2.3"}}
        lock = {
            "lockfileVersion": 3,
            "packages": {
                "": copy.deepcopy(package),
                "node_modules/demo": {
                    "version": "1.2.3",
                    "resolved": "https://registry.npmjs.org/demo/-/demo-1.2.3.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"a" * 64).decode(),
                },
            },
        }
        return lock, package

    def test_npm_valid(self) -> None:
        self.assertEqual(policy.npm_violations(*self.npm()), [])

    def test_npm_missing_invalid_hash_and_untrusted_origin(self) -> None:
        for field, value in (
            ("integrity", ""),
            ("integrity", "sha512-bad"),
            ("resolved", "https://user:password@registry.npmjs.org/demo.tgz"),
            ("resolved", "http://registry.npmjs.org/demo.tgz"),
            ("link", True),
            ("version", "latest"),
        ):
            with self.subTest(field=field, value=value):
                lock, package = self.npm()
                lock["packages"]["node_modules/demo"][field] = value
                errors = policy.npm_violations(lock, package)
                self.assertTrue(errors)
                self.assertNotIn("password", json.dumps(errors))

    def test_npm_empty_mismatch_and_floating(self) -> None:
        self.assertTrue(policy.npm_violations({}, {}))
        lock, package = self.npm()
        package["dependencies"]["demo"] = "^1.2.3"
        self.assertEqual(len(policy.npm_violations(lock, package)), 2)

    def test_python_hashes_and_build_tools(self) -> None:
        text = (
            "setuptools==82.0.0 \\\n    --hash=sha256:"
            + "a" * 64
            + "\npip==26.0.0 --hash=sha256:"
            + "b" * 64
        )
        errors, hashed = policy.python_violations(text)
        self.assertEqual(errors, [])
        self.assertEqual(
            policy.build_violations(
                {"build-system": {"requires": ["setuptools==82.0.0"]}}, hashed
            ),
            [],
        )

    def test_python_unhashed_floating_duplicate_and_empty_fail(self) -> None:
        for text in ("demo==1.0", "demo>=1", "", "demo==1\ndemo==1"):
            with self.subTest(text=text):
                self.assertTrue(policy.python_violations(text)[0])
        self.assertTrue(
            policy.build_violations(
                {"build-system": {"requires": ["setuptools>=75,<83"]}}, set()
            )
        )

    def test_images_digest_and_internal_stage(self) -> None:
        self.assertEqual(
            policy.image_violations(
                "FROM python:3.12@sha256:" + "a" * 64 + " AS base\nFROM base AS test"
            ),
            [],
        )
        self.assertEqual(policy.image_violations("FROM scratch"), [])

    def test_images_mutable_missing_and_invalid_digest_fail(self) -> None:
        for text in ("FROM python:3.12", "FROM x@sha256:bad", "", "FROM ${BASE}"):
            with self.subTest(text=text):
                self.assertTrue(policy.image_violations(text))


class WorkflowPolicyTests(unittest.TestCase):
    def test_informational_handoff_uses_real_outcomes_without_weakening_required_steps(
        self,
    ) -> None:
        doc = policy.yaml_document(
            policy.ROOT, policy.ROOT / ".github/workflows/ci.yml"
        )
        jobs = doc["jobs"]
        docker = jobs["docker-build"]
        steps = {s["id"]: s for s in docker["steps"] if "id" in s}
        tolerated = {s.get("id") for s in docker["steps"] if s.get("continue-on-error")}
        self.assertEqual(tolerated, {"image_export", "image_upload"})
        self.assertNotIn("continue-on-error", docker)
        for stage in ("image_export", "image_upload"):
            self.assertEqual(
                docker["outputs"][stage], "${{ steps." + stage + ".outcome }}"
            )
            self.assertIs(steps[stage]["continue-on-error"], True)
        self.assertEqual(
            steps["image_upload"]["if"],
            "${{ success() && steps.image_export.outcome == 'success' }}",
        )
        required = next(
            s
            for s in docker["steps"]
            if s["name"] == "Build inspect and smoke runtime images without publishing"
        )
        self.assertNotIn("continue-on-error", required)
        self.assertNotIn("--audit-archives", required["run"])
        self.assertIn("--retain-images", required["run"])
        cleanup = next(
            s
            for s in docker["steps"]
            if s["name"] == "Clean up only owned containers and image tags"
        )
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertNotIn("continue-on-error", cleanup)
        self.assertLess(
            docker["steps"].index(steps["image_export"]), docker["steps"].index(cleanup)
        )
        audit_job = jobs["vulnerability-audit"]
        self.assertNotIn("continue-on-error", audit_job)
        self.assertFalse(any(s.get("continue-on-error") for s in audit_job["steps"]))
        download = next(s for s in audit_job["steps"] if s.get("id") == "images")
        self.assertNotIn("name", download["with"])
        self.assertEqual(
            download["with"]["pattern"], steps["image_upload"]["with"]["name"]
        )
        self.assertEqual(download["with"]["digest-mismatch"], "error")
        self.assertIn(
            "needs.docker-build.outputs.image_upload == 'success'", download["if"]
        )
        run = next(
            s for s in audit_job["steps"] if "--preparation-result" in s.get("run", "")
        )
        for output in ("image_export", "image_upload"):
            self.assertIn("needs.docker-build.outputs." + output, run["run"])
        self.assertIn("steps.images.outcome", run["run"])

    def workflow(self) -> dict:
        return {
            "on": {"push": {}},
            "permissions": {"contents": "read"},
            "jobs": {
                "check": {
                    "timeout-minutes": 5,
                    "steps": [
                        {
                            "uses": "actions/checkout@" + "a" * 40,
                            "with": {"persist-credentials": False},
                        }
                    ],
                }
            },
        }

    def test_valid_workflow(self) -> None:
        self.assertEqual(policy.workflow_violations(self.workflow()), [])

    def test_permissions_and_target_fail(self) -> None:
        for change in (
            {"permissions": "write-all"},
            {"permissions": {"contents": "read", "id-token": "write"}},
            {"on": {"pull_request_target": {}}},
        ):
            doc = self.workflow()
            doc.update(change)
            self.assertTrue(policy.workflow_violations(doc))
        doc = self.workflow()
        doc["jobs"]["check"]["permissions"] = {"packages": "write"}
        self.assertTrue(policy.workflow_violations(doc))

    def test_actions_read_exception_is_scoped_and_read_only(self) -> None:
        path = ".github/workflows/f3c3-real-validation.yml"
        doc = self.workflow()
        doc["on"] = {"workflow_dispatch": None}
        doc["permissions"] = {"contents": "read", "actions": "read"}
        job = doc["jobs"].pop("check")
        job["if"] = "github.ref == 'refs/heads/main'"
        job["permissions"] = {"contents": "read", "actions": "read"}
        doc["jobs"]["integrated-validation"] = job
        self.assertEqual(policy.workflow_violations(doc, path), [])
        for other in (None, ".github/workflows/other.yml", "./" + path, path.upper()):
            with self.subTest(path=other):
                self.assertTrue(policy.workflow_violations(doc, other))
        for level, permissions in (
            ("workflow", {"contents": "read", "actions": "write"}),
            ("workflow", {"contents": "read", "actions": "read", "id-token": "write"}),
            ("workflow", {"actions": "read"}),
            ("job", {"contents": "read", "actions": "write"}),
            ("job", {"contents": "read", "actions": "read", "id-token": "write"}),
            ("job", {"contents": "read", "packages": "read"}),
        ):
            changed = copy.deepcopy(doc)
            if level == "workflow":
                changed["permissions"] = permissions
            else:
                changed["jobs"]["integrated-validation"]["permissions"] = permissions
            with self.subTest(level=level, permissions=permissions):
                self.assertTrue(policy.workflow_violations(changed, path))
        for change in ("trigger", "condition", "extra_job"):
            changed = copy.deepcopy(doc)
            if change == "trigger":
                changed["on"]["push"] = None
            elif change == "condition":
                changed["jobs"]["integrated-validation"]["if"] = "true"
            else:
                changed["jobs"]["other"] = copy.deepcopy(job)
            with self.subTest(change=change):
                self.assertTrue(policy.workflow_violations(changed, path))

    def test_action_tags_partial_sha_credentials_timeout_fail(self) -> None:
        for ref in ("actions/checkout@v4", "actions/checkout@abcdef", "./local"):
            doc = self.workflow()
            doc["jobs"]["check"]["steps"][0]["uses"] = ref
            self.assertTrue(policy.workflow_violations(doc))
        doc = self.workflow()
        del doc["jobs"]["check"]["steps"][0]["with"]
        self.assertTrue(policy.workflow_violations(doc))
        doc = self.workflow()
        del doc["jobs"]["check"]["timeout-minutes"]
        self.assertTrue(policy.workflow_violations(doc))

    def test_real_yaml_parser_rejects_duplicate_keys_and_custom_tags(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "workflow.yml"
            for source in ("permissions: {}\npermissions: {}", "a: !custom text"):
                path.write_text(source)
                with self.assertRaises(ValueError):
                    policy.yaml_document(policy.ROOT, path)
            path.write_text("on: push\npermissions:\n  contents: read\n")
            self.assertEqual(policy.yaml_document(policy.ROOT, path)["on"], "push")

    def test_pending_rules_remain_fail_and_all_enforcement_blocks(self) -> None:
        for mode, expected in (("active", 0), ("all", 1)):
            with (
                patch("sys.argv", ["policy", "--enforce", mode]),
                patch.object(
                    policy,
                    "audit",
                    return_value={
                        "npm-integrity": [],
                        "actions-policy": [],
                        "base-images": [],
                        "build-tools": [],
                        "python-hashes": ["Missing hashes"],
                    },
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(policy.main(), expected)
                report = json.loads(output.getvalue())
                self.assertEqual(report["rules"]["python-hashes"]["status"], "FAIL")

    def test_policy_parse_failure_is_closed(self) -> None:
        with (
            patch("sys.argv", ["policy"]),
            patch.object(policy, "audit", side_effect=ValueError("private-value")),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(policy.main(), 2)
            self.assertNotIn("private-value", output.getvalue())

    def test_missing_rule_cannot_silently_pass(self) -> None:
        with (
            patch("sys.argv", ["policy"]),
            patch.object(policy, "audit", return_value={}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(policy.main(), 2)

    def test_aggregator_preserves_original_jobs_and_rejects_every_bad_result(
        self,
    ) -> None:
        doc = policy.yaml_document(
            policy.ROOT, policy.ROOT / ".github/workflows/ci.yml"
        )
        required = doc["jobs"]["ci-required"]
        expected = {
            "backend-quality",
            "backend-unit",
            "api-contract",
            "frontend-quality",
            "frontend-tests",
            "frontend-build",
            "backend-postgres",
            "playwright-e2e",
            "observability-integration",
            "docker-build",
            "security-foundations",
        }
        self.assertEqual(set(required["needs"]), expected)
        self.assertEqual(required["name"], "ci-required")
        self.assertEqual(required["if"], "${{ always() }}")
        script = (
            required["steps"][0]["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        )
        results = {name: {"result": "success"} for name in expected}
        cases = [({}, 1), (copy.deepcopy(results), 0)]
        for name in expected:
            for outcome in ("failure", "cancelled", "skipped"):
                mutated = copy.deepcopy(results)
                mutated[name]["result"] = outcome
                cases.append((mutated, 1))
        for case, expected_code in cases:
            run = subprocess.run(
                ["python3", "-c", script],
                env={"NEEDS_JSON": json.dumps(case)},
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(run.returncode, expected_code)


class SecretScannerTests(unittest.TestCase):
    def test_clean_and_positive_reports_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for code, findings in (
                (0, []),
                (
                    1,
                    [
                        {
                            "RuleID": "github-pat",
                            "File": "credential-in-path",
                            "StartLine": 2,
                            "Secret": "private-value",
                            "Match": "private-value",
                        }
                    ],
                ),
            ):

                def run(
                    *args: object,
                    _code: int = code,
                    _findings: list = findings,
                    **kwargs: object,
                ) -> subprocess.CompletedProcess:
                    (root / "raw.json").write_text(json.dumps(_findings))
                    self.assertEqual(kwargs["env"], secrets.controlled_env(root))
                    command = args[0]
                    self.assertIn("--ignore-gitleaks-allow", command)
                    self.assertIn("--redact=100", command)
                    return subprocess.CompletedProcess(
                        [], _code, b"private-value", b"private-value"
                    )

                with patch.object(secrets.subprocess, "run", side_effect=run):
                    result = secrets.scan(root / "engine", "dir", root, root)
                    self.assertEqual(result["status"], "FAIL" if code else "PASS")
                    self.assertNotIn("private-value", json.dumps(result))
                    self.assertNotIn("credential-in-path", json.dumps(result))

    def test_missing_malformed_mismatched_and_engine_failure_fail_closed(self) -> None:
        for code, content in (
            (0, None),
            (0, "invalid"),
            (1, "[]"),
            (0, "{}"),
            (17, "[]"),
            (-15, "[]"),
        ):
            with (
                self.subTest(code=code, content=content),
                tempfile.TemporaryDirectory() as name,
            ):
                root = Path(name)

                def run(
                    *args: object,
                    _root: Path = root,
                    _content: str | None = content,
                    _code: int = code,
                    **kwargs: object,
                ) -> subprocess.CompletedProcess:
                    if _content is not None:
                        (_root / "raw.json").write_text(_content)
                    return subprocess.CompletedProcess([], _code)

                with (
                    patch.object(secrets.subprocess, "run", side_effect=run),
                    self.assertRaises(ValueError),
                ):
                    secrets.scan(root / "engine", "dir", root, root)

    def test_archive_tamper_fails_before_execution(self) -> None:
        with (
            tempfile.TemporaryDirectory() as name,
            patch.object(secrets.subprocess, "run") as run,
        ):
            with self.assertRaises(ValueError):
                secrets.install(b"tampered", Path(name))
            run.assert_not_called()

    def test_fixture_false_negative_and_false_positive_are_rejected(self) -> None:
        for responses in (
            [{"status": "PASS", "findings": []}] * 2,
            [{"status": "FAIL", "findings": [{"rule": "github-pat"}]}] * 2,
        ):
            with (
                tempfile.TemporaryDirectory() as name,
                patch.object(secrets, "scan", side_effect=responses),
                self.assertRaises(ValueError),
            ):
                secrets.fixtures(Path("engine"), Path(name))

    def test_timeout_and_bad_inventory_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with (
                patch.object(
                    secrets.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("engine", 1),
                ),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                secrets.scan(root / "engine", "dir", root, root)
            for inventory in (b"", b"missing\0", b"link\0"):
                (root / "link").unlink(missing_ok=True)
                (root / "link").symlink_to("/etc/passwd")
                with (
                    patch.object(
                        secrets.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0, inventory),
                    ),
                    self.assertRaises(ValueError),
                ):
                    secrets.snapshot(root, root / "snapshot")

    def test_shallow_history_fails_before_engine_installation(self) -> None:
        with (
            patch("sys.argv", ["secrets"]),
            patch.object(
                secrets.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "true\n"),
            ),
            patch.object(secrets, "install") as install,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(secrets.main(), 2)
            install.assert_not_called()

    def test_download_failure_is_closed_and_sanitized(self) -> None:
        with (
            patch("sys.argv", ["secrets"]),
            patch.object(
                secrets.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "false\n"),
            ),
            patch.object(
                secrets.urllib.request, "urlopen", side_effect=OSError("private-value")
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(secrets.main(), 2)
            self.assertNotIn("private-value", output.getvalue())

    def test_cleanup_failure_remains_nonzero_and_keeps_findings(self) -> None:
        class BrokenCleanup:
            def __enter__(self) -> str:
                return name

            def __exit__(self, *args: object) -> None:
                raise OSError("private-value")

        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / "archive"
            archive.write_bytes(b"controlled")
            for status in ("PASS", "FAIL"):
                checkout = Path(name) / "checkout"
                if checkout.exists():
                    checkout.rmdir()
                with (
                    patch("sys.argv", ["secrets", "--archive", str(archive)]),
                    patch.object(
                        secrets.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0, "false\n"),
                    ),
                    patch.object(
                        secrets.tempfile,
                        "TemporaryDirectory",
                        return_value=BrokenCleanup(),
                    ),
                    patch.object(
                        secrets, "install", return_value=Path(name) / "engine"
                    ),
                    patch.object(secrets, "fixtures"),
                    patch.object(secrets, "snapshot"),
                    patch.object(
                        secrets, "scan", return_value={"status": status, "findings": []}
                    ),
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(secrets.main(), 2)
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["history"]["status"], status)
                    self.assertEqual(report["status"], "ERROR")
                    self.assertNotIn("private-value", output.getvalue())

    def test_environment_cannot_override_config_or_inherit_credentials(self) -> None:
        with patch.dict(
            "os.environ", {"GITLEAKS_CONFIG": "bad", "TOKEN": "private-value"}
        ):
            environment = secrets.controlled_env(Path("/tmp/test"))
            self.assertNotIn("TOKEN", environment)
            self.assertNotIn("GITLEAKS_CONFIG", environment)

    def test_missing_archive_error_is_nonzero_and_sanitized(self) -> None:
        with (
            patch("sys.argv", ["secrets", "--archive", "/missing/private-value"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(secrets.main(), 2)
            self.assertNotIn("private-value", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
