# Sprint 06 — Phase D1: PostgreSQL Integration CI

Date: 2026-10-30. Status: local implementation and validation complete; awaiting
review. This phase in GitHub Actions: **NOT RUN**. No commit or push.

## Inspection and scope

Branch `develop`, baseline and HEAD preserved at
`545ade7d92e632850b8b7ed9e35ed64ffcb8e486`; working tree initially clean.
Read `AGENTS.md`, `.entire/agent-guide.md`, the workflow, `compose.test.yaml`,
backend Dockerfile and dockerignore, fixtures, PostgreSQL/worker tests, migrations,
Alembic configuration, SSRF policy, and Sprint 03–05 commands.

Entire Brain was consulted. The brief returned the invariant to preserve SSRF
transport and PostgreSQL isolation, but reported an index behind HEAD. Conclusions
were verified in current code. No Entire refresh, repair, or configuration change.

Files in this implementation:

- Modified `.github/workflows/ci.yml`.
- Created `docs/sprint06-phase-d1-report.md`.

No change to Compose, Dockerfile, dependencies, migrations, application, worker,
SSRF policy, or existing tests. The six preceding jobs and aggregator code were
structurally compared with baseline and remained unchanged; the aggregator
received only the new dependency.

## backend-postgres job

Runner `ubuntu-24.04`; job timeout: 25 minutes. No job dependencies. Checkout
uses the same previously approved SHA and `persist-credentials: false`. Command
working directory is explicitly the checkout root.

Steps:

1. Use project `infrapulse-ci-postgres-${{ github.run_id }}-${{ github.run_attempt }}`.
   Validate the format and reject existing containers, networks, or volumes with
   that project label. Daemon query failures also stop preflight.
2. Generate a disposable password with `openssl rand -hex 32`; register GitHub's
   mask before exporting it through `GITHUB_ENV`. Do not use production secrets
   or print environment dumps/resolved configuration. Output `ready=true` enables
   cleanup only after successful preflight.
3. Validate the existing Compose file with `config --quiet`.
4. Build `tests` using the existing Dockerfile target; 15-minute timeout.
5. Run `up --abort-on-container-exit --exit-code-from tests`; five-minute timeout.
   The existing CMD runs pytest serially, without xdist/parallelism.
6. Cleanup with `always()` and ownership condition `ready == 'true'`, with a
   two-minute timeout, limited to the reserved file and project. Remove that
   project's containers, network, and any volumes. No global prune or removal.

Every Compose command includes `--env-file /dev/null`, `-f compose.test.yaml`,
and `-p "$POSTGRES_TEST_PROJECT"`. Thus, they do not use the main Compose file,
its `.env`, or its project name. `--exit-code-from tests` preserves pytest's
result; separate cleanup cannot turn an earlier failure into success. The test
command has no wrapper, pipe, `continue-on-error`, or `|| true`.

## Isolation and security

The existing `compose.test.yaml` is reused unchanged:

- `postgres:17-bookworm` image; the test queries the actual server version.
- Database and user `infrapulse_test`, internal host `test-db`.
- Data in tmpfs at `/var/lib/postgresql/data`; no persistent data volume.
- One `${projeto}_test` network with `internal: true`; no external/host networks.
- No published ports, including PostgreSQL 5432.
- No privileged containers; the test image runs as user `app`.
- Password only in the transient environment; no volume, database, or log uploads.

No service container was added. Firewall, Docker daemon, development networks,
and GitHub configuration were unchanged. Package downloads occur during the
existing build; suite execution uses the disposable internal network. Global
`contents: read` permissions, triggers, concurrency, and other preceding
workflow protections were preserved. No action or dependency was added.

The worker test authorizes only its own container IP `/32`, the controlled
server's ephemeral port, and the exact host. No broad runner-private-network
permission was configured. URL credential, DNS, IP, metadata, TLS, proxy, and
redirect policies remain unchanged and are exercised by the existing full
SSRF/TLS suite.

