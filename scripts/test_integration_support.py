"""Exercise ownership and failure safety without accessing a Docker daemon."""

import io
import os
import runpy
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from integration_support import IntegrationProject


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "RUNNER_TEMP": self.directory.name,
                "INFRAPULSE_TEST_PROJECT": "infrapulse-e2e-unit",
                "GITHUB_ACTIONS": "false",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.project = IntegrationProject("e2e", ["compose.frontend.test.yaml"])

    def test_unexpected_project_name_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"INFRAPULSE_TEST_PROJECT": "jc-infrapulse"}),
            self.assertRaises(ValueError),
        ):
            IntegrationProject("e2e", ["compose.frontend.test.yaml"])

    def test_preexisting_project_never_owned(self) -> None:
        with (
            patch.object(self.project, "resources", return_value="existing"),
            self.assertRaises(RuntimeError),
        ):
            self.project.__enter__()
        self.assertFalse(self.project.marker.exists())

    def test_daemon_failure_never_enables_cleanup(self) -> None:
        with (
            patch.object(
                self.project,
                "resources",
                side_effect=subprocess.CalledProcessError(1, "docker"),
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.project.__enter__()
        self.assertFalse(self.project.marker.exists())

    def test_cleanup_without_ownership_does_not_call_docker(self) -> None:
        with patch.object(self.project, "run") as run:
            self.project.cleanup()
            run.assert_not_called()

    def test_invalid_marker_never_calls_docker(self) -> None:
        self.project.marker.write_text("another-project")
        with patch.object(self.project, "run") as run:
            with self.assertRaises(RuntimeError):
                self.project.cleanup()
            run.assert_not_called()

    def test_cleanup_failure_retains_marker_and_fails(self) -> None:
        self.project.marker.write_text(self.project.name)
        with (
            patch.object(
                self.project,
                "run",
                side_effect=subprocess.CalledProcessError(17, "docker"),
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.project.cleanup()
        self.assertTrue(self.project.marker.exists())

    def test_context_preserves_original_failure_after_cleanup(self) -> None:
        with (
            patch.object(self.project, "resources", return_value=""),
            patch.object(self.project, "snapshot", return_value={}),
            patch.object(self.project, "run") as run,
            patch("integration_support.signal.signal"),
        ):
            with self.assertRaisesRegex(RuntimeError, "original failure"), self.project:
                raise RuntimeError("original failure")
            self.assertEqual(
                run.call_args.args[0],
                [
                    *self.project.compose,
                    "down",
                    "--volumes",
                    "--remove-orphans",
                    "--timeout",
                    "30",
                ],
            )
        self.assertFalse(self.project.marker.exists())

    def test_marker_exclusive_creation_preserves_existing_owner(self) -> None:
        self.project.marker.write_text("previous-owner")
        with (
            patch.object(self.project, "resources", return_value=""),
            patch.object(self.project, "snapshot", return_value={}),
            self.assertRaises(FileExistsError),
        ):
            self.project.__enter__()
        self.assertEqual(Path(self.project.marker).read_text(), "previous-owner")


class EntrypointStatusTests(unittest.TestCase):
    def run_entrypoint(
        self, script: str, body: str
    ) -> subprocess.CompletedProcess[str]:
        scripts = Path(__file__).resolve().parent
        target = scripts / script
        # Replace only main's work; execute the actual imports and CLI dispatch.
        probe = (
            "import ast, sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(scripts)!r})\n"
            f"target = {str(target)!r}\n"
            "tree = ast.parse(Path(target).read_text())\n"
            f"replacement = ast.parse({body!r}).body\n"
            "for node in tree.body:\n"
            " if isinstance(node, ast.FunctionDef) and node.name == 'main':\n"
            "  node.body = replacement\n"
            "exec(compile(ast.fix_missing_locations(tree), target, 'exec'), "
            "{'__name__': '__main__', '__file__': target})\n"
        )
        return subprocess.run(
            [sys.executable, "-c", probe],
            # Synthetic subprocesses must not inherit the CI Compose project or secrets.
            env={
                "INFRAPULSE_TEST_PROJECT": "infrapulse-e2e-unit",
                "GITHUB_ACTIONS": "false",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_entrypoint_subprocess_environment_is_isolated(self) -> None:
        before = dict(os.environ)
        for script in ["validate-dashboard.py", "validate-observability.py"]:
            with self.subTest(script=script):
                result = self.run_entrypoint(
                    script,
                    "import os\n"
                    "assert os.environ['INFRAPULSE_TEST_PROJECT'] == 'infrapulse-e2e-unit'\n"
                    "assert os.environ['GITHUB_ACTIONS'] == 'false'\n"
                    # CPython may add LC_CTYPE during locale coercion at startup.
                    "assert set(os.environ) <= "
                    "{'INFRAPULSE_TEST_PROJECT', 'GITHUB_ACTIONS', 'LC_CTYPE'}\n"
                    "print('isolated environment')",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("isolated environment", result.stdout)
        self.assertEqual(dict(os.environ), before)

    def test_project_guard_outside_synthetic_environment(self) -> None:
        scripts = Path(__file__).resolve().parent
        probe = (
            f"import sys; sys.path.insert(0, {str(scripts)!r})\n"
            "from integration_support import IntegrationProject\n"
            "IntegrationProject('e2e', [])"
        )
        for name, valid in [
            ("infrapulse-e2e-valid", True),
            ("infrapulse-observability-41067075376-1", False),
            ("jc-infrapulse", False),
            ("infrapulse-e2e-INVALID", False),
        ]:
            with self.subTest(project=name):
                result = subprocess.run(
                    [sys.executable, "-c", probe],
                    env={"INFRAPULSE_TEST_PROJECT": name, "GITHUB_ACTIONS": "false"},
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                if valid:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Unexpected disposable project name", result.stderr)

    def test_numeric_exit_statuses(self) -> None:
        for script in ["validate-dashboard.py", "validate-observability.py"]:
            for code in [0, 1, 17, 127]:
                with self.subTest(script=script, code=code):
                    result = self.run_entrypoint(
                        script,
                        "import subprocess, sys\n"
                        f"subprocess.run([sys.executable, '-c', 'raise SystemExit({code})'], check=True)",
                    )
                    self.assertEqual(result.returncode, code)

    def test_signal_exit_statuses(self) -> None:
        for script in ["validate-dashboard.py", "validate-observability.py"]:
            for signum in [signal.SIGTERM, signal.SIGKILL]:
                with self.subTest(script=script, signal=signum):
                    result = self.run_entrypoint(
                        script,
                        "import subprocess, sys\n"
                        "subprocess.run([sys.executable, '-c', "
                        f"'import os; os.kill(os.getpid(), {int(signum)})'], check=True)",
                    )
                    self.assertEqual(result.returncode, 128 + signum)

    def test_invalid_exception_codes_cannot_become_success(self) -> None:
        for script in ["validate-dashboard.py", "validate-observability.py"]:
            for code in [0, 256, 512]:
                with self.subTest(script=script, code=code):
                    result = self.run_entrypoint(
                        script,
                        "import subprocess\n"
                        f"raise subprocess.CalledProcessError({code}, ['controlled-test'])",
                    )
                    self.assertNotEqual(result.returncode, 0)

    def test_useful_traceback_and_command_diagnostic(self) -> None:
        for script in ["validate-dashboard.py", "validate-observability.py"]:
            with self.subTest(script=script):
                result = self.run_entrypoint(
                    script,
                    "import subprocess\n"
                    "raise subprocess.CalledProcessError(17, ['controlled-primary-command'])",
                )
                self.assertEqual(result.returncode, 17)
                self.assertIn("Traceback", result.stderr)
                self.assertIn("controlled-primary-command", result.stderr)
                self.assertIn("17", result.stderr)

    def test_unexpected_failure_diagnostic_redacts_project_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "synthetic-secret"
            for script in ["validate-dashboard.py", "validate-observability.py"]:
                with self.subTest(script=script):
                    result = self.run_entrypoint(
                        script,
                        "from pathlib import Path\n"
                        "from integration_support import IntegrationProject\n"
                        "project = IntegrationProject('e2e', [])\n"
                        "value = project.env['GRAFANA_ADMIN_PASSWORD']\n"
                        f"Path({str(secret_file)!r}).write_text(value)\n"
                        "raise RuntimeError('preserved diagnostic ' + value)",
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("preserved diagnostic", result.stderr)
                    self.assertFalse(
                        secret_file.read_text() in result.stderr,
                        "Diagnostic exposed the generated synthetic credential",
                    )


class CollectionFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.results = Path(self.directory.name)
        (self.results / "normal.xml").write_text(
            '<testsuites><testsuite><testcase name="controlled"/></testsuite></testsuites>'
        )
        module = runpy.run_path(
            str(Path(__file__).with_name("validate-observability.py"))
        )
        self.run_case = module["run_case"]

    def invoke(
        self, primary: Exception | str, collection: Exception | str
    ) -> tuple[Exception | None, str]:
        project = Mock(spec=IntegrationProject)
        project.name = "infrapulse-observability-unit"
        project.compose = ["docker", "compose"]
        project.env = dict.fromkeys(
            ["POSTGRES_PASSWORD", "GRAFANA_ADMIN_PASSWORD", "INFRAPULSE_TEST_PASSWORD"],
            "unused-synthetic-credential",
        )
        project.run.side_effect = [primary, collection]
        stdout, stderr = io.StringIO(), io.StringIO()
        failure = None
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                self.run_case(
                    project, "test_real_observability_stack", "normal", self.results
                )
            except (subprocess.CalledProcessError, RuntimeError) as error:
                failure = error
        self.assertEqual(project.run.call_count, 2)
        return failure, stdout.getvalue() + stderr.getvalue()

    def test_primary_failure_and_successful_collection(self) -> None:
        primary = subprocess.CalledProcessError(
            17, ["primary-test"], output="primary output"
        )
        failure, diagnostic = self.invoke(primary, "")
        self.assertIs(failure, primary)
        self.assertIn("primary output", diagnostic)

    def test_primary_failure_and_failed_collection(self) -> None:
        primary = subprocess.CalledProcessError(
            17, ["primary-test"], output="primary output"
        )
        collection = subprocess.CalledProcessError(
            127, ["docker", "cp", "collection-command"]
        )
        failure, diagnostic = self.invoke(primary, collection)
        self.assertIs(failure, primary)
        self.assertIn("collection-command", diagnostic)
        self.assertIn("primary output", diagnostic)

    def test_primary_success_and_failed_collection(self) -> None:
        collection = subprocess.CalledProcessError(
            127, ["docker", "cp", "collection-command"]
        )
        failure, diagnostic = self.invoke("test passed", collection)
        self.assertIs(failure, collection)
        self.assertIn("collection-command", diagnostic)

    def test_complete_success(self) -> None:
        failure, diagnostic = self.invoke("test passed", "")
        self.assertIsNone(failure)
        self.assertIn("test passed", diagnostic)

    def test_unexpected_collection_exceptions(self) -> None:
        for primary in [
            "test passed",
            subprocess.CalledProcessError(17, ["primary-test"]),
        ]:
            with self.subTest(primary_failed=isinstance(primary, Exception)):
                collection = RuntimeError("unexpected collection diagnostic")
                failure, diagnostic = self.invoke(primary, collection)
                self.assertIs(
                    failure, primary if isinstance(primary, Exception) else collection
                )
                self.assertIn("unexpected collection diagnostic", diagnostic)


class SecondaryLifecycleFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "RUNNER_TEMP": self.directory.name,
                "INFRAPULSE_TEST_PROJECT": "infrapulse-e2e-secondary-unit",
                "GITHUB_ACTIONS": "false",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.project = IntegrationProject("e2e", ["compose.frontend.test.yaml"])

    def test_primary_survives_multiple_secondary_failures(self) -> None:
        primary = subprocess.CalledProcessError(17, ["primary-test"])
        cleanup = subprocess.CalledProcessError(23, ["cleanup-command"])
        stderr = io.StringIO()
        with (
            patch.object(self.project, "resources", return_value=""),
            patch.object(
                self.project,
                "snapshot",
                side_effect=[{}, RuntimeError("inspection diagnostic")],
            ),
            patch.object(self.project, "run", side_effect=cleanup),
            patch("integration_support.signal.signal"),
            redirect_stderr(stderr),
            self.assertRaises(subprocess.CalledProcessError) as raised,
            self.project,
        ):
            raise primary
        self.assertIs(raised.exception, primary)
        self.assertIn("cleanup-command", stderr.getvalue())
        self.assertIn("inspection diagnostic", stderr.getvalue())

    def test_success_followed_by_cleanup_failure_fails(self) -> None:
        cleanup = subprocess.CalledProcessError(23, ["cleanup-command"])
        stderr = io.StringIO()
        with (
            patch.object(self.project, "resources", return_value=""),
            patch.object(self.project, "snapshot", return_value={}),
            patch.object(self.project, "run", side_effect=cleanup),
            patch("integration_support.signal.signal"),
            redirect_stderr(stderr),
            self.assertRaises(subprocess.CalledProcessError) as raised,
            self.project,
        ):
            pass
        self.assertIs(raised.exception, cleanup)
        self.assertIn("cleanup-command", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
