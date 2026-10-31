"""Verify installer modes and failure propagation without installing packages."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_supply_chain import ROOT, audit, yaml_document


class LockedInstallTests(unittest.TestCase):
    def execute(self, mode: list[str], failure: int = 0) -> tuple[int, list[list[str]]]:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fake = directory / "python"
            fake.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys\n"
                "with open(os.environ['CALLS'], 'a') as f: "
                "f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "sys.exit(int(os.environ['FAILURE']))\n"
            )
            fake.chmod(0o700)
            calls = directory / "calls.jsonl"
            result = subprocess.run(
                ["bash", str(ROOT / "backend/install-locked.sh"), *mode],
                env={
                    "PATH": str(directory) + ":" + os.defpath,
                    "CALLS": str(calls),
                    "FAILURE": str(failure),
                },
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode, [
                json.loads(line) for line in calls.read_text().splitlines()
            ] if calls.exists() else []

    def test_runtime_and_dev_use_hashes_and_offline_local_install(self) -> None:
        for mode, lock in (
            ("runtime", "requirements-runtime.lock"),
            ("dev", "requirements.lock"),
        ):
            with self.subTest(mode=mode):
                code, calls = self.execute([mode, "--editable"])
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 4)
                self.assertIn("--force-reinstall", calls[0])
                self.assertEqual(calls[0][-1], "requirements-build.lock")
                for call in calls[:2]:
                    self.assertIn("--require-hashes", call)
                    self.assertIn("--only-binary=:all:", call)
                self.assertEqual(calls[1][-1], lock)
                for option in (
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    "--editable",
                ):
                    self.assertIn(option, calls[2])
                self.assertEqual(calls[3], ["-m", "pip", "check"])

    def test_failure_code_is_preserved_and_subsequent_steps_do_not_run(self) -> None:
        for code in (1, 17, 127):
            with self.subTest(code=code):
                status, calls = self.execute(["runtime"], code)
                self.assertEqual(status, code)
                self.assertEqual(len(calls), 1)

    def test_invalid_options_do_not_invoke_pip(self) -> None:
        for arguments in (
            [],
            ["invalid"],
            ["dev", "unexpected"],
            ["dev", "--editable", "extra"],
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.execute(arguments), (2, []))

    def test_complete_repository_policy_has_no_violations(self) -> None:
        self.assertEqual(
            {name: len(errors) for name, errors in audit(ROOT).items()},
            {
                "npm-integrity": 0,
                "python-hashes": 0,
                "build-tools": 0,
                "base-images": 0,
                "actions-policy": 0,
            },
        )

    def test_lock_subset_drift_is_rejected(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for relative in ("backend", ".github"):
                shutil.copytree(
                    ROOT / relative,
                    root / relative,
                    ignore=shutil.ignore_patterns(
                        ".venv",
                        ".env*",
                        "__pycache__",
                        ".pytest_cache",
                        ".mypy_cache",
                        ".ruff_cache",
                    ),
                )
            (root / "frontend").mkdir()
            for filename in ("package.json", "package-lock.json", "Dockerfile"):
                shutil.copyfile(
                    ROOT / "frontend" / filename, root / "frontend" / filename
                )
            (root / "frontend/node_modules").symlink_to(
                ROOT / "frontend/node_modules", target_is_directory=True
            )
            path = root / "backend/requirements-runtime.lock"
            path.write_text(
                re.sub(
                    r"(?<=--hash=sha256:)[0-9a-f]{64}",
                    lambda match: ("0" if match[0][0] != "0" else "1") + match[0][1:],
                    path.read_text(),
                    count=1,
                )
            )
            self.assertTrue(audit(root)["python-hashes"])

    def test_dependabot_targets_reviewable_updates_without_merge_automation(
        self,
    ) -> None:
        document = yaml_document(ROOT, ROOT / ".github/dependabot.yml")
        self.assertEqual(document["version"], 2)
        self.assertEqual(
            {update["package-ecosystem"] for update in document["updates"]},
            {
                "pip",
                "npm",
                "docker",
                "github-actions",
            },
        )
        for update in document["updates"]:
            self.assertEqual(update["target-branch"], "develop")
            self.assertEqual(update["schedule"]["interval"], "weekly")
            self.assertEqual(update["open-pull-requests-limit"], 3)
            self.assertNotIn("auto-merge", update)


if __name__ == "__main__":
    unittest.main(verbosity=2)
