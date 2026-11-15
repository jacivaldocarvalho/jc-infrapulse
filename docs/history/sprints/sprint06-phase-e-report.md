# Sprint 06 — Phase E: Docker builds and image validation

## Inspection before implementation

Baseline: `develop`, HEAD `fd275524a8690147d77382287225c70e78e93488`, clean
working tree. AGENTS.md, the Entire guide and Brain were consulted; current code
was the source of truth. The workflow, all Dockerfiles/dockerignores, four existing
Compose files, OpenAPI export, TypeScript generator, and runtime were inspected.

- API and worker use the runtime stage of `backend/Dockerfile`. It is built once,
  with distinct commands in smoke tests.
- Backend: `python:3.12-slim`, user `app`, declared port 8000, uvicorn CMD.
  The worker command and both backend health checks belong to Compose.
- Frontend: Python export from the checkout, Node `24.18.1-bookworm-slim`, npm ci,
  generated types and build, runtime `nginxinc/nginx-unprivileged:1.29-alpine`,
  user 101, port 8080, and `/healthz` health check.
- The frontend Dockerfile already uses OpenAPI from its own context. The new job
  must compare the build-stage schema with `api-contract-openapi` from the same
  run, without changing Dockerfiles or accepting locally generated types.
- Dockerignores exclude `.env`, local dependencies, reports, and generated types;
  explicit backend COPY limits content. Actual produced files and layers will
  also be checked, not only ignore patterns.

Risks/inconsistencies documented before the fix: mutable base tags; no image-level
HEALTHCHECK in backend (Compose has one); packaging tools and possible pip residue
in the backend image; legitimate need for a password in configuration and
PostgreSQL for readiness/heartbeat. This inspection found no vulnerability
justifying a Dockerfile change. Any material configuration found by dynamic
inspection would be proposed before changing a Dockerfile.

Strategy: local Buildx `--load` builds, without login, publication, or deployment;
a temporary frontend build stage to verify schema/types; two runtimes. Smoke
without network or database: API `/health` 200 and `/ready` 503; worker starts,
health check fails without heartbeat, and SIGTERM stops gracefully; Nginx checks
configuration, static files, and health. No migration will run. This does not
replace earlier real database and observability tests.

The state above was recorded before implementation; executed results follow.
No Dockerfile needed a change.

## Files and job

- Modified: `.github/workflows/ci.yml`.
- Created: `compose.images.test.yaml`, `scripts/validate-images.py`,
  `scripts/test_image_validation.py`, and this report.

`docker-build`: Ubuntu 24.04, Python 3.12, 35-minute job timeout, 28-minute
validation step, and three-minute always cleanup. Uses runner-provided Buildx
without installing arbitrary tools or changing the builder. Depends on
`api-contract`; download without an alternative run ID consumes only the current
run's `api-contract-openapi` artifact. No login, push, or deployment.

Existing actions were reused with full SHAs and version comments.
`permissions: contents: read`, checkout without persisted credentials, triggers,
and concurrency remain unchanged. The preceding nine jobs did not change.

## Builds and contract

Three targets are loaded locally with `docker buildx build --load
--platform linux/amd64`: backend runtime, temporary frontend build, and frontend
runtime. API and worker share the backend image; the worker is not built again.
Frontend stages reuse cache from the same context. The build stage contains
development tools by definition; it is never started and is removed afterward,
not treated as runtime.

Existing Dockerfiles remain the installation source: pip with `requirements.lock`,
npm ci with `package-lock.json`, OpenAPI export, and `npm run build` with type
generation. Inspection copies the schema and `generated.d.ts` from an unstarted
build-stage container. The initial implementation required nonempty types and
JSON schema equality; this was insufficient to validate type content and was
fixed in the later round below. Dockerignores exclude local types, node_modules,
`.venv`, `.env`, and reports. The root `.dockerignore` governs the root-context
frontend; `frontend/.dockerignore` does not replace it. Backend has its own
ignore. `.env.example` passes backend ignore but not its explicit COPYs; it was
not found in runtimes or layers.

Local validation used an exporter from the same checkout in a copy without
`.git`, `.env`, or local dependencies. In CI, api-contract supplies the file.
Local contract SHA-256:
`fd3c9ec866c776d3ae0e6c50234015bccc6a793e3ae39362d1013029aa9e1a39`.

