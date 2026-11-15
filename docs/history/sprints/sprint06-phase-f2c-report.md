# Sprint 06 — F2C: review fixes

Local decision: READY_FOR_REVIEW; remote approval NOT RUN.

Files: `.github/workflows/ci.yml`, `scripts/validate-images.py`,
`scripts/vulnerability_ci.py`, `scripts/audit_vulnerabilities.py`,
`scripts/test_image_validation.py`, `scripts/test_supply_chain.py`,
`scripts/test_vulnerability_ci.py`, `scripts/test_vulnerability_audit.py`,
and this report. Dependencies, scanners, images, and F1 policies unchanged.

Fix 1: export/packaging and upload are separate, exclusively informational
steps with `continue-on-error`. Outputs use `outcome`, before conversion to
`conclusion`; failures reach vulnerability-audit as SCANNER_ERROR.
Docker validation remains blocking; containers are cleaned up, tags are
retained after success and removed by mandatory `always()` cleanup.
The 11 ci-required dependencies and 13 jobs remain.

Download uses the exact pattern for this run and digest-mismatch=error.
Missing artifact: NOT_RUN/3; received artifact without a manifest, with invalid SHA,
or with export/upload/transport failure: SCANNER_ERROR/2. None becomes PASS.
Sanitized JSON retains commit/run/attempt/scanners; JSON retention seven days,
images one day. FINDINGS remains informational, with no approved baseline/exceptions.

Fix 2: a present Vulnerabilities field must be a list; {}, false, 0, empty string, null,
and other types fail even with a positive inventory and zero exit code. [] and an
omitted field remain valid. Red-green testing demonstrated ten invalid normalization
cases and eight handoff cases before the fixes.

Validation: 145 image, lifecycle, F1/F2A/F2B/F2C tests, Ruff, actionlint,
ShellCheck, Gitleaks, whitespace, and F1. Consulted the
[steps/outcome/continue-on-error documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#steps-context) and [pinned source](https://raw.githubusercontent.com/actions/download-artifact/9000827ccba6bdab643e8b6fd33ac0654aef8333/src/download-artifact.ts). Local fixtures do not
replace a GitHub Actions run.

Risks: mutable APIs/databases, Docker archive traffic/storage, and
runner cancellation/timeouts. 422 findings, including 345 pending,
retained without approval; F2B context invalidated.

Git develop/1288ad74, staging empty. Entire doctor/hooks OK, one checkpoint
pending; Brain covers HEAD, worktree not indexed. No Docker, full scans,
staging, commit, push, deploy, or F3.
