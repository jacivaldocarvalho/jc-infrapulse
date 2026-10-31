"""Exercise image-file gates without Docker or extracting archive paths."""

import io
import json
import runpy
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

MODULE = runpy.run_path(str(Path(__file__).with_name("validate-images.py")))
SCAN = MODULE["scan_archive"]


class ImageArchiveTests(unittest.TestCase):
    def archive(
        self, name: str, content: bytes = b"safe", mode: int = 0o644
    ) -> dict[str, int]:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = mode
            archive.addfile(member, io.BytesIO(content))
        data.seek(0)
        with tarfile.open(fileobj=data) as archive:
            return SCAN(archive)

    def test_regular_runtime_file(self) -> None:
        self.assertEqual(self.archive("app/app/main.py"), {"files": 1})

    def test_credentials_and_local_dependencies_rejected(self) -> None:
        for name in (
            "app/.env",
            "app/.env.local",
            "root/.aws/credentials",
            "app/.git/config",
            "app/.venv/a",
            "web/node_modules/a",
            "app/tests/test_api.py",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.archive(name)

    def test_secret_in_deleted_layer_still_rejected(self) -> None:
        # The same scanner is applied separately to every original image layer.
        with self.assertRaises(ValueError):
            self.archive(
                "tmp/old-key", b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 64 + b"\n"
            )

    def test_secret_crossing_stream_chunk_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.archive(
                "tmp/credential",
                b"x" * (1024 * 1024 - 10) + b"\nAKIAABCDEFGHIJKLMNOP\n",
            )

    def test_world_writable_code_or_configuration_rejected(self) -> None:
        for name in ("app/app/main.py", "etc/nginx/nginx.conf"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.archive(name, mode=0o666)

    def test_symlink_mode_is_not_an_inode_permission(self) -> None:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            member = tarfile.TarInfo("etc/nginx/modules")
            member.type = tarfile.SYMTYPE
            member.linkname = "/usr/lib/nginx/modules"
            member.mode = 0o777
            archive.addfile(member)
        data.seek(0)
        with tarfile.open(fileobj=data) as archive:
            self.assertEqual(SCAN(archive), {"files": 0})

    def test_build_status_survives_secondary_image_cleanup_failure(self) -> None:
        main = MODULE["main"]
        for build_code, cleanup_code in (
            (17, 23),
            (17, None),
            (None, 23),
            (None, None),
        ):
            with (
                self.subTest(build=build_code, cleanup=cleanup_code),
                tempfile.TemporaryDirectory() as directory,
            ):
                primary = (
                    subprocess.CalledProcessError(build_code, ["build"])
                    if build_code
                    else None
                )
                secondary = (
                    subprocess.CalledProcessError(cleanup_code, ["image-cleanup"])
                    if cleanup_code
                    else None
                )
                project = MagicMock()
                project.name = "infrapulse-images-unit"
                project.env = {}
                project.compose = []
                project.__exit__.return_value = False
                stderr = io.StringIO()
                with (
                    patch.dict(
                        main.__globals__,
                        {
                            "IntegrationProject": Mock(return_value=project),
                            "validate": Mock(side_effect=primary),
                            "cleanup_images": Mock(side_effect=secondary),
                        },
                    ),
                    patch(
                        "sys.argv",
                        [
                            "validate-images.py",
                            "--schema",
                            directory + "/schema.json",
                            "--report",
                            directory + "/report.json",
                        ],
                    ),
                    redirect_stderr(stderr),
                ):
                    if primary or secondary:
                        with self.assertRaises(subprocess.CalledProcessError) as raised:
                            main()
                        self.assertIs(raised.exception, primary or secondary)
                    else:
                        main()
                if secondary:
                    self.assertIn("Image cleanup failed", stderr.getvalue())

    def test_public_key_not_treated_as_private_credential(self) -> None:
        self.assertEqual(
            self.archive(
                "etc/ssl/cert.pem", b"-----BEGIN CERTIFICATE-----\n" + b"A" * 64 + b"\n"
            ),
            {"files": 1},
        )


class TypeContractTests(unittest.TestCase):
    def invoke(
        self,
        built: bytes | None = b"export interface Correct {}\n",
        reference: bytes = b"export interface Correct {}\n",
        *,
        schema_matches: bool = True,
        generation_failure: Exception | None = None,
    ) -> tuple[Exception | None, str, dict[str, object] | None]:
        validate = MODULE["validate"]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            schema = directory / "expected.json"
            schema.write_text(
                json.dumps({"openapi": "3.1.0", "paths": {"/health": {}}})
            )
            report = directory / "report.json"
            project = MagicMock()
            project.name = "infrapulse-images-unit"
            project.compose = ["docker", "compose"]

            def run(command: list[str], **kwargs: object) -> str:
                if command[:2] == ["docker", "cp"]:
                    output = Path(command[-1])
                    if command[-2].endswith("/tmp/openapi.json"):
                        output.write_bytes(
                            schema.read_bytes() if schema_matches else b"{}"
                        )
                    elif built is not None:
                        output.write_bytes(built)
                return "controlled-container"

            project.run.side_effect = run
            generator = Mock(
                return_value=(reference, {"generator_version": "7.13.0"}),
                side_effect=generation_failure,
            )
            diagnostics = io.StringIO()
            failure = None
            with (
                patch.dict(
                    validate.__globals__,
                    {
                        "reserve_images": Mock(),
                        "build": Mock(),
                        "inspect_image": Mock(return_value={}),
                        "smoke": Mock(),
                        "generate_reference": generator,
                    },
                ),
                redirect_stdout(diagnostics),
                redirect_stderr(diagnostics),
            ):
                try:
                    validate(project, schema, report)
                except (
                    ValueError,
                    AssertionError,
                    OSError,
                    subprocess.CalledProcessError,
                ) as error:
                    failure = error
                    diagnostics.write(str(error))
            return (
                failure,
                diagnostics.getvalue(),
                json.loads(report.read_text()) if report.exists() else None,
            )

    def test_matching_types(self) -> None:
        failure, _, report = self.invoke()
        self.assertIsNone(failure)
        self.assertEqual(
            report["types"]["built_sha256"], report["types"]["reference_sha256"]
        )

    def test_divergent_types(self) -> None:
        failure, _, report = self.invoke(built=b"export interface Unrelated {}\n")
        self.assertIsInstance(failure, ValueError)
        self.assertIsNone(report)

    def test_empty_types(self) -> None:
        for content in (b"", b"\n\t "):
            with self.subTest(content=content):
                self.assertIsInstance(self.invoke(built=content)[0], ValueError)

    def test_missing_types(self) -> None:
        self.assertIsInstance(self.invoke(built=None)[0], ValueError)

    def test_failed_reference_generation(self) -> None:
        primary = subprocess.CalledProcessError(17, ["reference-generator"])
        failure, _, report = self.invoke(generation_failure=primary)
        self.assertIs(failure, primary)
        self.assertIsNone(report)

    def test_empty_reference(self) -> None:
        for content in (b"", b"\n\t "):
            with self.subTest(content=content):
                self.assertIsInstance(self.invoke(reference=content)[0], ValueError)

    def test_divergent_schema(self) -> None:
        self.assertIsInstance(self.invoke(schema_matches=False)[0], ValueError)

    def test_same_size_different_content(self) -> None:
        self.assertIsInstance(
            self.invoke(built=b"type X = 'a';", reference=b"type X = 'b';")[0],
            ValueError,
        )

    def test_diagnostics_do_not_disclose_type_contents(self) -> None:
        secret = b"synthetic-credential-do-not-log"
        failure, diagnostics, _ = self.invoke(built=secret)
        self.assertIsInstance(failure, ValueError)
        self.assertNotIn(secret.decode(), diagnostics)
        self.assertIn("built_sha256", diagnostics)
        self.assertIn("reference_sha256", diagnostics)


class ReferenceProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.schema = Path(self.temporary.name) / "contract.json"
        self.schema.write_text('{"openapi":"3.1.0","paths":{}}')
        self.project = Mock()
        self.project.name = "infrapulse-images-unit"
        self.project.env = {"SYNTHETIC_HOST_CREDENTIAL": "must-not-forward"}
        self.generate = MODULE["generate_reference"]

    def test_isolated_generation_uses_stdin_and_locked_inputs(self) -> None:
        completed = subprocess.CompletedProcess(
            [], 0, stdout=b"generated reference", stderr=b""
        )
        with patch("subprocess.run", return_value=completed) as run:
            reference, metadata = self.generate(self.project, self.schema)
        self.assertEqual(reference, b"generated reference")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertIn("--rm", command)
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--user") + 1], "node")
        self.assertIn("--tmpfs", command)
        self.assertNotIn("--env", command)
        self.assertNotIn("--volume", command)
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["schema"], self.schema.read_text())
        self.assertEqual(payload["version"], metadata["generator_version"])
        self.assertEqual(
            payload["expected"]["scripts/generate-api.mjs"],
            metadata["generator_source_sha256"],
        )
        self.assertEqual(
            payload["expected"]["package-lock.json"], metadata["lockfile_sha256"]
        )
        self.assertNotIn("must-not-forward", json.dumps(payload))
        self.assertNotIn("must-not-forward", repr(command))

    def test_failed_generation_keeps_code_and_redacts_diagnostics(self) -> None:
        secret = b"synthetic-secret-do-not-print"
        primary = subprocess.CalledProcessError(
            17, ["docker", secret.decode()], output=secret, stderr=secret
        )
        stderr = io.StringIO()
        with (
            patch("subprocess.run", side_effect=primary),
            redirect_stderr(stderr),
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            self.generate(self.project, self.schema)
        self.assertIs(raised.exception, primary)
        self.assertEqual(raised.exception.returncode, 17)
        self.assertNotIn(secret.decode(), str(raised.exception) + stderr.getvalue())
        self.assertIn("exit_code=17", stderr.getvalue())
        self.assertIn("stderr_sha256=", stderr.getvalue())
        self.assertLess(len(stderr.getvalue()), 512)

    def test_timeout_is_not_success_and_does_not_expose_output(self) -> None:
        failure = subprocess.TimeoutExpired(
            ["docker"],
            120,
            output=b"synthetic-timeout-secret",
            stderr=b"synthetic-timeout-secret",
        )
        stderr = io.StringIO()
        with (
            patch("subprocess.run", side_effect=failure),
            redirect_stderr(stderr),
            self.assertRaises(subprocess.TimeoutExpired) as raised,
        ):
            self.generate(self.project, self.schema)
        self.assertIs(raised.exception, failure)
        self.assertNotIn("synthetic-timeout-secret", stderr.getvalue())

    def test_floating_or_inconsistent_generator_version_rejected(self) -> None:
        frontend = Path(self.temporary.name) / "frontend"
        (frontend / "scripts").mkdir(parents=True)
        (frontend / "scripts/generate-api.mjs").write_text("synthetic generator")
        (frontend / "package.json").write_text(
            json.dumps({"devDependencies": {"openapi-typescript": "^7.13.0"}})
        )
        (frontend / "package-lock.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "node_modules/openapi-typescript": {
                            "version": "7.13.0",
                            "integrity": "synthetic-integrity",
                        }
                    }
                }
            )
        )
        with (
            patch.dict(self.generate.__globals__, {"ROOT": Path(self.temporary.name)}),
            patch("subprocess.run") as run,
        ):
            with self.assertRaises(ValueError):
                self.generate(self.project, self.schema)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
