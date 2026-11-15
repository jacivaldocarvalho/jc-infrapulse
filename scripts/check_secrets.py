"""Pinned Gitleaks, executable fixtures and sanitized fail-closed reporting."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "8.30.1"
ARCHIVE_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/"
    f"v{VERSION}/gitleaks_{VERSION}_linux_x64.tar.gz"
)


def controlled_env(home: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def install(data: bytes, directory: Path) -> Path:
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Gitleaks archive digest mismatch")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        member = archive.getmember("gitleaks")
        if not member.isfile() or member.size > 100_000_000:
            raise ValueError("Invalid Gitleaks executable")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("Missing executable")
        executable = directory / "gitleaks"
        executable.write_bytes(stream.read())
        executable.chmod(0o700)
    result = subprocess.run(
        [str(executable), "version"],
        capture_output=True,
        text=True,
        timeout=15,
        env=controlled_env(directory),
        check=True,
    )
    if result.stdout.strip() != VERSION:
        raise ValueError("Unexpected Gitleaks version")
    return executable


def scan(executable: Path, mode: str, target: Path, temporary: Path) -> dict:
    report = temporary / "raw.json"
    report.unlink(missing_ok=True)
    ignore = temporary / "empty.ignore"
    ignore.write_text("")
    command = [
        str(executable),
        mode,
        str(target),
        "--config",
        str(ROOT / "security/gitleaks.toml"),
        "--gitleaks-ignore-path",
        str(ignore),
        "--ignore-gitleaks-allow",
        "--redact=100",
        "--no-banner",
        "--no-color",
        "--report-format=json",
        "--report-path",
        str(report),
        "--timeout=120",
    ]
    if mode == "git":
        command.append("--log-opts=HEAD")
    result = subprocess.run(
        command,
        capture_output=True,
        timeout=150,
        check=False,
        env=controlled_env(temporary),
    )
    # Never forward engine stdout/stderr or raw report fields containing matches.
    if result.returncode not in (0, 1) or not report.is_file():
        raise ValueError("Scanner failed or did not produce a report")
    findings = json.loads(report.read_text())
    if not isinstance(findings, list) or bool(findings) != (result.returncode == 1):
        raise ValueError("Scanner exit/report mismatch")
    sanitized = []
    for finding in findings:
        rule = finding["RuleID"]
        line = finding["StartLine"]
        path = finding["File"]
        if (
            not isinstance(rule, str)
            or not re.fullmatch(r"[a-z0-9_-]+", rule)
            or not isinstance(line, int)
            or line < 1
            or not isinstance(path, str)
        ):
            raise ValueError("Invalid scanner finding")
        sanitized.append(
            {
                "rule": rule,
                "path_sha256": hashlib.sha256(path.encode()).hexdigest(),
                "line": line,
            }
        )
    return {"status": "FAIL" if findings else "PASS", "findings": sanitized}


def fixtures(executable: Path, temporary: Path) -> None:
    positive = temporary / "positive"
    negative = temporary / "negative"
    positive.mkdir()
    negative.mkdir()
    # Synthetic, nonfunctional token assembled only in a disposable directory.
    token = (
        "gh" + "p_" + hashlib.sha256(b"infrapulse-positive-fixture").hexdigest()[:36]
    )
    (positive / "fixture.txt").write_text(f"credential = '{token}'\n")
    (negative / "fixture.txt").write_text("credential = os.environ['TOKEN']\n")
    detected = scan(executable, "dir", positive, temporary)
    clean = scan(executable, "dir", negative, temporary)
    if (
        detected["status"] != "FAIL"
        or clean["status"] != "PASS"
        or not any(finding["rule"] == "github-pat" for finding in detected["findings"])
    ):
        raise ValueError("Gitleaks positive/negative fixtures failed")
    if token in json.dumps(detected):
        raise ValueError("Sanitization failed")


def snapshot(root: Path, destination: Path) -> None:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=True,
    )
    names = result.stdout.decode().split("\0")
    count = 0
    for name in filter(None, names):
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Scan inventory contains symlink or unavailable file")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        count += 1
    if not count:
        raise ValueError("Secret scan inventory is empty")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Optional verified release cache")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    report: dict = {"version": VERSION, "archive_sha256": ARCHIVE_SHA256}
    code = 2
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        if shallow.stdout.strip() != "false":
            raise ValueError("Complete HEAD ancestry is required")
        with tempfile.TemporaryDirectory(prefix="infrapulse-secrets-") as name:
            temporary = Path(name)
            if args.archive:
                data = args.archive.read_bytes()
            else:
                with urllib.request.urlopen(URL, timeout=60) as response:
                    data = response.read(25_000_001)
                if len(data) > 25_000_000:
                    raise ValueError("Gitleaks download exceeds limit")
            executable = install(data, temporary)
            fixtures(executable, temporary)
            report["fixtures"] = "PASS"
            report["history"] = scan(executable, "git", ROOT, temporary)
            tree = temporary / "checkout"
            tree.mkdir()
            snapshot(ROOT, tree)
            report["checkout"] = scan(executable, "dir", tree, temporary)
            code = int(
                any(report[key]["status"] != "PASS" for key in ("history", "checkout"))
            )
            report["status"] = "FAIL" if code else "PASS"
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ):
        # Includes download, engine, malformed/missing reports and cleanup errors.
        # No exception text: tool diagnostics and URLs can contain credentials.
        report["status"] = "ERROR"
        report["diagnostic"] = "Secret scan failed closed; no raw output was published"
        code = 2
    report["duration_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(report, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