## Inspection of resulting images

| Item | Shared backend | Frontend runtime |
| --- | --- | --- |
| OS/architecture | linux/amd64 | linux/amd64 |
| Declared user | app; nonzero UID proved in process | 101 |
| Entrypoint | none | /docker-entrypoint.sh |
| CMD | uvicorn app.main:app --host 0.0.0.0 --port 8000 | nginx -g daemon off; |
| Declared port | 8000/tcp | 8080/tcp |
| Image HEALTHCHECK | absent per Dockerfile; operational Compose has health checks | wget /healthz |
| Inspected layers | 12 | 12 |
| Regular files in final filesystem | 7.891 | 1.009 |
| Docker-reported size | 287.942.518 bytes | 83.283.684 bytes |

IDs observed before cleanup:

- Backend: `sha256:b8e830516a8961db25a549d25db71ce610151c4fac3bbba51a2979c61442800f`.
- Frontend: `sha256:de52f1bfc3e52d53ad05076521b5af3a8a256a55bd7de38b88341bfa66d53982`.

Configuration, history, and exported filesystem were checked, as was each original
layer, including content that might later have been deleted. TARs were streamed
without extracting host paths. Inspection rejects `.env` files, credential/Git/
local-dependency directories, backend tests in runtime, private-key/token patterns,
and world-writable application/configuration inodes. Symlink modes are not target
permissions. The implemented checks found no secrets. This is neither a universal
secret scanner nor the next phase's CVE/supply-chain analysis.

Inspected embedded variables: only PATH, locale/version/public GPG fingerprint
from the Python base, and Python/pip flags in backend; PATH and Nginx/NJS/package
versions in frontend. The report publishes variable names only. `GPG_KEY` is a
public base-image identifier, not a private credential. No password is embedded.

pytest, Ruff, mypy, and httpx are absent from backend runtime; `pip check` passed.
Python and base-image packaging tools remain. Frontend runtime has no Node,
Python, workspace, or node_modules. Found 42 build/egg-info residue files in
backend `/app`: a size-reduction opportunity, with no identified dev dependency
or credential. Future review proposal: remove only this residue after pip install
or consider a separate packaging stage; the Dockerfile was unchanged here.

Permissions: `/app` and `/app/app` root:root 0755; main.py root:root 0664.
Frontend static files root:root 0755/0644. The base retains `/etc/nginx` 101:0
0775, and copied nginx.conf is root:root 0664. This permits writing configuration
in a deliberately writable container running in group 0; operational Compose
already applies read_only, which smoke requires. Separate hardening proposal:
consider 0644 for configs and directory ownership after checking base-entrypoint
compatibility. No production permission was changed.

## Smoke tests and isolation

The new Compose file has no database, networks, published ports, persistent
volumes, or bind mounts. Every container uses network_mode none, a read-only
filesystem, cap_drop ALL, and no-new-privileges. Runtimes have only `/tmp` on
tmpfs. Nginx receives an api loopback alias to resolve its static upstream at
startup; no proxy/API E2E is exercised. There is no access to the runner's
private network.

Passwords are generated in memory by the existing lifecycle and masked in CI;
only the variable name is passed to Docker, without printing its value. Backend
uses 127.0.0.1:1, a one-second timeout, and explicitly disabled telemetry/metrics.
It does not read `.env`, access development PostgreSQL, or run migrations. D1/D2
continue to cover positive database/worker scenarios; deliberate database
absence is not presented as healthy readiness.

- API starts with the original CMD; `/health` returns 200/healthy.
- `/ready` must return 503/not_ready without a database.
- Worker uses the same runtime with `python -m app.worker`; startup is proved,
  health check must return exactly 1 without heartbeat; any other unexpected
  status fails. SIGTERM exits with code 0 and worker_stopped/scheduler_stopped
  logs. There is no generic error suppression.
- Nginx starts with original entrypoint/CMD; nginx -t passes; index.html and JS/CSS
  assets are served; Docker health check becomes healthy.
- API, worker, and Nginx exit with code 0. Inspection confirms no port,
  privileged container, or host bind mount/socket.

