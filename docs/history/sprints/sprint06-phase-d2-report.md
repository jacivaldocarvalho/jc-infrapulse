# Sprint 06 — Phase D2: Playwright E2E and observability CI

Date: 2026-10-30. Implementation published; remote run
**41067075376: FAIL**. A subsequent test-isolation fix was validated locally
only, without staging, commit, or push in that round. Another complete remote
run is required before D2 approval. Sections preceding the final fix preserve
the history of local runs and publication.

## Baseline, inspection, and problems found

Branch `develop`, HEAD preserved at
`8598ab8a6c3090360ced5401403e37fc4872fd4d`; working tree initially clean.
Read AGENTS.md, the Entire guide, workflow, validators, Compose files,
dependencies, Playwright configuration, E2E scenarios, fixtures, and real
observability tests. Brain was consulted, but its index was behind; current
code was the source of truth. No Entire refresh, repair, or configuration change.

Problems documented before the fixes:

| Problem | Fix limited to test infrastructure |
| --- | --- |
| E2E validator required the develop branch | Removed branch/Git dependency; validated in a copy without .git |
| Main Compose check resolved backend/.env | Runner validates only its disposable Compose with --env-file /dev/null |
| E2E required free port 8080, occupied by local frontend | Docker publishes a dynamic port on 127.0.0.1; runner discovers E2E_BASE_URL |
| Fixed 172.30.240/241 subnets | Removed; Docker allocates networks; execution authorization uses the target's actual /32 IP |
| Names lacked explicit preflight/ownership | Unique name, resource check, and exclusively created marker |
| Observability reran the full PostgreSQL suite | D1 retained as a separate gate; D2 runs only the real stack |
| Broad test selection caused skips | Two exact node IDs selected; JUnit rejects skip, failure, error, or missing case |
| Collector restart lacked recovery check | Full real test repeated after Collector restart; worker shutdown/restart checked |
| No Playwright JUnit | JUnit reporter added, retaining HTML, traces, and screenshots |

## Files

Modified:

- `.github/workflows/ci.yml`.
- `compose.frontend.test.yaml` and `compose.observability.test.yaml`.
- `frontend/playwright.config.ts`.
- `scripts/validate-dashboard.py` and `scripts/validate-observability.sh`.
- `README.md`, `docs/frontend.md`, and `docs/observability.md`.

Created:

- `scripts/integration_support.py`: shared lifecycle without new dependencies.
- `scripts/test_integration_support.py`: 20 unittest cases after fixes, including
  the eight original lifecycle tests, without Docker access.
- `scripts/validate-observability.py`: orchestrates validators and real scenarios.
- `docs/sprint06-phase-d2-report.md`.

The application, worker, SSRF policy, migrations, Dockerfiles, production Compose,
dependencies/locks, Playwright tests, and backend tests were not modified. The
observability shell wrapper remains available and delegates to the Python runner.
The seven preceding validation jobs were structurally compared with HEAD and
remain identical. Only the aggregator's needs list was extended.

## Jobs

| Job | Environment and execution | Limits |
| --- | --- | --- |
| playwright-e2e | Ubuntu 24.04, Python 3.12, Node 24.18.1, npm ci, locked Playwright Chromium, real stack, eight scenarios | Job 35 min, browser install 5 min, integration 25 min, cleanup 3 min |
| observability-integration | Ubuntu 24.04, Python 3.12, Docker Compose, validators, normal/outage/recovery scenarios, and correlation | Job 35 min, integration 25 min, cleanup 3 min |

Playwright installs Chromium/system dependencies with
`npm exec --no -- playwright install --with-deps chromium` after npm ci in frontend.
It does not resolve an arbitrary Playwright version. Node 24.18.1 matches the
Dockerfile and existing engines. Both jobs run lifecycle security tests. pip is
not installed on these job hosts; backend dependencies come from existing
Dockerfiles and constraints.

Frontend E2E is served by real Nginx connected to the disposable API and
PostgreSQL. The existing Dockerfile exports OpenAPI from the same checkout,
generates types, and builds the frontend. There is no manual schema copy or
consumption of another commit/run.

