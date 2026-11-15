"""Offline F2B classification and proposed policy; never changes the CI gates."""

import argparse
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from audit_vulnerabilities import ID, ROOT, VERSION, finding, safe

TARGETS = {
    "python": "pip-audit",
    "node": "npm audit",
    "backend-image": "Trivy",
    "nginx-image": "Trivy",
}
LEVEL = {
    "UNKNOWN": 0,
    "INFO": 1,
    "LOW": 2,
    "MODERATE": 3,
    "MEDIUM": 3,
    "HIGH": 4,
    "CRITICAL": 5,
}
STATES = {
    "CLEAN": "PASS",
    "FINDINGS": "FINDINGS",
    "ERROR": "SCANNER_ERROR",
    "NOT_RUN": "NOT_RUN",
}
MATCH_FIELDS = ("scanner", "package", "version", "advisory", "target")
BOUND_INPUTS = {
    "backend/requirements.lock",
    "backend/requirements-build.lock",
    "frontend/package-lock.json",
    "backend/Dockerfile",
    "frontend/Dockerfile",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alias_index(reports: list[dict], evidence: dict) -> dict[str, str]:
    parents: dict[str, str] = {}

    def root(identifier: str) -> str:
        parents.setdefault(identifier, identifier)
        while parents[identifier] != identifier:
            identifier = parents[identifier]
        return identifier

    groups = list(evidence.get("aliases", []))
    for report in reports:
        for result in report.get("targets", {}).values():
            groups.extend(row["ids"] for row in result.get("findings", []))
    for group in groups:
        if not group or not isinstance(group, list):
            raise ValueError("Invalid advisory aliases")
        for identifier in group:
            safe(identifier, ID)
            parents[root(identifier)] = root(group[0])
    components: dict[str, list[str]] = {}
    for identifier in parents:
        components.setdefault(root(identifier), []).append(identifier)
    canonical = {}
    for identifiers in components.values():
        preferred = min(
            identifiers,
            key=lambda item: (
                not item.startswith("CVE-"),
                not item.startswith("GHSA-"),
                item,
            ),
        )
        canonical.update(dict.fromkeys(identifiers, preferred))
    return canonical


def fingerprint(match: dict) -> str:
    if set(match) != set(MATCH_FIELDS):
        raise ValueError("Finding identity must have five exact fields")
    return hashlib.sha256(
        json.dumps(match, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize(
    report: dict, aliases: dict[str, str]
) -> tuple[dict[str, dict], dict[str, str]]:
    if set(report["targets"]) != set(TARGETS):
        raise ValueError("Mandatory target inventory differs")
    records: dict[str, dict] = {}
    states = {}
    for target, result in report["targets"].items():
        states[target] = STATES[result["status"]]
        rows = result.get("findings", [])
        if not isinstance(rows, list):
            raise TypeError("Invalid findings list")
        if result["status"] in {"ERROR", "NOT_RUN"}:
            continue
        if bool(rows) != (result["status"] == "FINDINGS"):
            raise ValueError("Scanner status/findings mismatch")
        scanner = result["scanner"]["name"]
        if scanner != TARGETS[target]:
            raise ValueError("Unexpected scanner for target")
        safe(result["scanner"]["version"], VERSION)
        coverage = result["coverage"]
        count = (
            coverage.get("dependencies")
            if target in {"python", "node"}
            else coverage.get("os_packages")
        )
        if type(count) is not int or count < 1:
            raise ValueError("Scanner coverage is empty")
        if result.get("exit_code") != (10 if scanner == "Trivy" else 1) * bool(rows):
            raise ValueError("Scanner exit code differs from status")
        for row in rows:
            clean = finding(
                row["package"],
                row["installed_version"],
                row["ids"],
                row["severity"],
                row["fix_versions"],
                row["origin"],
                row["fix_available"],
            )
            package = clean["package"]
            if target == "python" or clean["origin"] == "lang-pkgs":
                package = re.sub(r"[-_.]+", "-", package).lower()
            match = {
                "scanner": scanner,
                "target": target,
                "package": package,
                "version": clean["installed_version"],
                "advisory": aliases[clean["ids"][0]],
            }
            key = fingerprint(match)
            current = records.setdefault(
                key,
                {
                    **match,
                    "fingerprint": key,
                    "ids": [],
                    "severity": "UNKNOWN",
                    "scanner_fix_versions": [],
                    "scanner_fix_available": False,
                    "origin": clean["origin"],
                    "occurrences": 0,
                },
            )
            if current["origin"] != clean["origin"]:
                raise ValueError("Different package ecosystems share an identity")
            current["ids"] = sorted(set(current["ids"]) | set(clean["ids"]))
            current["scanner_fix_versions"] = sorted(
                set(current["scanner_fix_versions"]) | set(clean["fix_versions"])
            )
            current["scanner_fix_available"] |= clean["fix_available"]
            if LEVEL[clean["severity"]] > LEVEL[current["severity"]]:
                current["severity"] = clean["severity"]
            current["occurrences"] += 1
    if report.get("status") == "ERROR":
        states["report"] = "SCANNER_ERROR"
    elif report.get("status") == "INCOMPLETE":
        states["report"] = "NOT_RUN"
    elif report.get("status") not in {"CLEAN", "FINDINGS"}:
        raise ValueError("Unsupported consolidated report state")
    elif bool(records) != (report["status"] == "FINDINGS"):
        raise ValueError("Consolidated status differs from target findings")
    return records, states


def manifests(evidence: dict) -> dict:
    project = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())

    def python_names(values: list[str]) -> set[str]:
        return {
            re.sub(r"[-_.]+", "-", re.match(r"[A-Za-z0-9_.-]+", value)[0]).lower()
            for value in values
        }

    def locked_names(name: str) -> set[str]:
        return python_names(
            [
                line
                for line in (ROOT / "backend" / name).read_text().splitlines()
                if re.match(r"^[A-Za-z0-9_.-]+==", line)
            ]
        )

    package = json.loads((ROOT / "frontend/package.json").read_text())
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    npm = {}
    for path, record in lock["packages"].items():
        if "node_modules/" in path:
            npm.setdefault(
                (path.rsplit("node_modules/", 1)[-1], record["version"]), []
            ).append(record.get("dev") is True)
    return {
        "runtime": locked_names("requirements-runtime.lock"),
        "build": locked_names("requirements-build.lock"),
        "direct": python_names(project["project"]["dependencies"]),
        "dev": python_names(project["project"]["optional-dependencies"]["dev"]),
        "npm": npm,
        "npm_direct": set(package["dependencies"]) | set(package["devDependencies"]),
        "embedded": {
            (row["package"], row["version"]): row
            for row in evidence.get("embedded_packages", [])
        },
    }


def context(row: dict, manifest: dict) -> dict:
    package = row["package"]
    if row["origin"] == "os-pkgs":
        return {
            "deployment": "runtime",
            "dependency_kind": "system_package",
            "usage": "system_library",
        }
    if row["target"] == "node":
        entries = manifest["npm"].get((package, row["version"]))
        if not entries:
            raise ValueError("npm finding does not match the current lock")
        return {
            "deployment": "build_only" if all(entries) else "runtime_bundle_candidate",
            "dependency_kind": "direct"
            if package in manifest["npm_direct"]
            else "transitive",
            "usage": "frontend_tooling" if all(entries) else "application_dependency",
        }
    embedded = manifest["embedded"].get((package, row["version"]))
    if row["target"] == "backend-image" and embedded:
        return {
            "deployment": "runtime_and_build",
            "dependency_kind": "vendored_transitive",
            "usage": "package_manager_tooling",
            "owner": embedded["owner"],
            "owner_version": embedded["owner_version"],
        }
    return {
        "deployment": "runtime_and_build"
        if package in manifest["build"]
        else "runtime"
        if row["target"] == "backend-image" or package in manifest["runtime"]
        else "build_only",
        "dependency_kind": "direct"
        if package in manifest["direct"] | manifest["dev"] | manifest["build"]
        else "transitive",
        "usage": "build_tooling"
        if package in manifest["build"]
        else "application_or_transitive",
    }


def context_matches(evidence: dict, root: Path = ROOT) -> bool:
    hashes = evidence["context_sha256"]
    if not hashes:
        raise ValueError("Assessment context must be bound to source files")
    for name, expected in hashes.items():
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            raise ValueError("Invalid context binding")
        if not (root / path).is_file() or digest(root / path) != expected:
            return False
    return True


def validate_source_binding(report: dict, root: Path = ROOT) -> None:
    hashes = report.get("input_sha256", {})
    if report.get("status") in {"CLEAN", "FINDINGS"} and set(hashes) != BOUND_INPUTS:
        raise ValueError("Audit source inventory is incomplete")
    for name, expected in hashes.items():
        if (
            name not in BOUND_INPUTS
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or digest(root / name) != expected
        ):
            raise ValueError("Audit inputs differ from current source")


def apply_reviews(
    records: dict[str, dict],
    evidence: dict,
    aliases: dict[str, str],
    today: date,
    matches: bool,
) -> None:
    if evidence["schema_version"] != 1:
        raise ValueError("Unsupported assessment schema")
    reviews = {}
    for review in evidence["reviews"]:
        fields = {
            "match",
            "applicability",
            "fix_verification",
            "verified_fixed_versions",
            "note",
            "sources",
            "reviewed_on",
            "expires_on",
        }
        if not fields <= review.keys() or review.keys() - fields - {
            "verified_severity"
        }:
            raise ValueError("Unexpected assessment fields")
        if not isinstance(review["verified_fixed_versions"], list):
            raise TypeError("Invalid verified fixes")
        for version in review["verified_fixed_versions"]:
            safe(version, VERSION)
        match = dict(review["match"])
        match["advisory"] = aliases.get(match["advisory"], match["advisory"])
        key = fingerprint(match)
        if key in reviews:
            raise ValueError("Duplicate assessment identity")
        if review["applicability"] not in {
            "CONFIRMED_AFFECTED",
            "NOT_APPLICABLE",
            "NEEDS_REVIEW",
        } or review["fix_verification"] not in {
            "CONFIRMED",
            "CONFLICT",
            "UNVERIFIED",
            "NO_VENDOR_FIX",
            "BACKPORT_FIXED",
        }:
            raise ValueError("Invalid assessment classification")
        if not review["sources"] or not review["note"].strip():
            raise ValueError("Assessment needs evidence and rationale")
        for source in review["sources"]:
            safe(source, r"https://[A-Za-z0-9./_?=&%:+@-]+")
            if "@" in source:
                raise ValueError("Credential-bearing evidence URL")
        reviewed = date.fromisoformat(review["reviewed_on"])
        expires = date.fromisoformat(review["expires_on"])
        if expires <= reviewed:
            raise ValueError("Assessment expiry is invalid")
        reviews[key] = review if matches and reviewed <= today < expires else None
    for key, row in records.items():
        review = reviews.get(key)
        row["assessment"] = (
            {
                "applicability": "NEEDS_REVIEW",
                "fix_verification": "UNVERIFIED",
                "verified_fixed_versions": [],
                "note": "No current source-bound assessment; do not assume backports or reachability.",
            }
            if review is None
            else {k: v for k, v in review.items() if k != "match"}
        )
        verified_severity = row["assessment"].get("verified_severity")
        if verified_severity:
            if verified_severity not in LEVEL:
                raise ValueError("Invalid authoritative severity")
            if LEVEL[verified_severity] > LEVEL[row["severity"]]:
                row["severity"] = verified_severity


def validate_policy(policy: dict) -> None:
    if (
        policy["schema_version"] != 1
        or policy["mode"] != "proposal_only"
        or set(policy["blocking_severities"]) != {"HIGH", "CRITICAL"}
    ):
        raise ValueError("Unsupported policy proposal")
    if (
        policy["block_new_confirmed_fixable"] is not True
        or policy["unreviewed_new_high_critical"] != "require_review"
    ):
        raise ValueError("Unreviewed risks cannot silently pass")
    if policy["unknown_severity_new_fixable"] != "require_review":
        raise ValueError("Unknown severities require review")
    if (
        type(policy["max_exception_days"]) is not int
        or not 1 <= policy["max_exception_days"] <= 30
    ):
        raise ValueError("Invalid exception lifetime")


def exceptions_for(
    records: dict[str, dict],
    document: dict,
    today: date,
    maximum: int,
    context_current: bool = True,
) -> list[dict]:
    if document["schema_version"] != 1:
        raise ValueError("Unsupported exception schema")
    outcomes = []
    ids: set[str] = set()
    fingerprints: set[str] = set()
    for entry in document["exceptions"]:
        required = {
            "id",
            "match",
            "fingerprint",
            "justification",
            "owner",
            "approved_by",
            "created_on",
            "expires_on",
            "review_reference",
        }
        if (
            set(entry) != required
            or entry["id"] in ids
            or entry["fingerprint"] in fingerprints
        ):
            raise ValueError("Incomplete or duplicate exception")
        for name in required - {"match"}:
            if (
                not isinstance(entry[name], str)
                or not entry[name].strip()
                or len(entry[name]) > 2000
            ):
                raise ValueError("Empty exception accountability field")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", entry["fingerprint"])
            or fingerprint(entry["match"]) != entry["fingerprint"]
            or "*" in json.dumps(entry)
        ):
            raise ValueError("Exceptions must have exact finding scope")
        created = date.fromisoformat(entry["created_on"])
        expires = date.fromisoformat(entry["expires_on"])
        if created > today or not 0 < (expires - created).days <= maximum:
            raise ValueError("Invalid exception duration")
        state = (
            "EXPIRED"
            if today >= expires
            else "CONTEXT_CHANGED"
            if not context_current
            else "UNMATCHED"
            if entry["fingerprint"] not in records
            else "ACTIVE"
        )
        outcomes.append({**entry, "state": state})
        if state == "ACTIVE":
            records[entry["fingerprint"]]["exception_id"] = entry["id"]
        ids.add(entry["id"])
        fingerprints.add(entry["fingerprint"])
    return outcomes


def evaluate(
    report: dict,
    evidence: dict,
    policy: dict,
    exceptions: dict,
    today: date,
    manifest: dict,
    bindings_match: bool,
    baseline: dict | None = None,
    baseline_hash: str | None = None,
) -> dict:
    validate_policy(policy)
    aliases = alias_index([report, *([baseline] if baseline else [])], evidence)
    records, states = normalize(report, aliases)
    previous, previous_states = (
        normalize(baseline, aliases) if baseline is not None else ({}, {})
    )
    approval = policy.get("baseline")
    approved = False
    baseline_state = "NOT_APPROVED"
    if approval is not None:
        if (
            baseline is None
            or not re.fullmatch(r"[a-f0-9]{64}", approval["report_sha256"])
            or approval["report_sha256"] != baseline_hash
            or any(s in {"SCANNER_ERROR", "NOT_RUN"} for s in previous_states.values())
        ):
            raise ValueError("Approved baseline is absent, altered or incomplete")
        if (
            not approval["approved_by"].strip()
            or not approval["review_reference"].strip()
            or date.fromisoformat(approval["approved_on"]) > today
        ):
            raise ValueError("Baseline has no valid approval")
        approved_on = date.fromisoformat(approval["approved_on"])
        expires_on = date.fromisoformat(approval["expires_on"])
        if not 0 < (expires_on - approved_on).days <= policy["max_exception_days"]:
            raise ValueError("Invalid baseline lifetime")
        approved = today < expires_on
        baseline_state = "ACTIVE" if approved else "EXPIRED"
    apply_reviews(records, evidence, aliases, today, bindings_match)
    for key, row in records.items():
        effective_manifest = (
            manifest if bindings_match else {**manifest, "embedded": {}}
        )
        row["context"] = context(row, effective_manifest)
        old = previous.get(key)
        regressed = old is not None and (
            LEVEL[row["severity"]] > LEVEL[old["severity"]]
            or row["scanner_fix_available"]
            and (
                not old["scanner_fix_available"]
                or set(row["scanner_fix_versions"]) != set(old["scanner_fix_versions"])
            )
        )
        row["change"] = (
            "NEW"
            if old is None
            else "REOPENED_CONTEXT"
            if not bindings_match
            else "REOPENED"
            if regressed
            else "EXISTING"
        )
        row["baseline_approved"] = approved
        row["priority"] = (
            "P3"
            if row["assessment"]["applicability"] == "NOT_APPLICABLE"
            else "P0"
            if row["severity"] == "CRITICAL"
            else "P1"
            if row["severity"] == "HIGH" and row["scanner_fix_available"]
            else "P2"
        )
    waivers = exceptions_for(
        records, exceptions, today, policy["max_exception_days"], bindings_match
    )
    blockers = []
    for key, row in records.items():
        reviewed = row["assessment"]
        is_new = not approved or row["change"] != "EXISTING"
        confirmed = (
            reviewed["applicability"] == "CONFIRMED_AFFECTED"
            and reviewed["fix_verification"] == "CONFIRMED"
            and bool(reviewed["verified_fixed_versions"])
        )
        unreviewed = reviewed["applicability"] != "NOT_APPLICABLE" and not confirmed
        important = (
            row["severity"] in policy["blocking_severities"]
            or row["severity"] == "UNKNOWN"
            and row["scanner_fix_available"]
        )
        if (
            is_new
            and important
            and not row.get("exception_id")
            and (confirmed or unreviewed and row["scanner_fix_available"])
        ):
            blockers.append(
                {
                    "fingerprint": key,
                    "reason": "new_confirmed_fixable"
                    if confirmed
                    else "new_requires_review",
                }
            )
    operational = (
        "SCANNER_ERROR"
        if "SCANNER_ERROR" in states.values()
        else "NOT_RUN"
        if "NOT_RUN" in states.values()
        else None
    )
    return {
        "schema_version": 1,
        "mode": "proposal_only",
        "status": operational or ("FINDINGS" if records else "PASS"),
        "proposed_gate_status": operational or ("FINDINGS" if blockers else "PASS"),
        "baseline_approved": approved,
        "baseline_state": baseline_state,
        "target_states": states,
        "assessment_context_matches": bindings_match,
        "input_occurrences": sum(r["occurrences"] for r in records.values()),
        "deduplicated_findings": len(records),
        "unique_component_advisories": len(
            {
                (
                    "python"
                    if row["origin"] in {"python", "lang-pkgs"}
                    else row["origin"],
                    row["package"],
                    row["version"],
                    row["advisory"],
                )
                for row in records.values()
            }
        ),
        "priorities": dict(Counter(r["priority"] for r in records.values())),
        "blockers": blockers,
        "exceptions": waivers,
        "findings": sorted(
            records.values(),
            key=lambda r: (
                r["priority"],
                r["target"],
                r["package"],
                r["version"],
                r["advisory"],
            ),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("Generated triage reports must remain outside Git")
    report_available = args.report.is_file()
    try:
        report = json.loads(args.report.read_text())
        validate_source_binding(report)
        policy = json.loads((ROOT / "security/vulnerability-policy.json").read_text())
        evidence = json.loads(
            (ROOT / "security/vulnerability-assessments.json").read_text()
        )
        exceptions = json.loads(
            (ROOT / "security/vulnerability-exceptions.json").read_text()
        )
        baseline = json.loads(args.baseline.read_text()) if args.baseline else None
        result = evaluate(
            report,
            evidence,
            policy,
            exceptions,
            datetime.now(UTC).date(),
            manifests(evidence),
            context_matches(evidence),
            baseline,
            digest(args.baseline) if args.baseline else None,
        )
        result.update(
            {
                "source_report_sha256": digest(args.report),
                "classified_on": datetime.now(UTC).date().isoformat(),
            }
        )
    except FileNotFoundError:
        state = "SCANNER_ERROR" if report_available else "NOT_RUN"
        result = {
            "status": state,
            "proposed_gate_status": state,
            "diagnostic": "Required report or configuration was not available",
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        result = {
            "status": "SCANNER_ERROR",
            "proposed_gate_status": "SCANNER_ERROR",
            "error_type": type(error).__name__,
            "diagnostic": "Invalid report, policy, exception or source binding; no approval",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"findings", "exceptions", "blockers"}
            }
        )
    )
    return {"PASS": 0, "FINDINGS": 1, "SCANNER_ERROR": 2, "NOT_RUN": 3}[
        result["status"]
    ]


if __name__ == "__main__":
    sys.exit(main())
