# Sprint 06 — F2A

Baseline develop/1288ad74 preserved. Informational local implementation.
Files: `scripts/audit_vulnerabilities.py`, `scripts/test_vulnerability_audit.py`,
`security/vulnerability-scanners.json`, and this report.

Execution: `python3 scripts/audit_vulnerabilities.py --pip-audit "$TOOLS/scanner-venv/bin/pip-audit" --trivy "$TOOLS/trivy" --build-images --output-dir "$REPORT_DIR"`.
Isolated scanners: pip-audit 2.10.1, npm 11.16.0, Trivy 0.76.1;
Trivy file and executable hashes verified.

Final result: FINDINGS in all four targets, with no operational errors:

| Target | Coverage | Findings |
|---|---|---:|
| Python | 52 unique requirements, including build | 2 |
| npm | 395 dependencies; 14 vulnerable packages | 15 |
| Backend | 87 Debian packages; 74 libraries | 176 |
| Nginx | 71 Alpine packages | 229 |

Total: 422 findings, not unique CVEs; 1 CRITICAL, 95 HIGH, 143 MEDIUM,
15 MODERATE, 159 LOW, 9 UNKNOWN. Critical: brotli-libs/CVE-2026-111497,
1.2.0-r0 → 1.2.1-r0. Setuptools: CVE-2026-59890/GHSA-h35f-9h28-mq5c
(83.0.0), GHSA-xmj3-9gf7-h52w (84.1.0).

Sources: PyPI/Python Advisory Database and npm, APIs without a frozen revision;
Trivy DB v2/ghcr.io, updated 2026-10-31T01:14:32Z, SHA-256 recorded in JSON.
Four sanitized reports and a summary outside Git; IDs/aliases, severities,
fixes, input hashes, and times preserved.

States: informational CLEAN/FINDINGS (0), ERROR (2), NOT_RUN/INCOMPLETE (3).
Preliminary ERROR states preserved; BIT/TEMP support tested.

PASS: 22 new tests, 74 existing regressions, Ruff, actionlint, ShellCheck,
whitespace, Gitleaks, and F1 policies. Cleanup limited to owned resources; pre-existing resources preserved.

Cost: 58,1s; child CPU 40,7s; child RSS 506MiB; temporary cache 1,61GiB,
removed; Docker daemon/cache excluded from these measurements.

Limitations: Python severity UNKNOWN; applicability/backports not triaged;
npm suggestions may involve downgrades or major versions. No remote CI or F2B/F3 fixes.
Git without staging/commit/push; Entire doctor/hooks PASS, checkpoint pending;
Brain indexes HEAD only.