E2E tests run unchanged: real CRUD/worker UP; history, metrics/p95/charts; empty
states; filters/pagination/refresh errors; proxy allowlist and SSRF;
keyboard/mobile/themes/axe; two deletion regressions. Existing network
interceptions remain limited to injecting failure or latency; no real integration
was replaced with mocks. Existing pageerror and no-new-read-after-DELETE checks
were preserved.

Observability runs in sequence:

1. Build existing services and start the controlled target.
2. Run Promtool config/rules/test-rules checks, Collector validate, and amtool
   check-config using the project's own Compose images and mounts.
3. Start PostgreSQL in tmpfs, run migration, and start API, worker, Collector,
   Tempo, Prometheus, Grafana, Alertmanager, and HTTP target.
4. Check ports/networks and `test_real_observability_stack`, one case, zero skips.
5. Extract real IDs from structured API/worker logs and confirm them in Tempo.
6. Stop Collector and run only `test_collector_outage_real_processes`.
7. Restart Collector and repeat the complete real test, including metrics, traces,
   dashboard/data-source queries, and integration alert delivery.
8. Stop worker, require worker_stopped/scheduler_stopped logs, and restart with --wait.

Grafana uses admin and PostgreSQL database/user values explicitly defined in the
disposable environment. A final run with conflicting process-environment values
proved they were not inherited by the stack.

## Isolation and cleanup

Each job uses `INFRAPULSE_TEST_PROJECT` with its own prefix, run ID, and attempt.
Locally, the runner generates a random suffix without depending on directory or
branch names. Before creating resources, it rejects containers, networks, or
volumes with that project name. A marker in RUNNER_TEMP (or local /tmp) is created
atomically and contains no password.

Ephemeral passwords are generated in memory and masked before Docker commands
on GitHub. Logs contain no `.env`, credential file, environment dump, or resolved
configuration. Compose files use --env-file /dev/null and explicit project names.

Docker allocates networks without fixed IPAM. After starting monitoring-target,
the runner gets its IP and authorizes only that /32, exact hostname, and expected
port. It does not expand checker access to the runner's private network. SSRF
code was unchanged.

E2E publishes only Nginx on loopback at a dynamic port, with user 101 and a
read-only filesystem. API, worker, target, and PostgreSQL publish no ports.
The database remains on the internal data network; the worker reaches the
target through the internal targets network.

The observability override removes unnecessary egress bridges from API, worker,
and Grafana. The database uses only internal; telemetry services use the internal
telemetry network. No administrative, metrics, or PostgreSQL port is published.
Telemetry volumes belong only to the disposable project; the database uses tmpfs.

The context manager runs down --volumes --remove-orphans on success or failure,
confirms no resources remain with the project label, and compares IDs, StartedAt,
RestartCount, and status of pre-existing containers. SIGTERM shuts down via
finally. The workflow also has an always() fallback --cleanup step using the
same project name and marker. Without an ownership marker, it does not run down.
Cleanup failures or changed pre-existing containers fail validation. There is
no prune, firewall change, privileged container, or command against the local stack.

## Reports and artifacts

- `playwright-e2e-results`: frontend/test-results and frontend/playwright-report,
  with JUnit at test-results/playwright.xml; traces/screenshots on failures and
  screenshots/timelines already produced by scenarios.
- `observability-integration-junit`: only normal.xml, outage.xml, and recovery.xml
  in RUNNER_TEMP/observability-results, copied from test containers with docker cp.

Uploads use always(), seven-day retention, and failure on missing files. They do
not include database, volumes, marker, environment, or credentials. Final files
were inspected: no .env, symlinks, or token/private-key patterns. Traces contain
only disposable-stack traffic and fixtures, not the Grafana administration UI.
Pattern checks are not an exhaustive audit.

## Local validation

A clean copy in `/tmp/infrapulse-s06d2-clean` was created from versioned files
and authorized local changes, without .git, .env, node_modules, or generated types.
`npm ci` installed 370 packages and reported zero vulnerabilities in its
incidental audit. Chromium 1248 already on the host was reused; no system
packages were installed locally. CI's --with-deps command still requires remote execution.

