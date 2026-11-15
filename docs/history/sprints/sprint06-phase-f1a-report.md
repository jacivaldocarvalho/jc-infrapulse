# Sprint 06 — F1A

Local PASS; remote CI NOT RUN. Branch develop, HEAD bee93e8, without staging/commit/push.

Files: `.github/workflows/ci.yml`, `security/gitleaks.toml`,
`scripts/check_secrets.py`, `scripts/check_supply_chain.py`,
`scripts/test_supply_chain.py`, and this report.

New required gate `security-foundations`: Gitleaks, npm integrity, and Actions policies. Ten previous jobs structurally unchanged; `ci-required` also requires the new job. The js-yaml 4.3.2 parser was already locked, with no added dependency.

[Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1), Linux x64,
SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
Positive and negative synthetic fixtures; versionable checkout and HEAD ancestors; no custom baseline/exclusions or inline permissions. Output: rule, line, and path hash; no raw match, token, or log is published. Errors fail closed.

FAIL/F1B_PENDING: 49 Python requirements without hashes; setuptools version inexact and unlocked, pip implicit; four external FROM statements without digests. `--enforce all` returns 1; the active mode returns 0 while explicitly reporting a FAIL audit. F1B must also require actual hash verification during Python installation.

PASS: `python3 scripts/test_supply_chain.py` (25), lifecycle (22), inspector (21); Ruff check/format, actionlint, ShellCheck, and whitespace including new files; 35 aggregator scenarios, sanitization, and disposable installation without `.env`.

Local duration: npm ci/existing cache 3,12s; Gitleaks/download 3,64s; policies 0,08s; new regressions 1,26s. npm: 370 packages, 323MiB; remote overhead was not measured.

Entire: doctor/hooks PASS, one earlier checkpoint pending; Brain covers HEAD, with local changes not yet indexed. No configuration was modified.

Risks: false positives, growing history, external download, transitive parser. A digest does not establish provenance; CVEs/SBOM/attestations and Compose images were not verified. Docker was not run; locks, images, and Dockerfiles remained intact. Ready for review, awaiting authorization to version the changes and validate remotely.