## Execution, tests, and evidence before the type-comparison fix

Local environment: Python 3.12, Buildx v0.38.0, Compose v5.6.0. Temporary checkout
`/tmp/infrapulse-s06e-j44xxe1h`, project `infrapulse-images-s06e-j44xxe1h`.
Absence of resources and tags with that name was proved before each run.

Main commands run (targets selected by validator):

```bash
backend/.venv/bin/python -B scripts/export-openapi.py > /tmp/current-openapi.json
python3 scripts/test_image_validation.py
python3 scripts/test_integration_support.py
python3 scripts/validate-images.py --schema /tmp/current-openapi.json --report /tmp/image-report.json
python3 scripts/validate-images.py --cleanup
backend/.venv/bin/ruff check --no-cache scripts
backend/.venv/bin/ruff format --check --no-cache scripts
actionlint -color .github/workflows/ci.yml
git diff --check
```

Actual schema/report paths were files in the temporary checkout's `evidence/`,
with explicit INFRAPULSE_TEST_PROJECT and RUNNER_TEMP. `actionlint` ran from the
existing binary at `/tmp/infrapulse-s06b-actionlint-feulusd_/`. ShellCheck
examined all workflow shell blocks with Actions expressions replaced by
placeholders, alongside actionlint syntax validation.

Two local failures during implementation were retained as evidence: cleanup
initially omitted the inspection profile; then a permissions gate treated
0777 symlinks as writable files. Both were fixed in the new validator without
changing existing code/Dockerfiles. The corrected fallback removed the leftover
container/tag from the first attempt; the second proved cleanup on failure too.
A final full run passed, including inspection and smoke. Eight focused tests
checked credentials, content crossing chunks, permissions, symlinks, public
certificates, and preservation of primary error 17 despite cleanup 23, plus
isolated success/failure cases.

| Final validation | Result |
| --- | --- |
| Dockerfiles: Buildx --check and real builds | PASS |
| Three Dockerignores and context without .env | PASS |
| Backend runtime, frontend build, frontend runtime | PASS — no publication |
| Frontend schema/type existence | Initial PASS — identical schema; full type comparison still missing |
| Configuration, rootfs, history, and 24 runtime layers | PASS |
| API, worker, Nginx, and static files | PASS — no database/network |
| Inspection/error unit gates | PASS — eight tests |
| Existing lifecycle and propagation | PASS — 22 tests |
| Container/tag/marker cleanup | PASS |
| Pre-existing containers, networks, and volumes | PASS — baseline preserved |
| ci-required | PASS — 1.048.576 combinations; only ten successes accepted |
| actionlint, ShellCheck, Ruff, and format | PASS |
| git diff --check, including new files | PASS |
| E run in GitHub Actions | NOT RUN — no authorized commit/push |
| Full Playwright/observability in this round | NOT RUN — unnecessary for this smoke |

Evidence in `evidence/`: `validation.log`, `report.json`, `openapi.json`, and
`before.json`. Earlier attempts: `first-validation.log`, `second-validation.log`,
and `recovery-cleanup.log`. The final report contains metadata/permissions and
the contract hash; it contains no passwords, environment values, TAR files, or
runtime files. The workflow publishes only that sanitized JSON as
`docker-image-validation`, with seven-day retention and error if absent. Default
upload requires success of preceding steps, including cleanup; it does not
publish failed-validation results.

## Cleanup, quality gate, and limitations

The D2 ownership mechanism limits down --volumes --remove-orphans to the
reserved project. The inspection profile is included in creation and cleanup,
but never in runtime startup. An extra marker reserves the three tags after
rejecting existing tags. Only tags with matching ownership labels are removed.
Secondary errors are logged without replacing a primary failure; without a
primary failure, cleanup errors still fail the job. There is no prune.

Final confirmation: zero project-labeled containers/networks/volumes; three
tags and both markers absent. IDs, StartedAt, RestartCount, and status of five
pre-existing containers, as well as networks and volumes, match baseline.
Build cache and base images remain; no global image removal.

ci-required retains its public name, always, and prior code, now with ten
dependencies. The exhaustive success/failure/cancelled/skipped matrix rejects
any docker-build failure/cancellation/skip, including a contract-failure skip.
The nine prior validation jobs were structurally compared with HEAD and are
identical. Only the aggregator needs list was extended.