## Selection, Alembic, and worker

The job runs the full suite as in the historical README and Sprint 03–05 commands,
not only `-m integration`. This also exercises CRUD and monitoring against
PostgreSQL through `registry_engine`, alongside SSRF/TLS regressions.

Final result: **197 cases collected, 195 passed, and 2 skipped**.
The only skips are:

- `test_observability_stack.py::test_real_observability_stack`: needs the real stack.
- `test_observability_stack.py::test_collector_outage_real_processes`: needs the
  Collector to be stopped deliberately.

The 11 PostgreSQL-specific cases skipped in the local database-free suite ran
here. The observability stack was not enabled and D2 was not started.

Evidence from existing passing tests:

- `postgres_engine` requires `INFRAPULSE_RUN_INTEGRATION=1` and rejects a host,
  database, or user outside the dedicated tuple. It upgrades to head, downgrades
  to base, confirms table removal, upgrades to `0001`, inserts a sentinel,
  advances to head and proves preservation; it downgrades to `0001`, confirms
  removal of check_results, reapplies head, and runs `alembic command.check`.
- `test_migration_and_postgres_persistence` checks `SHOW server_version_num`
  with actual major version **17**, revision **0002**, defaults, UTC, and
  persistence across sessions. The observed local server was **PostgreSQL 17.11**.
- Monitoring tests check persistence, time zones, indexes, constraints,
  history, metrics, and cascade in real PostgreSQL.
- `test_standalone_worker_real_http_and_graceful_signal` starts `python -m
  app.worker` as a real subprocess, receives HTTP at the controlled server,
  confirms one persisted result, runs the heartbeat health check, sends SIGTERM,
  requires exit zero, worker_started/worker_stopped logs, and persisted UP status.

No migration or data query targeted the development database.

## Commands run and results

Initial read-only inventory: `docker version`, `docker compose version`,
`docker ps`, `docker network ls`, `docker volume ls`, and `docker inspect` with
specific fields, without `.Config.Env`. Available local tools: Docker 29.9.1,
Compose 5.6.0, actionlint 1.7.12, Bash/ShellCheck, and OpenSSL.

A clean copy was created with `git archive HEAD compose.test.yaml backend` at
`/tmp/infrapulse-s06d1-clean`. A temporary helper extracted workflow steps and
ran them with Bash `-e`/`pipefail`, in an empty project with a fresh password.
Equivalent commands:

```bash
# POSTGRES_TEST_PROJECT: nome exclusivo; senha gerada, nunca literal.
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" config --quiet
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" build tests
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" up --abort-on-container-exit --exit-code-from tests
# Somente após confirmar que o projeto é exclusivamente descartável:
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" down --volumes --remove-orphans --timeout 30
```

Final validation project: `infrapulse-ci-postgres-1793361568-317229`.

| Check | Result | Evidence |
| --- | --- | --- |
| Baseline, scope, and prior jobs | PASS | Expected HEAD, clean initial tree, prior jobs intact |
| Compose configuration | PASS | config --quiet and in-memory-only JSON inspection |
| Existing image build | PASS | Test target built without changing Dockerfile |
| PostgreSQL startup and health | PASS | Healthy dependency met; PostgreSQL 17.11 observed |
| Serial suite | PASS | 195 passed, 2 skipped in **13,40 s**, code 0 |
| Migrations/Alembic/major 17 | PASS | Full fixture and persistence/version test passed |
| Worker/HTTP/persistence/heartbeat/SIGTERM | PASS | Real-process test passed |
| SSRF/TLS | PASS | Existing cases run; policy unchanged |
| Actual isolation | PASS | No port bindings or privileged mode; own internal network; HostConfig.Tmpfs confirmed |
| Cleanup | PASS | Code 0; no remaining project container, network, or volume |
| Existing-environment preservation | PASS | Identical before/after snapshots for five containers, seven networks, four volumes |
| Preflight with existing resources | PASS | Simulation stops before enabling cleanup |
| Preflight with unavailable daemon | PASS | Simulation stops before enabling cleanup |
| Test failure code | PASS | Exact step preserves simulated code 17 |
| ci-required | PASS | 16.384 combinations, only seven successes accepted |
| actionlint, bash -n, and ShellCheck | PASS | Final workflow and all run blocks passed |
| git diff --check and untracked report | PASS | No whitespace errors |
| GitHub Actions for this phase | NOT RUN | No authorized commit/push |
| D2 / observability stack | NOT RUN | Outside scope; two expected skips |

