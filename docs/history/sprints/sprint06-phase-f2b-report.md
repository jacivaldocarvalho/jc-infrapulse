# Sprint 06 — F2B

Local, without updates. Files: `scripts/triage_vulnerabilities.py`,
`scripts/test_vulnerability_triage.py`,
`security/vulnerability-{policy,assessments,exceptions}.json`, and this report.

Usage: `python3 scripts/triage_vulnerabilities.py --report "$F2A/summary.json" --output "$OUT/triage.json"`.
F2A JSON files reused; no full scan. Temporary exports of pinned runtimes
provided metadata/ELF without loading images or creating containers.

FINDINGS result: 422 unique records by scanner/package/version/advisory/target;
420 component/advisory combinations. Aliases and duplicates are consolidated
without merging versions or targets. Classification separates runtime, build-only,
vendored, direct, transitive, and system components; tools present in runtime are not build-only.

Priorities: 47 fixable Alpine HIGH/CRITICAL findings, confirmed against
APK/secdb 3.23; two urllib3 copies vendored in pip, fixed in 2.8.0.
[CVE-2026-111497](https://github.com/google/brotli/releases/tag/v1.2.1):
brotli-libs 1.2.0-r0 → 1.2.1-r0; HTTP exposure was not demonstrated.

Nine contextually non-applicable assessments retain the findings: setuptools
Windows/macOS and components removed from pip/msgpack vendoring.
[Official vendoring](https://raw.githubusercontent.com/pypa/pip/26.2.2/pyproject.toml).
19 Debian matches remain affected, without a confirmed Trixie fix;
a fix in unstable/no-dsa does not prove a backport. Another 345 need further triage.

Proposed policy only: block new confirmed/fixable HIGH/CRITICAL findings;
material uncertainties require review. Candidate result: 49 confirmed, four
fixable UNKNOWN findings pending. Baseline not approved; exceptions empty.
Exceptions require exact identity/fingerprint, justification, owner,
approver, reference, and expiration within 30 days. Changed context/version/risk
reopens review; expiration never silently grants approval.

States/codes: PASS/0, FINDINGS/1, SCANNER_ERROR/2, NOT_RUN/3;
proposed decision separate, without hiding the backlog. Errors or missing data cannot be waived.

PASS: 23 new tests, 96 regressions, Ruff, actionlint, ShellCheck, Gitleaks,
whitespace, and F1 policy. Workflow/12 jobs unchanged.
Git develop/1288ad74, staging empty. Entire doctor/hooks PASS, one checkpoint
pending; Brain covers HEAD.
Risks: divergent setuptools sources, backports, unreplicated reachability.
No staging/commit/push/deploy; F3 not started.