| Check performed | Result |
| --- | --- |
| Ruff check/format for seven Python scripts | PASS |
| Lifecycle unittest | PASS — 8 cases: name, conflict, daemon, ownership, failure, and error preservation |
| TypeScript, ESLint --max-warnings=0, and Prettier | PASS |
| OpenAPI/type generation from clean checkout | PASS |
| Nginx/API/worker/observability Docker build | PASS — existing Dockerfiles |
| Isolated Playwright | PASS — 8 cases in 28,6 s, no failures, errors, or skips |
| Deletion E2E regressions | PASS — zero new automatic reads; cancellation before DELETE observed |
| Accessibility/keyboard/mobile/themes | PASS — original scenario and axe preserved |
| Promtool, Collector validate, and amtool | PASS |
| Normal observability | PASS — 1 case, 52,11 s, zero skips |
| Collector unavailable | PASS — 1 case, 1,09 s, zero skips; API ready and worker persisted UP |
| Collector recovery | PASS — repeated real case, 1,73 s, zero skips |
| Log/trace correlation | PASS — API and worker IDs found in Tempo |
| Worker shutdown/restart | PASS — stop and startup logs with --wait |
| Final JUnit/artifacts | PASS — 8 Playwright cases and three one-case XMLs |
| Cleanup and pre-existing containers | PASS — disposable resources gone; IDs/start times/restarts/status preserved |
| Aggregator | PASS — 262.144 combinations, only nine successes accepted |
| actionlint, bash -n, ShellCheck, and git diff --check | PASS |
| Local OS package installation via --with-deps | NOT RUN — host already runs Chromium; no environment change |
| D2 GitHub Actions | NOT RUN — commit/push not authorized |

No final check remained FAIL or BLOCKED. Remote limitations remain NOT RUN.

The first local attempt ran both validators in parallel on one daemon. Playwright
passed seven cases but failed the refresh scenario: the trace showed 17
net::ERR_NETWORK_CHANGED responses while the other stack removed networks,
in addition to the single intentional 503. The guard found that temporary
observability containers present in E2E's initial snapshot had disappeared.
Both stacks were removed. Failure evidence was retained in /tmp. E2E was
repeated alone and passed without changing tests, assertions, or product. On
GitHub the jobs use separate runners; locally they must run sequentially.

An npm ci attempt in the root directory was also rejected for lacking a lock;
it was repeated in frontend in the temporary copy. No dependency in the existing
local environment was updated.

Main commands:

```bash
npm --prefix frontend ci
# No CI, dentro de frontend:
npm exec --no -- playwright install --with-deps chromium
python3 scripts/test_integration_support.py
python3 scripts/validate-dashboard.py
bash scripts/validate-observability.sh
# Fallback CI, com INFRAPULSE_TEST_PROJECT idêntico ao da execução:
python3 scripts/validate-dashboard.py --cleanup
bash scripts/validate-observability.sh --cleanup
```

Local evidence outside Git:

- `/tmp/infrapulse-s06d2-clean/evidence/playwright-final.log`.
- `/tmp/infrapulse-s06d2-clean/frontend/test-results/playwright.xml` and playwright-report.
- `/tmp/infrapulse-s06d2-clean/evidence/observability-final.log`.
- `/tmp/infrapulse-s06d2-clean/evidence/observability-final/{normal,outage,recovery}.xml`.
- `/tmp/infrapulse-s06d2-clean/evidence/first-test-results/`, with trace/screenshot from the initial failure.
- `/tmp/infrapulse-s06d2-final-check.py`, helper for structure/aggregator/artifact verification.

## Quality gate, security, and residual risks

ci-required retains its public name, always(), and prior code. It requires the
seven existing jobs plus playwright-e2e and observability-integration. The exact
block was evaluated in 262.144 success/failure/cancelled/skipped combinations:
only nine successes pass. Failures and skips in new dependencies prevent approval.

