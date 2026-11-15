"""Focused tests for the sanitized CI diagnostic artifact selection step."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
IMAGE_NAMES = ("backend-image", "nginx-image")


def selection_script() -> str:
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index("      - name: Check for sanitized image root diagnostics")
    run = lines.index("        run: |", step)
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.startswith("      - name: "):
            break
        body.append(line[10:] if line.startswith("          ") else "")
    return "\n".join(body)


class DiagnosticUploadSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "ci-evidence"
        self.output.mkdir()
        self.github_output = self.root / "github-output"

    def run_selection(self) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "EVIDENCE_OUTPUT": str(self.output),
            "GITHUB_OUTPUT": str(self.github_output),
        }
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-e",
                "-o",
                "pipefail",
                "-c",
                selection_script(),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_regular_json_is_the_only_validated_upload_path(self) -> None:
        diagnostic = self.output / f"{IMAGE_NAMES[0]}.root-diagnostic.json"
        diagnostic.write_text('{"status":"ERROR"}\n')
        result = self.run_selection()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.github_output.read_text()
        self.assertIn("exists=true", output)
        self.assertIn(str(diagnostic), output)
        self.assertIn(
            "path: ${{ steps.root_diagnostic.outputs.paths }}", WORKFLOW.read_text()
        )

    def test_absent_report_skips_upload(self) -> None:
        result = self.run_selection()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.github_output.read_text(), "exists=false\n")

    def test_directory_with_json_name_is_rejected(self) -> None:
        (self.output / f"{IMAGE_NAMES[0]}.root-diagnostic.json").mkdir()
        result = self.run_selection()
        self.assertNotEqual(result.returncode, 0)

    def test_unexpected_diagnostic_path_is_rejected(self) -> None:
        (self.output / "unexpected-root-diagnostic.json").write_text("{}\n")
        result = self.run_selection()
        self.assertNotEqual(result.returncode, 0)

    def test_symlink_to_file_is_rejected(self) -> None:
        target = self.root / "target.json"
        target.write_text("{}\n")
        (self.output / f"{IMAGE_NAMES[0]}.root-diagnostic.json").symlink_to(target)
        result = self.run_selection()
        self.assertNotEqual(result.returncode, 0)

    def test_symlink_to_directory_is_rejected(self) -> None:
        target = self.root / "target-directory"
        target.mkdir()
        (self.output / f"{IMAGE_NAMES[0]}.root-diagnostic.json").symlink_to(target)
        result = self.run_selection()
        self.assertNotEqual(result.returncode, 0)

    def test_all_matching_allowlisted_reports_are_validated_and_selected(self) -> None:
        paths = []
        for name in IMAGE_NAMES:
            path = self.output / f"{name}.root-diagnostic.json"
            path.write_text('{"status":"ERROR"}\n')
            paths.append(path)
        result = self.run_selection()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.github_output.read_text()
        self.assertIn("exists=true", output)
        for path in paths:
            self.assertIn(str(path), output)


if __name__ == "__main__":
    unittest.main()