Residual risks: mutable base tags, registry dependency for downloads, backend
packaging tools/residue, and base Nginx permissions described above. Builds are
not claimed bit-for-bit reproducible. Secret patterns do not cover every possible
format. Positive database health checks are not repeated here. Forced runner
loss may prevent finally/fallback cleanup. The hosted runner still needs to
prove Buildx --check/--load support and validate the full job. Inherited Node 20
action warnings were unchanged.

Git remains on develop at baseline, with only the five files of this phase
pending, no staging/commit/push. Entire enabled, one prior pending checkpoint
reported by status; no synchronization, repair, manual checkpoint, or
hook/configuration change. Local containers preserved.

Recommendation: ready for local review and subsequent authorization for versioning
and remote validation of ten gates plus aggregator. Do not consider E remotely
approved. Phase F was not started. Suggested commit, not executed:
`ci(docker): build inspect and smoke runtime images`.

## Local fix for OpenAPI type comparison

Defect documented before the fix: `validate` compared JSON schemas but required
only that `generated.d.ts` have nonzero length. A nonempty, divergent TypeScript
file was accepted. The prior review reproduced this by mocking Docker operations;
current types were not shown to be wrong, but an effective negative gate was missing.

Round baseline: develop, HEAD
`fd275524a8690147d77382287225c70e78e93488`; the five Phase E files remained
pending. The fix was authorized only for the validator, its tests, and this report.
Workflow, Compose, Dockerfiles, and Dockerignores were to be preserved.

Inspected generator: `frontend/scripts/generate-api.mjs`, command
`npm run generate:api` (also invoked by npm run build), using openapiTS with
JSON.parse and astToString, no extra options, and the existing header.
`openapi-typescript` is 7.13.0 in package.json and package-lock.json v3, with
recorded integrity. api-contract produces the run's schema; docker-build
downloads it by name without selecting another run ID.

Docker was available, but the three Phase E tags, including the build stage with
its pinned generator, had been removed by previously verified cleanup. Existing
frontend runtimes were Nginx, without generator/Node dependencies. No rebuild
was planned in this round; complete real validation would require a new
disposable project after authorization for revalidation/rebuild.

### Independent comparison implementation

The build-stage schema is still compared as JSON with the expected artifact.
Then `compare_types` reads bytes from the file actually copied out of the build
and calls `generate_reference`. That reference uses only the contract supplied
by --schema, downloaded from the current run. It does not read or copy the
build's generated.d.ts as input.

Generation runs in a new container from the frontend-build image already loaded
by the job; --pull never prevents implicitly fetching another image. The process
uses user node, network none, read-only filesystem, cap_drop ALL,
no-new-privileges, a 64-process limit, 512 MiB, and one CPU. Only /tmp is writable,
through a 64 MiB noexec/nosuid tmpfs. No published ports, bind mounts, Docker
socket, or forwarded host environment variables. A payload containing the
contract and expected hashes arrives over stdin, not command arguments or logs.

Inside the container, SHA-256 hashes of the generator and package-lock.json
are checked against current checkout, as is the installed openapi-typescript
version against the exact declared/locked version. The lock must have integrity
and package.json must declare the same exact version, without a range. The
existing script is copied into a new /tmp workspace and uses only npm-ci-installed
build-stage dependencies through a symlink. The child process receives only
OPENAPI_SCHEMA_FILE pointing to a copy of the expected contract. It runs the
same generate-api.mjs with identical options and header and writes a new file
in that workspace.

The validator requires every byte to match, without normalizing spaces, lines,
order, or header. Missing, empty, or whitespace-only types, an empty reference,
generation failure/timeout, and schema divergence fail. On success, the report
records types.built_sha256, types.reference_sha256, generator_version,
generator_source_sha256, and lockfile_sha256. Divergence produces a diagnosis
limited to hashes and a fixed message, with no file diff or content.

Reference-process stdout/stderr are captured. Failures print only status/timeout
and stream hashes; the original exception retains identity and numeric code,
with a sanitized cmd to avoid long tracebacks or output data. Bootstrap
preserves the child generator's numeric status; signals use 128 + signal.
Existing main/run_cli flow preserves primary failure even if cleanup also fails.
The container is --rm; project/service/oneoff labels also allow removal by
--remove-orphans cleanup after interruption.