Contents: read permissions, triggers, concurrency, timeouts, checkout without
persisted credentials, and full SHAs were preserved. npm cache uses package-lock.json.
No action was updated; inherited Node.js 20 warnings may reappear. There is no
pull_request_target, production secret, publication/deploy, or suppression to
obtain PASS. pip build, initdb, and NO_COLOR/FORCE_COLOR warnings were retained;
no new dependency was installed and no validation was disabled.

The runner needs Compose support for !reset/!override tags in test Compose files.
This passed with local Compose 5.6.0; a hosted run is still required. Forced
runner loss may prevent cleanup despite finally and always() fallback. Builds
and downloads depend on registries; existing Docker tags remain mutable.
Local images/cache remain; disposable containers, networks, and volumes were
removed. Validators should not share a daemon while Chromium is active.

Branch and HEAD remain at baseline. Changes are limited to the listed 13 files,
without staging, commit, or push. Entire remains enabled and reports one pending
D1 checkpoint (`01M66BC9ERQK3REFAJC1FCWQ1M`), without a sync error; no manual
checkpoint, repair, hook change, or synchronization was run. Local frontend,
API, and PostgreSQL retained their IDs and stayed healthy.

Recommendation: ready for local review and later authorization for versioning
and remote validation. Do not consider D2 finally approved before the nine
gates and aggregator succeed on GitHub Actions. No later phase was started.

Suggested Conventional Commit, not executed:
`ci(integration): add Playwright and observability gates`.

## Local error-propagation fix — D2 review

This round fixes only the validators and their tests. There was no staging,
commit, push, Docker run, or new Playwright/observability run. The integration
results above are historical; D2 still lacks remote approval.

### Defects, root cause, and fix

- **A:** both entrypoints called `main()` directly. An unhandled
  `CalledProcessError` made the interpreter exit with 1, discarding the original
  code, including 17. Both now use `run_cli`, which logs a traceback diagnosis
  and propagates subprocess codes from 1 to 255.
- **B:** `docker cp` in `run_case`'s `finally` could replace the pytest exception.
  Collection now runs in a separate block: a collection failure is logged and
  the original primary exception is re-raised. Without a primary failure,
  collection failure still prevents success. The same precedence applies to
  project shutdown: cleanup and inspection are attempted separately, all
  secondary errors are logged, and none replaces an existing primary cause.
- The logger masks the three ephemeral credential values generated by the
  project before emitting messages/tracebacks. Captured test output is masked
  too. No dependency or error suppression was added.

Exit policy: normal success returns 0; valid codes 1–255 are preserved. Negative
statuses from valid signals become `128 + sinal`, per shell convention: SIGTERM
(-15) becomes 143 and SIGKILL (-9) becomes 137. An entrypoint cannot directly
represent a negative subprocess status. Unexpected exceptions without a numeric
code and malformed `CalledProcessError` values (0 or outside the representable
range) return 1 with a diagnostic, avoiding accidental success from OS truncation.

Files changed in this fix, all already among the 13 D2 files:

- `scripts/integration_support.py`: entrypoint, masking, cleanup precedence.
- `scripts/validate-dashboard.py`: adoption of the shared entrypoint.
- `scripts/validate-observability.py`: entrypoint and collection without replacing the primary cause.
- `scripts/test_integration_support.py`: deterministic regressions without Docker.
- `docs/sprint06-phase-d2-report.md`: this record.

### Regression matrix and evidence

| Scenario | Observed result | Status |
| --- | --- | --- |
| Entrypoints: 0, 1, 17, and 127 | Identical code in both validators | PASS |
| SIGTERM/SIGKILL signals | 143/137 in both validators | PASS |
| Invalid CalledProcessError: 0, 256, 512 | Failure with code 1 and diagnostic | PASS |
| Test 17; successful collection | Original exception preserved; CLI propagates 17 | PASS |
| Test 17; collection 127 | Original cause preserved; collection logged | PASS |
| Test passes; collection 127 | Collection exception propagated; CLI propagates 127 | PASS |
| Test and collection pass | Success | PASS |
| Unexpected collection exception, with/without primary failure | Precedence and diagnostic preserved | PASS |
| Primary 17, cleanup 23, unexpected inspection | Primary preserved; both secondary errors logged | PASS |
| No primary, cleanup 23 | Secondary failure propagated | PASS |
| Traceback, messages, synthetic credential | Useful diagnostic preserved; credential masked | PASS |