The existing Dockerfile emits a pip-as-root warning during build; the test
process runs as app. PostgreSQL logs show constraint errors produced by negative
tests and a logical-replication-launcher shutdown message. They were not
suppressed; the suite exited zero.

The first run also passed 195 tests/2 skips, but the inspection helper failed
when looking for tmpfs in `.Mounts` after database shutdown. Cleanup ran and
preserved the existing environment. Only the temporary check was corrected to
`.HostConfig.Tmpfs`; the final run repeated the full suite and passed. No FAIL
or BLOCKED remains in the implemented workflow.

## Cleanup and aggregator evidence

Containers, network, and volumes were queried before creation. The project was
confirmed empty and distinct from `jc-infrapulse`. Before down with `--volumes`,
its two containers and one network were inspected to confirm project ownership
and absence of persistent/shared volumes.

After cleanup, project-label queries returned no containers, networks, or
volumes. Snapshots of pre-existing resources remained identical: container IDs,
names, StartedAt, RestartCount, status, and ports; network IDs and volume names.
Local frontend, API, and PostgreSQL were not restarted. Temporary password files
used by the helper were removed; local logs replace the ephemeral value with a
marker. No artifact was added to Git. Local test images and build cache remain
available; no image push, prune, or removal of pre-existing images/volumes.

`ci-required` retains its name and `always()` and now depends on seven jobs:
backend-quality, backend-unit, api-contract, frontend-quality, frontend-tests,
frontend-build, and backend-postgres. The exact aggregator block ran in
**16.384 combinations** of success/failure/cancelled/skipped: only all-success
passes, including backend-postgres. Successful cleanup does not mask an earlier
failed step or turn cancellation into success.

Local evidence outside Git:

- `/tmp/infrapulse-s06d1-clean/evidence/summary.json`.
- `/tmp/infrapulse-s06d1-clean/evidence/before.json` and `after.json`.
- `/tmp/infrapulse-s06d1-clean/evidence/runtime-isolation.json`.
- Step logs in `/tmp/infrapulse-s06d1-clean/evidence/*.log`.
- Temporary helper `/tmp/infrapulse-s06d1-validate.py`.

## Limitations, Git, Entire, and recommendation

This run was local, not on GitHub Actions. A globally or forcibly canceled runner
may prevent cleanup; `always()` covers the platform's normal flow, not runner
loss. Build/test/cleanup steps have timeouts to reserve time within the job's
overall timeout. Preflight is not a distributed lock; run ID and attempt yield
unique names for this job.

Existing PostgreSQL/Python/OS tags remain mutable, and no scanning or supply-chain
phase was anticipated. No action pin changed; inherited checkout may repeat
the Node.js 20 warning already documented in earlier phases. No JUnit or
PostgreSQL data upload was added; results remain in logs.

Branch `develop` and original HEAD preserved. Only the workflow and this report
changed, without staging, commit, or push. Entire remains enabled; the final
query indicated one pending checkpoint associated with the preceding Phase C
commit, with no reported synchronization error. No manual checkpoint, repair,
hook/configuration change, or synchronization occurred in this phase.

Recommendation: ready for local review and later authorization for versioning
and remote validation. Do not regard D1 as finally approved until the new job
and aggregator pass on GitHub Actions. D2 was not started.

Suggested Conventional Commit, not executed:
`ci(backend): add isolated PostgreSQL integration gate`.