### Red-green regressions and functional comparison

Before the fix, eight prior tests and nine new regressions ran: 17 methods,
seven failures and one error, including acceptance of divergent types and
different content of the same size. After the fix, all 17 passed. Four more
methods covered isolation, pinned version, diagnostics, status 17, and timeout;
the final suite has 21 tests.

| Required scenario | Result after fix |
| --- | --- |
| Correct schema and types | PASS — full equality and equal hashes |
| Correct schema, divergent types | PASS — divergence rejected |
| Empty or whitespace-only types | PASS — rejected |
| Missing types | PASS — rejected |
| Reference generation failure | PASS — code 17 preserved |
| Empty reference or timeout | PASS — failure without accidental success |
| Divergent schema | PASS — rejected before reference generation |
| Same size, different content | PASS — rejected |
| Synthetic credential in types/stdout/stderr | PASS — content absent from diagnosis |
| Primary failure 17 and cleanup 23 | PASS — primary preserved, secondary logged |
| Floating/inconsistent declared version | PASS — rejected before Docker execution |

The same JavaScript bootstrap was also functionally validated outside Docker in
a temporary workspace, using Node v24.18.1 and openapi-typescript 7.13.0 already
installed in the prior temporary copy; no global dependency was installed.
A local contract and existing generated file in that frontend copy were used
to test independent reference generation. This is not presented as comparison
against a Docker image in this round.

Result: 19.275 bytes in both files and matching SHA-256
`266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34` for
reference and compared file. A one-byte change without size change was rejected.
The real bootstrap also rejected incompatible source hash and installed version;
a synthetic generator exiting 17 preserved 17 without exposing sensitive
stderr. These tests used only new /tmp files and read existing dependencies,
without changing the original frontend copy.

Hashes verified for the reference strategy:

- Generator: `a29f3652005928fe95be121be7edc6d232741db21c213701057e190dc97620b3`.
- Lockfile: `bf4985cb153daee0f826e53223554511d3a155e42e32200467bfa92983eadc61`.

| Final validation of this fix | Result |
| --- | --- |
| Inspector/contract/reference-process tests | PASS — 21 tests |
| Existing lifecycle and propagation | PASS — 22 tests |
| Real generator and functional comparison outside Docker | PASS |
| Ruff check and format | PASS |
| actionlint and ShellCheck | PASS |
| git diff --check, including new files | PASS |
| ci-required | PASS — ten dependencies and six representative scenarios |
| Actual comparison against Docker frontend stage | NOT RUN — build image removed by prior cleanup |
| Docker rebuild and GitHub Actions | NOT RUN — no authorized execution/publication in this round |

Evidence: `/tmp/infrapulse-e-types-ipnfx1me/red.log`, `green.log`,
`final-tests.log`, `functional-node.json`, initial `before.json` manifest,
and Brain query `brain-status.json`. Aggregator matrix includes success, empty,
docker-build failure/cancelled/skipped, and contract failure with skipped
dependents; it does not replace the initial exhaustive matrix, whose code is
unchanged.

### Limitations, status, and next review

Docker revalidation plan: with renewed authorization, build existing targets in
an exclusive project, export/download the current contract, run validate-images
with new comparison, and verify hashes, smoke, and cleanup; after review and
authorization for versioning, run the eleven jobs in GitHub Actions. Do not
reuse another run's schema, types, or artifacts as reference. The reference
helper and its Docker cleanup still require proof in the real environment.

Only scripts/validate-images.py, scripts/test_image_validation.py, and this
report changed since the start of this fix. Hashes confirmed the Phase E
workflow and Compose unchanged; Dockerfiles, Dockerignores, application,
contracts, and SSRF were not modified. The original five pending files remained,
without staging, commit, or push, on expected branch and HEAD. Entire was
enabled, doctor reported no hook or stuck-session problem and one prior pending
checkpoint; no repair, forced sync, or manual checkpoint.

Strict comparison may reject future generator/lock changes due to real output
differences; hashes and version make the cause checkable without publishing
content. Other base-image, permission, and pattern-inspection risks remain
as documented above. Recommendation: resume local review and authorize Docker
revalidation before versioning. Phase E remains without remote approval;
Phase F was not started.