Before the fix, the 20 executed tests produced 18 failing subcases,
demonstrating the defects. After the fix, the same 20 tests passed in 2,366 s:
eight existing lifecycle tests and 12 new regression methods with subcases.
AST comparison confirmed that the original class containing eight tests was
unchanged. Entry points run in controlled Python subprocesses; collection and
lifecycle use mocks. No existing integration test was changed.

| Validation in this round | Result |
| --- | --- |
| Regressions before fix | Expected FAIL — 18 subcases reproduced defects |
| Regressions and eight lifecycle tests after fix | PASS — 20 tests |
| Ruff check and Ruff format --check on scripts | PASS |
| ShellCheck and bash -n on observability wrapper | PASS |
| actionlint on current workflow | PASS |
| ci-required | PASS — 32 scenarios with exact aggregator code |
| git diff --check, including new files | PASS |
| Repeat Docker, Playwright, and observability stack | NOT RUN — deterministic tests sufficient for this fix |
| D2 GitHub Actions | NOT RUN — no authorization to version in this round |

Validation commands:

```bash
python3 scripts/test_integration_support.py
backend/.venv/bin/ruff check --no-cache scripts
backend/.venv/bin/ruff format --check --no-cache scripts
shellcheck scripts/validate-observability.sh
bash -n scripts/validate-observability.sh
/tmp/infrapulse-s06b-actionlint-feulusd_/actionlint -color .github/workflows/ci.yml
git diff --check
# Arquivos novos: git diff --no-index --check /dev/null <arquivo>
```

The workflow was unchanged in this round. An additional aggregator check
confirmed the nine required names, `always()`, and the actual code: all-success,
empty dependencies, failure/cancelled/skipped for each job, and contract
failure/cancellation/skip with frontend-quality/frontend-build skipped. Only
the scenario with all nine successes passes. This revalidation had 32 scenarios,
in addition to the exhaustive historical matrix documented above.

Temporary evidence: `/tmp/infrapulse-s06d2-error-fix/red-tests.log`,
`green-tests.log`, and `validation-summary.json` in the same directory. The
`before-files.json` manifest confirms that the other eight D2 files were
preserved in this round. These temporary files are not published artifacts.

Git remains on `develop`, HEAD `8598ab8a6c3090360ced5401403e37fc4872fd4d`,
with 13 D2 files pending and nothing staged. Entire was queried read-only:
enabled, active session, one prior D1 checkpoint pending. Brain still uses seed
`bf315771`, freshness `unsafe` because it is behind; current code was the source
of truth. No repair, refresh, forced synchronization, manual checkpoint, or
hook/configuration change occurred.

Remaining risks: this round validates propagation with controlled processes and
mocks, without repeating real infrastructure scenarios. Masking covers
project-generated credentials; it is not a universal sanitizer for external
output. A real GitHub Actions run is still required. Recommendation: resume
final review before requesting new authorization for staging, commit, and push.

## Sequential revalidation for controlled publication

After renewed explicit authorization for one commit/push, the 13 files were
reviewed again on `develop`, HEAD `8598ab8a6c3090360ced5401403e37fc4872fd4d`.
The corrected validators ran sequentially without further implementation
changes in temporary copy `/tmp/infrapulse-s06d2-publication-7wtydhk9`, without
`.git` or `.env`. Frontend dependencies and Chromium already installed in the
prior temporary copy were reused; no local environment was reinstalled. A
SHA-256 manifest confirmed the identity of all 13 files before this documentation update.

