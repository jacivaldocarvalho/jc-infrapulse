# Sprint 06 — F1B

Local implementation; remote NOT RUN. Baseline develop/bee93e8 preserved.

F1B files: `.github/{workflows/ci.yml,dependabot.yml}`, `README.md`,
`backend/{Dockerfile,pyproject.toml,requirements.lock,requirements-runtime.lock,requirements-build.lock,install-locked.sh}`,
`frontend/Dockerfile`, `scripts/{check_supply_chain.py,test_locked_install.py}`, and this report.
F1A preserved.

PASS: 49 original versions unchanged, SHA-256 hashes obtained from official PyPI; 38 runtime requirements; bootstrap pip 26.2.2, setuptools 82.0.1 (compatible with the prior range), wheel 0.48.0, and packaging 26.3. Installation requires hashes and wheels; the local package is installed without an index, dependencies, or isolation that downloads tools. A tampered hash was rejected in a real download. Lockfiles were compared for discrepancies.

PASS: four FROM statements use three verified multi-architecture index digests in the registry, retaining tags and platforms. Backend/runtime, backend/test, frontend/build, and Nginx builds; independent OpenAPI type comparison, API health 200, readiness 503 without a database, worker/SIGTERM, negative heartbeat, and Nginx assets/health.

PASS: 184 unit tests, 25 F1A regressions, six F1B tests, 22 lifecycle tests, 21 inspector tests; Gitleaks/sanitized fixtures; Ruff/format, actionlint, ShellCheck, and whitespace. Full audit without violations; CI uses `--enforce all`, preserving ten jobs, security-foundations, and ci-required.

Weekly Dependabot for pip/npm/Docker/Actions, PRs targeting develop, a limit of three per ecosystem, and no automerge. `.lock` files require reviewed manual synchronization; remote activation and operation have not yet been verified.

Cleanup limited to owned resources; shared cache retained. Pre-existing resources preserved. Installation: 10,53s; unit tests: 8,56s; final builds/smokes: 77,80s. pip/root warnings during the build remain visible; runtimes are non-root.

Risks: amd64 validated; other platforms not run; hashes do not establish provenance; build tools remain in the runtime; CVEs and APT packages were not audited. Git without staging/commit/push; Entire doctor/hooks PASS, one earlier checkpoint pending; Brain limited to HEAD. Decision: review changes before authorizing versioning and remote CI.