## Authorized Docker revalidation after the OpenAPI fix

This section records the real run after the fix, superseding the previous
round's Docker NOT RUN limitation. GitHub Actions remains **NOT RUN** for E.
Branch develop and HEAD `fd275524a8690147d77382287225c70e78e93488` were
preserved; only the five planned Phase E files were pending initially.
Workflow and Compose were checked by hash and not changed in this round.
Differences in the two validators and the D2-reused ownership lifecycle,
error propagation, and cleanup were also inspected.

### Environment, commands, and provenance

Run in clean temporary copy `/tmp/infrapulse-e-docker-l28po9cc`, without .git,
.env, or local dependencies. Exclusive project
`infrapulse-images-e-docker-l28po9cc`, with RUNNER_TEMP pointing to runtime/
in that copy. Before resource creation, conflicts involving containers,
networks, volumes, markers, and three owned tags were rejected. Snapshots of
five containers, seven networks, four volumes, and 105 pre-existing image/tag
entries were saved.

The exporter reran with installed backend Python, reading sources copied from
current checkout. Backend, generator, locks, Dockerfiles, Dockerignores, and
exporter matched HEAD; Phase E changes did not alter contracts. No prior run's
artifact/schema was used. Contract SHA-256:
`fd3c9ec866c776d3ae0e6c50234015bccc6a793e3ae39362d1013029aa9e1a39`.

Validator command with parameters actually used:

```bash
INFRAPULSE_TEST_PROJECT=infrapulse-images-e-docker-l28po9cc \
RUNNER_TEMP=/tmp/infrapulse-e-docker-l28po9cc/runtime \
python3 evidence/run-validation.py \
  --schema /tmp/infrapulse-e-docker-l28po9cc/evidence/openapi.json \
  --report /tmp/infrapulse-e-docker-l28po9cc/evidence/report.json
```

The temporary runner in evidence/ loads the current validate-images.py and calls
its main through run_cli. It adds only observation and a negative comparison
case: the original function runs the positive case first, then compares a
temporary copy of the types with one changed byte. No Docker operations were
mocked, no validator file was changed, and simulated data did not replace the
reference. Unexpected errors still propagate; only the expected divergence
exception in the negative test is recognized as correct rejection.

Builds performed by the current flow, after Buildx --check on both Dockerfiles:

```bash
docker buildx build --load --platform linux/amd64 --progress plain \
  --target runtime --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:backend -f backend/Dockerfile backend
docker buildx build --load --platform linux/amd64 --progress plain \
  --target build --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:frontend-build -f frontend/Dockerfile .
docker buildx build --load --platform linux/amd64 --progress plain \
  --target runtime --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:frontend -f frontend/Dockerfile .
```

No login, push, destination registry, or deployment. Backend built once for API
and worker. Frontend stages reused cache; no no-cache build was requested, and
shared base images/cache were not removed.

### Images and real comparison

| Temporary local image | ID observed before removal | Result |
| --- | --- | --- |
| shared backend | sha256:767b56fb75eb842680c1f14d6dc3bba00f5b2de948c96f77f5076e46d7cc559e | PASS |
| frontend-build, inspection/generator only | sha256:cfdaea2b5dbeb4aea57f12099489063e88e24a342bc95be09588e3b06ea6f290 | PASS |
| frontend/Nginx runtime | sha256:550b91aefd9836c4343a0174ddcdec5a5813d83683a9e5b4ab76784441e2462d | PASS |

Both runtimes are linux/amd64, with app/101 users, expected CMDs/entrypoint,
and declared ports. Rootfs, history, permissions, embedded variables, and 24
runtime layers were inspected again. Backend still has 42 packaging residue
files, no installed pytest/Ruff/mypy/httpx, and a passing pip check. Frontend
contains only Nginx runtime/static files, without Node, Python, or workspace.

The actual generated.d.ts was copied from a container created from the
frontend-build target. The reference was generated twice in new Node containers
(positive and negative), from the expected contract over stdin, with
openapi-typescript 7.13.0 and documented source/lock hashes. The build file
was neither read nor copied as the reference. Image source and lock were
validated against checkout; generation used a new workspace as user node,
without network or host mounts, with read-only rootfs and limited tmpfs writes.

