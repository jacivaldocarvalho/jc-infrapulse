"""Deterministic supply-chain checks; F1B debt is reported, never called PASS."""

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = frozenset({"npm-integrity", "actions-policy"})
PENDING = frozenset({"python-hashes", "build-tools", "base-images"})
SHA = r"[0-9a-f]{40}"
DIGEST = r"[0-9a-f]{64}"


def yaml_document(root: Path, path: Path) -> dict:
    """Use the existing, lockfile-pinned js-yaml, without adding a dependency."""
    parser = root / "frontend/node_modules/js-yaml"
    code = """
const fs = require('node:fs');
const yaml = require(process.argv[1]);
if (require(process.argv[1] + '/package.json').version !== process.argv[2]) {
  throw new Error('YAML parser version differs from the lockfile');
}
const doc = yaml.load(fs.readFileSync(0, 'utf8'), {schema: yaml.JSON_SCHEMA});
process.stdout.write(JSON.stringify(doc));
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            code,
            str(parser),
            json.loads((root / "frontend/package-lock.json").read_text())["packages"][
                "node_modules/js-yaml"
            ]["version"],
        ],
        input=path.read_text(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        # Parser errors can quote sensitive input. Never forward stderr.
        raise ValueError("YAML parser failed (install locked npm dependencies)")
    document = json.loads(result.stdout)
    if not isinstance(document, dict):
        raise TypeError("Expected YAML mapping")
    return document


def npm_violations(lock: dict, package: dict) -> list[str]:
    errors: list[str] = []
    packages = lock.get("packages", {})
    if lock.get("lockfileVersion") != 3 or not packages or "" not in packages:
        return ["Expected nonempty npm lockfile v3"]
    for section in ("dependencies", "devDependencies"):
        if packages[""].get(section, {}) != package.get(section, {}):
            errors.append(f"Root {section} differ from package.json")
        for version in package.get(section, {}).values():
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
                errors.append("Direct npm dependency is not exact")
    for name, record in packages.items():
        if not name:
            continue
        resolved = record.get("resolved", "")
        integrity = record.get("integrity", "")
        valid_hash = False
        if isinstance(integrity, str) and integrity.startswith("sha512-"):
            try:
                valid_hash = len(base64.b64decode(integrity[7:], validate=True)) == 64
            except (ValueError, binascii.Error):
                valid_hash = False
        if (
            not valid_hash
            or not re.fullmatch(
                r"https://registry\.npmjs\.org/[A-Za-z0-9@%_./~-]+\.tgz", resolved
            )
            or not re.fullmatch(
                r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", record.get("version", "")
            )
            or record.get("link")
        ):
            # Do not echo resolved URLs, which may contain credentials.
            errors.append("Package record lacks approved origin/version/SHA-512")
    return errors


def python_violations(text: str) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    hashed: set[str] = set()
    seen: set[str] = set()
    entries = text.replace("\\\n", " ").splitlines()
    for line in entries:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)"
            r"((?:\s+--hash=sha256:[0-9a-f]{64})*)\s*",
            line,
        )
        if not match:
            errors.append("Requirement must be exact with SHA-256 hashes")
            continue
        name, version, hashes = match.groups()
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in seen:
            errors.append("Duplicate Python requirement")
        seen.add(name)
        if not hashes:
            errors.append("Exact Python requirement lacks SHA-256 hashes")
        else:
            hashed.add(f"{name}=={version}")
    if not seen:
        errors.append("Python lock contains no exact requirements")
    return errors, hashed


def build_violations(project: dict, hashed: set[str]) -> list[str]:
    requirements = project.get("build-system", {}).get("requires", [])
    errors: list[str] = []
    if not requirements:
        errors.append("Build-system requirements are absent")
    for requirement in requirements:
        normalized = requirement.lower().replace("_", "-")
        if normalized not in hashed:
            errors.append("Build requirement must be exact and hashed in Python lock")
    # pip drives every backend install; its bootstrap version is currently implicit.
    if not any(item.startswith("pip==") for item in hashed):
        errors.append("pip bootstrap must have an exact version and artifact hashes")
    return errors


def image_violations(text: str) -> list[str]:
    stages: set[str] = set()
    errors: list[str] = []
    count = 0
    for line in text.splitlines():
        if not re.match(r"\s*FROM\s", line, re.IGNORECASE):
            continue
        count += 1
        match = re.fullmatch(
            r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)"
            r"(?:\s+AS\s+([A-Za-z0-9_-]+))?\s*",
            line,
            re.IGNORECASE,
        )
        if not match:
            errors.append("Unsupported FROM syntax")
            continue
        image, alias = match.groups()
        if (
            image.lower() not in stages
            and image != "scratch"
            and not re.fullmatch(r"[^\s@$]+@sha256:" + DIGEST, image)
        ):
            errors.append("External FROM must pin a SHA-256 digest")
        if alias:
            stages.add(alias.lower())
    if not count:
        errors.append("Dockerfile has no FROM")
    return errors


def workflow_violations(doc: dict, workflow_path: str | None = None) -> list[str]:
    errors: list[str] = []
    events = doc.get("on", {})
    jobs = doc.get("jobs", {})
    # This fixed workflow reads artifacts only in its main-only manual job.
    artifact_reader = (
        workflow_path == ".github/workflows/f3c3-real-validation.yml"
        and isinstance(events, dict)
        and set(events) == {"workflow_dispatch"}
        and isinstance(jobs, dict)
        and set(jobs) == {"integrated-validation"}
        and isinstance(jobs["integrated-validation"], dict)
        and jobs["integrated-validation"].get("if") == "github.ref == 'refs/heads/main'"
    )
    allowed_workflow_permissions = [{"contents": "read"}]
    if artifact_reader:
        allowed_workflow_permissions.append({"contents": "read", "actions": "read"})
    if doc.get("permissions") not in allowed_workflow_permissions:
        errors.append("Workflow permissions must be contents: read only")
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, (dict, list)) or "pull_request_target" in events:
        errors.append("Unsafe workflow trigger")
    if not isinstance(jobs, dict) or not jobs:
        return errors + ["Workflow has no jobs"]
    for job in jobs.values():
        permissions = job.get("permissions", {})
        if not isinstance(permissions, dict) or any(
            name not in ({"contents", "actions"} if artifact_reader else {"contents"})
            or value != "read"
            for name, value in permissions.items()
        ):
            errors.append("Job grants permissions beyond contents: read")
        if type(job.get("timeout-minutes")) is not int or job["timeout-minutes"] <= 0:
            errors.append("Job timeout must be explicit")
        steps = job.get("steps", [])
        uses = [step for step in steps if "uses" in step]
        if "uses" in job:
            uses.append(job)
        for step in uses:
            action = step["uses"]
            if not isinstance(action, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@" + SHA, action
            ):
                errors.append("Action/reusable workflow must use a full commit SHA")
            if (
                isinstance(action, str)
                and action.startswith("actions/checkout@")
                and step.get("with", {}).get("persist-credentials") is not False
            ):
                errors.append("Checkout must disable persisted credentials")
    return errors


def audit(root: Path, npm_only: bool = False) -> dict[str, list[str]]:
    results = {
        "npm-integrity": npm_violations(
            json.loads((root / "frontend/package-lock.json").read_text()),
            json.loads((root / "frontend/package.json").read_text()),
        )
    }
    if npm_only:
        return results
    errors, hashed = python_violations((root / "backend/requirements.lock").read_text())
    runtime_errors, runtime_hashed = python_violations(
        (root / "backend/requirements-runtime.lock").read_text()
    )
    errors.extend(runtime_errors)
    if not runtime_hashed <= hashed:
        errors.append("Runtime requirements must match exact full-lock versions/hashes")

    # Exact versions alone cannot detect differing hashes between the lock subsets.
    def artifact_lines(name: str) -> dict[str, str]:
        text = (root / "backend" / name).read_text().replace("\\\n", " ")
        return {
            line.split("==", 1)[0].lower().replace("_", "-"): " ".join(line.split())
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        }

    full_artifacts = artifact_lines("requirements.lock")
    runtime_artifacts = artifact_lines("requirements-runtime.lock")
    if any(
        full_artifacts.get(name) != line for name, line in runtime_artifacts.items()
    ):
        errors.append("Runtime artifact hashes differ from full lock")
    results["python-hashes"] = errors
    build_errors, build_hashed = python_violations(
        (root / "backend/requirements-build.lock").read_text()
    )
    build_errors.extend(
        build_violations(
            tomllib.loads((root / "backend/pyproject.toml").read_text()), build_hashed
        )
    )
    for name, line in artifact_lines("requirements-build.lock").items():
        if name in full_artifacts and full_artifacts[name] != line:
            build_errors.append("Build artifact versions/hashes differ from full lock")
    results["build-tools"] = build_errors
    dockerfiles = []
    for directory, children, files in os.walk(root):
        # Do not traverse installed dependencies, Git internals or local artifacts.
        children[:] = sorted(set(children) - {"node_modules", ".venv", ".git", "dist"})
        if "Dockerfile" in files:
            dockerfiles.append(Path(directory) / "Dockerfile")
    if not dockerfiles:
        raise ValueError("Dockerfile inventory is empty")
    results["base-images"] = [
        error for path in dockerfiles for error in image_violations(path.read_text())
    ]
    workflows = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not workflows:
        raise ValueError("Workflow inventory is empty")
    results["actions-policy"] = [
        error
        for path in workflows
        for error in workflow_violations(
            yaml_document(root, path), path.relative_to(root).as_posix()
        )
    ]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enforce", choices=("active", "all"), default="active")
    parser.add_argument("--npm-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.npm_only and args.enforce == "all":
        parser.error("--npm-only cannot enforce the complete policy")
    started = time.perf_counter()
    try:
        findings = audit(ROOT, args.npm_only)
        expected = {"npm-integrity"} if args.npm_only else ACTIVE | PENDING
        if findings.keys() != expected:
            raise ValueError("Rule inventory mismatch")
        enforced = ACTIVE | PENDING if args.enforce == "all" else ACTIVE
        code = int(any(errors for name, errors in findings.items() if name in enforced))
        report = {
            "mode": args.enforce,
            "policy_status": "FAIL" if any(findings.values()) else "PASS",
            "gate_status": "FAIL" if code else "PASS",
            "rules": {
                name: {
                    "status": "FAIL" if errors else "PASS",
                    "enforcement": "BLOCKING" if name in enforced else "F1B_PENDING",
                    "violations": errors,
                }
                for name, errors in findings.items()
            },
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
    ):
        report = {"status": "ERROR", "diagnostic": "Policy evaluation failed closed"}
        code = 2
    print(json.dumps(report, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