| Check | Result |
| --- | --- |
| Regressions and lifecycle | PASS — 20 tests in 2,589 s |
| Ruff check and Ruff format --check on scripts | PASS — seven formatted files |
| actionlint, ShellCheck, and bash -n, including workflow shell blocks | PASS |
| ci-required | PASS — 262.144 combinations; only nine successes accepted |
| Seven preceding validation jobs, permissions, triggers, concurrency, and SHAs | PASS — preserved |
| Playwright | PASS — eight scenarios, 23,4 s; zero failures, errors, or skips |
| Service deletion | PASS — two regressions, zero new automatic reads after success and cancellation of pending read |
| Accessibility | PASS — original desktop/mobile scenario, keyboard, both themes, and axe |
| Normal observability | PASS — one test in 50,82 s; zero skips |
| Collector unavailable | PASS — one test in 1,07 s; zero skips |
| Collector recovery | PASS — repeated real test in 1,46 s; zero skips |
| Log/Tempo correlation | PASS — real API and worker IDs found in Tempo |
| Worker and scheduler | PASS — graceful shutdown and healthy restart |
| Both validators' exit codes | PASS — both exited zero |
| Cleanup and pre-existing resources | PASS — containers, networks, and volumes preserved |
| Local artifacts | PASS — 13 files inspected; JUnit with 8 + 1 + 1 + 1 cases, no skips |
| git diff --check, including four new files | PASS |
| D2 GitHub Actions | NOT RUN in this pre-commit/push record |

Exclusive projects: `infrapulse-e2e-publication-7wtydhk9` and
`infrapulse-observability-publication-7wtydhk9`. Before each run, absence of
project-labeled containers/networks/volumes and a conflicting marker was
confirmed. E2E published only Nginx at `127.0.0.1:32770`; PostgreSQL and
observability published no ports. Cleanup removed all owned resources and
markers. Final comparison confirmed IDs, StartedAt, RestartCount, and status
of five pre-existing containers, plus network and volume identities. Validators
did not run in parallel.

Evidence in the temporary directory above: `evidence/playwright.log`,
`frontend/test-results/playwright.xml`, `frontend/playwright-report/`,
`evidence/observability.log`, `evidence/observability/{normal,outage,recovery}.xml`,
`evidence/results.json`, pre-integration snapshots, and the file manifest.
Artifact inspection found no `.env`, symlink, or token, private-key, and
authentication-header patterns. This inspection is limited to produced files
and checked patterns, not a universal sensitive-data audit. The Playwright
NO_COLOR/FORCE_COLOR warning remained visible; no `ERR_NETWORK_CHANGED`
occurred in this sequential run.

Entire: `doctor` confirmed intact hooks and no stuck session. `status` shows D1
checkpoint `01M66BC9ERQK3REFAJC1FCWQ1M` pending normal synchronization on the
next push. Local metadata and transcript (282.514 bytes) are present; no
concrete traceability-loss risk was identified. Brain remains behind at
`bf315771`; current code was the source of truth. No repair, manual checkpoint,
hook change, or forced synchronization occurred.

`origin/develop` was queried and still matched baseline. After staged-diff
review, authorized publication should include only the 13 D2 files while
preserving Entire hooks. Local success permits that publication; D2 closure
depends on the ten jobs in GitHub Actions. Do not start Phase E.

## Local test-isolation fix — after remote run

Baseline for this round: `develop`, HEAD
`ef547b4ab61512974c8efb38c2026f78b58df938`, working tree initially clean.
Run [41067075376](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41067075376)
ended in FAIL: eight jobs passed; `observability-integration` and `ci-required`
failed. The real observability test never started on the remote runner.

Cause confirmed before changing the test: `run_entrypoint` copied all of
`os.environ` into the subprocess. Test
`test_unexpected_failure_diagnostic_redacts_project_password` constructs
`IntegrationProject('e2e', [])`, but inherited the job's
`infrapulse-observability-41067075376-1` name. The guard correctly rejected the
incompatible prefix with `ValueError: Unexpected disposable project name`
before generating the credential and expected diagnostic. The affected
subcases were `validate-dashboard.py` and `validate-observability.py` in the
same test method.

Pre-fix reproduction without Docker:

```bash
INFRAPULSE_TEST_PROJECT=infrapulse-observability-41067075376-1 \
GITHUB_ACTIONS=true python3 scripts/test_integration_support.py \
  EntrypointStatusTests.test_unexpected_failure_diagnostic_redacts_project_password
```