| Evidence | Result |
| --- | --- |
| Extracted generated.d.ts | 19.275 bytes |
| Build file SHA-256 | 266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34 |
| Independent reference SHA-256 | 266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34 |
| Byte-for-byte comparison | PASS — full equality |
| Same-size synthetic copy | REJECTED — one byte changed |
| Divergent copy SHA-256 | 49d6acf69160e3268630bae8894b34ee539f86b2aa67bbe1a3a88c6896db08e1 |

The original file remained unchanged after the negative case. The real comparator
raised ValueError with distinct hashes and a fixed message, without exposing
types. Final flow result: exit code 0 after positive equality, negative rejection,
inspections, smoke, and cleanup. Synthetic rejection is an expected test
condition; no unexpected failure was turned into success. Unit tests again
proved preservation of primary status 17 despite cleanup 23 and safe diagnostics.

### Smoke, cleanup, and complementary controls

| Check performed | Result |
| --- | --- |
| API /health | PASS — 200/healthy, non-root process |
| API /ready without database | PASS — exactly 503/not_ready |
| Worker startup | PASS — worker_started |
| Worker heartbeat without database | PASS — health check returns exactly 1 |
| Worker SIGTERM | PASS — worker_stopped and scheduler_stopped; exit code 0 |
| Nginx configuration | PASS — nginx -t |
| Frontend static files and health check | PASS — index/assets served and healthy |
| API/worker/frontend processes on exit | PASS — exit code 0 |
| Runtime permissions/isolation | PASS — read_only, network none, no privileges, ports, or binds |
| Real runtime/layer inspection and type comparison | PASS |
| 21 inspector tests | PASS — 0,059 s |
| 22 lifecycle/propagation tests | PASS — 2,369 s |
| Ruff check and format --check | PASS — nine formatted scripts |
| actionlint, ShellCheck, and git diff --check | PASS |
| Ten ci-required dependencies | PASS — six representative scenarios preserved |
| Cleanup and pre-existing resources | PASS |
| E GitHub Actions | NOT RUN — no versioning in this round |

Post-run queries confirmed cleanup: no project-labeled container, network, or
volume; both --rm reference containers gone; three owned tags and markers gone.
The five prior containers retained IDs, StartedAt, RestartCount, and status;
seven networks, four volumes, and all 105 earlier image/tag entries were
unchanged. Image/tag count returned to 105. There was no prune, mount of older
volumes, migration, firewall change, or local database access: runtimes had no
network and used a fictitious database on loopback:1 in their own namespaces.

Evidence in `/tmp/infrapulse-e-docker-l28po9cc/evidence/`:
`before-resources.json`, `after-resources.json`, `before-source.json`,
`openapi.json`, `validation.log`, `report.json`, `type-comparison.json`,
`inspector-tests.log`, `lifecycle-tests.log`, and the temporary observation runner.
Inspected sanitized JSON reports contained only hashes/metadata and variable
names, without environment values, type files, or credentials. Implemented
checks found no secret patterns; this is not a universal sensitive-data audit.
Buildx --check reported no warnings found for both Dockerfiles. Cached steps
do not represent a fresh, cache-free installation.

### Status and recommendation after revalidation

Only this report was updated in this round. A SHA-256 manifest confirmed that
the five executed files were current before this documentation update; the four
workflow/Compose/validator files matched the round's start. Branch and HEAD
preserved; no staging, commit, or push. Entire doctor found no hook failures or
stuck sessions, status enabled with one prior pending checkpoint; Brain was
read-only. No repair, forced sync, manual checkpoint, or configuration change.

Remaining limitations: hosted-runner behavior/support and the full E workflow
still need an actual GitHub Actions run; mutable base tags, backend residue,
and Nginx permissions described above remain. Build cache was preserved.
Pattern inspection is neither a CVE scanner nor a universal absence-of-secrets
guarantee. Phase F was not started.

Recommendation: local Docker revalidation satisfactory; ready for final
versioning review and renewed commit/push authorization. Do not consider
Phase E remotely approved until all ten validation jobs and ci-required pass.