Result: expected FAIL, code 1, one method with two subcases failing because
`preserved diagnostic` was absent; evidence in
`/tmp/infrapulse-s06d2-isolation-red.log`. The security guard worked; the
defect was test-environment inheritance, not production validation.

Fix limited to `scripts/test_integration_support.py`: helper `run_entrypoint`
now supplies an explicit environment dictionary containing only
`INFRAPULSE_TEST_PROJECT=infrapulse-e2e-unit` and `GITHUB_ACTIONS=false`, rather
than copying the parent process environment. The interpreter is invoked by
absolute `sys.executable`; these scenarios do not need inherited PATH, HOME,
CI variables, or credentials. CPython may add `LC_CTYPE` at startup for locale
coercion; regression recognizes only that runtime addition. The fix makes no
global change to `os.environ`.

Two regression methods were added: one inspects the child's effective environment
in both entrypoints, rejects additional keys, and compares the parent's environment
before and after; the other instantiates the real class in subprocesses outside
the synthetic helper and proves a valid project passes while an observability
prefix, a prefixless name, and an uppercase name remain rejected. The earlier
20 tests, including exit codes, signals, collection, cleanup, and generated
credential masking, remain preserved.

Red-green: with the two new methods but before the environment change, the
22-test suite failed in four subcases under CI-like environment. After the fix,
the same 22 tests passed in each of the five configurations below (110 method
runs total), always without Docker:

| Test process environment | Result after fix |
| --- | --- |
| Empty environment; INFRAPULSE_TEST_PROJECT absent | PASS — 22 tests, 2,613 s |
| Valid project infrapulse-e2e-parent-valid | PASS — 22 tests, 2,679 s |
| Observability CI: infrapulse-observability-41067075376-1 | PASS — 22 tests, 2,514 s |
| Invalid project jc-infrapulse | PASS — 22 tests, 2,185 s; direct guard rejects name |
| Playwright CI: infrapulse-e2e-41067075376-1 | PASS — 22 tests, 2,396 s |

CI-equivalent environments include `CI=true`, `GITHUB_ACTIONS=true`, run ID,
run attempt, RUNNER_TEMP, three synthetic-only passwords, and a sentinel
variable. The test confirms none of those extra entries reaches the synthetic
child and the parent remains unchanged. The masking test confirms a useful
diagnostic, nonzero exit, and absence of the generated credential from stderr.
The invalid name in the outer environment is not accepted by the real class;
the suite passes because only the synthetic subprocess receives an explicitly
valid name.

Additional validations: Ruff check, Ruff format --check (seven Python scripts),
actionlint, ShellCheck, and git diff --check: **PASS**. Workflow, production,
`integration_support.py`, SSRF, Dockerfiles, Compose, and Entire configuration
were unchanged. Docker and a new GitHub Actions run: **NOT RUN** in this round.

Red evidence: `/tmp/infrapulse-s06d2-isolation-red.log` and
`/tmp/infrapulse-s06d2-isolation-red-suite.log`. Green evidence:
`/tmp/infrapulse-s06d2-isolation-bfn9n05s/{clean-absent,valid-project,ci-observability,invalid-project,ci-playwright}.log`
and `results.json` in the same directory. The matrix ran with
`subprocess.run([sys.executable, 'scripts/test_integration_support.py'], env=...)`,
fully replacing each test process environment without changing the environment
of the orchestrating process.

Round state: branch and HEAD preserved at baseline; only the test and this
report modified, nothing staged. Entire remains enabled; doctor found no stuck
sessions or hook failures. `status` again showed one pending D2 checkpoint
(`01M66HBTQ3KB1YWV8QM5RY8HV9`); the previous push had synchronized checkpoints,
so this was the subsequently observed local state, without another sync attempt.
No manual checkpoint, repair, or hook/configuration change.

Recommendation: ready for another review and authorization of a separate commit,
followed by full remote validation of all ten jobs. This fix does not grant D2
remote approval or start Phase E. Suggested Conventional Commit, not executed:
`test(ci): isolate integration validator subprocess environment`.
