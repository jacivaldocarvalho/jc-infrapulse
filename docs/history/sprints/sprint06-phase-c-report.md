# Sprint 06 — Phase C: Frontend CI and OpenAPI contract

Date: 2026-10-30. Status: local implementation complete, awaiting review.
Phase C GitHub Actions: **NOT RUN**. No commit or push in this phase.

## Scope and initial state

Branch `develop`, initial and expected final HEAD
`bf315771bcf9d6c18a833bc0922aabe1114fac89`; working tree initially clean.
Read `AGENTS.md`, `.entire/agent-guide.md`, the workflow, frontend configuration,
lockfile, generation scripts, and regression tests. Entire Brain was queried
with `brief` and `recall`; the decision to generate types from the current
checkout was checked against source and existing documentation.

Files in this phase:

- Modified `.github/workflows/ci.yml`.
- Created `docs/sprint06-phase-c-report.md`.

Dependencies/locks, public contracts, business logic, worker, SSRF protection,
existing tests, migrations, Dockerfiles, and Compose were unchanged. The
`backend-quality` and `backend-unit` jobs remained structurally identical to
the baseline, verified by comparing YAML against `HEAD`.

## Jobs and dependencies

| Added job | Dependencies | Purpose |
| --- | --- | --- |
| `api-contract` | None | Install backend, export and validate OpenAPI, upload JSON |
| `frontend-quality` | `api-contract` | Install frontend, download contract, generate types, check quality |
| `frontend-tests` | None | Vitest/Testing Library/MSW and JUnit report |
| `frontend-build` | `api-contract` | Install frontend, download contract, generate types and build, check output |

All four jobs use `ubuntu-24.04` and a 15-minute timeout. `api-contract` uses
Python 3.12. The three frontend jobs use Node **24.18.1**, matching the Dockerfile
and compatible with `engines.node: >=24.15.0 <25` in package.json/lock.
Verified local environment: Python 3.12.3, Node 24.18.1, npm 11.16.0.

The global default `backend` working directory was preserved. Frontend jobs set
`defaults.run.working-directory: frontend`; export explicitly uses the root.
The public `ci-required` name remains stable, with a five-minute timeout.

## Contract and artifacts

1. Backend installation: `python -m pip install -c requirements.lock -e .` in
   `backend/`, without the development extra unnecessary for schema export.
2. `python -B scripts/export-openapi.py` exports `app.openapi()` from the current
   checkout without server, lifespan, SQL session, or database. It does not read
   `backend/.env` in this flow.
3. `fastapi.openapi.models.OpenAPI.model_validate_json` validates structure using
   an existing dependency. The gate also requires OpenAPI 3, nonempty paths,
   and nonempty component schemas. No other validator was installed.
4. `$RUNNER_TEMP/api-contract/openapi.json` is uploaded as the stable-name artifact
   **`api-contract-openapi`**, retained for seven days.
5. Quality and build download that name using the action's default context:
   the same repository and **same run**, without an explicit token or another
   run selection. The contract is not cached. A fresh checkout exports it again.
6. `OPENAPI_SCHEMA_FILE` is set in consumer steps, so the existing generator
   reads the shared file. A missing contract fails without falling back to an
   old schema or attempting to start the API.

The quality job runs `npm run generate:api`, `npm run typecheck`,
`npm run lint -- --max-warnings=0`, and `npm run format:check`.
The build uses `npm run build`, which already regenerates types, runs typecheck,
and executes Vite. It then requires nonempty `dist/index.html`, JavaScript, and
CSS in `dist/assets`. Build output stays on the runner: no dist upload,
publication, or deployment.

`frontend-tests` runs the existing command with native reporters:

```bash
npm test -- --reporter=default --reporter=junit --outputFile=test-results/frontend-unit.xml
```

Vitest's inclusion pattern remains `src/**/*.test.{ts,tsx}`. Playwright/E2E is
not run. Tests use jsdom, Testing Library, and MSW; type imports are erased during
transformation and need no previously generated file. Phase B pytest and
business scripts were unchanged.

JUnit is uploaded as **`frontend-unit-junit`**, with `always()`, seven-day
retention, and failure if the file is missing. Vitest is invoked directly by npm,
preserving failures. No option permits an empty suite or masks errors.

## Quality gate

`ci-required` requires exactly:

- `backend-quality`
- `backend-unit`
- `api-contract`
- `frontend-quality`
- `frontend-tests`
- `frontend-build`

The job retains `if: ${{ always() }}` and the approved Phase B code: it requires
`success` for every `needs` entry, with at least one entry. Failures,
cancellations, and skips never count as approval.

The exact Python aggregator block ran against **4.096 combinations** of
`success`, `failure`, `cancelled`, and `skipped` across six jobs: only the all-success
combination passed. The full shell block also ran in three scenarios with the
contract `failure`, `cancelled`, or `skipped` and `frontend-quality` and
`frontend-build` both `skipped`. All returned code 1.

These tests validate local logic. Scheduling and global cancellation remain
subject to GitHub; a canceled run is not claimed to always complete the
aggregator. This phase still needs to exercise its actual graph on the platform.

## Local validation performed

A clean copy was created with `git archive HEAD frontend backend scripts` at
`/tmp/infrapulse-s06c-clean`, without `.env`, node_modules, dist, or generated
types. New Python and node_modules environments stayed in that copy only.
Existing local environments and containers were unchanged.

New `run` steps were extracted from the workflow by a temporary helper and
executed with Bash, `-e`, and `pipefail`. Vitest ran **before** export and type
generation, proving independence from the contract and installed backend.

| Check | Result | Evidence |
| --- | --- | --- |
| Backend installation with declared dependencies only | PASS | Editable installation with constraints completed |
| `npm ci` in a clean checkout | PASS | 370 packages added; incidental audit reported zero vulnerabilities |
| OpenAPI export and validation | PASS | OpenAPI 3.1.0, 8 paths, 14 schemas, 12.528 bytes |
| Rejection of invalid/empty contract | PASS | Real validator errors in both cases |
| Rejection of missing contract file | PASS | Generator errors without fallback |
| `npm run generate:api` | PASS | Types generated from shared file |
| `npm run typecheck` | PASS | TypeScript 5.9.3 without errors |
| `npm run lint -- --max-warnings=0` | PASS | ESLint 10.13.0, zero warnings |
| `npm run format:check` | PASS | Prettier 3.9.10 accepted files |
| Vitest 5.0.5 | PASS | 26 tests in 10 files, in 4,27 s |
| Frontend JUnit | PASS | 26 cases; zero failures, errors, or skips; XML parsed |
| Deletion regressions | PASS | Five DeleteService.race and two deletion-state cases |
| `npm run build` | PASS | Vite 8.3.5; 1.689 modules transformed, build in 750 ms |
| Production output check | PASS | Nonempty index.html, JS, and CSS; 12 dist files |
| Aggregator | PASS | 4.096 combinations and three shell scenarios with dependent skips |
| YAML structure and security | PASS | Permissions, SHAs, caches, directories, needs, timeouts, and artifacts checked |
| Shell | PASS | `bash -n` and ShellCheck on all run blocks |
| actionlint 1.7.12 | PASS | No diagnostics in final workflow |
| `git diff --check`, including untracked report | PASS | No whitespace errors |
| Backend quality/unit in this phase | NOT RUN | Jobs unchanged; remote validation approved in Phase B baseline |
| Phase C GitHub Actions | NOT RUN | No authorized commit/push |
| Playwright, PostgreSQL, and observability stack | NOT RUN | Outside this implementation's scope |

No final local check remained FAIL or BLOCKED. Frontend configuration did not
need changes, and no new npm tools were installed. npm's notice that version
12.2.1 was available was recorded without updating npm.

Existing regressions covered: no status query after DELETE during slow
revalidation; cancellation of a pending query before DELETE; pausing polling or
manual refresh without interrupting other services; canceling the dialog;
DELETE failure with resumption and retry; navigation before revalidation with a
new visit receiving 404; cache/pagination/filter invariants and isolation
between QueryClients.

Temporary evidence, outside Git:

- `/tmp/infrapulse-s06c-clean/evidence/summary.json` and step logs.
- `/tmp/infrapulse-s06c-clean/evidence/deletion-regressions.json`.
- `/tmp/infrapulse-s06c-clean/frontend/test-results/frontend-unit.xml`.
- `/tmp/infrapulse-s06c-clean/runner-temp/api-contract/openapi.json`.
- `/tmp/infrapulse-s06c-clean/frontend/dist/`.
- Helper `/tmp/infrapulse-s06c-validate.py`.

During preparation, actionlint identified invalid use of `runner.temp` in the
job's env. The variable moved to step env, where that context is allowed; final
actionlint passed. An initial isolated installation used a constraints path
relative to the wrong directory; it was repeated correctly and completed.

## Security, actions, and residual risks

Global `contents: read` permissions, checkout without persisted credentials,
concurrency, triggers, and Phase B SHAs were preserved. Every new action use
has a full SHA, readable version, and job timeout. npm cache derives only from
`frontend/package-lock.json`; pip cache considers pyproject and requirements.lock.
There is no `pull_request_target`, production secret, broad suppression,
continue-on-error, or gate with `|| true`.

New actions, checked against official tags and manifests:

- [setup-node v7.1.0](https://github.com/actions/setup-node/tree/v7.1.0), SHA
  `949feb2413d6458794dcd2491c4babbbce0c15c1`, Node.js 24 runtime.
- [download-artifact v8.0.2](https://github.com/actions/download-artifact/tree/v8.0.2), SHA
  `9000827ccba6bdab643e8b6fd33ac0654aef8333`, Node.js 24 runtime; default consumption
  restricted to this run and default failure on digest mismatch.

The [approved Phase B run](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41048526460)
recorded Node.js 20 deprecation warnings for checkout v4.2.2, setup-python
v5.6.0, and upload-artifact v4.6.2, forced by the runner to Node.js 24 and
completed successfully. Their SHAs were not updated in this phase: no
compatibility need was observed. Maintaining those pins remains recommended in
a separate, authorized change; new jobs using them may repeat the warnings.

The JSON, JUnit, and 12 build files were inspected: no `.env`, symlinks,
token/private-key patterns, credential-bearing URLs, or backend password
configuration. Uploads point only to a JSON or XML file, without globs,
whole trees, or environment directories. JUnit includes test names and normal
runner metadata such as hostname, not the process environment. Pattern-based
inspection is not an exhaustive sensitive-data audit.

Pydantic validation and TypeScript generation do not replace a semantic API
compatibility test suite. Schemas still originate from the application. The
existing backend lock does not pin the build backend by exact version or hashes;
this policy was not expanded. npm uses the existing lock without resolving
arbitrary versions; its incidental audit does not replace a dedicated security phase.

Actual upload/download, caches, and the new job graph still need verification
in GitHub Actions. No local check in this report is equivalent to that success.

## Git, Entire, and recommendation

Branch `develop`, HEAD preserved at
`bf315771bcf9d6c18a833bc0922aabe1114fac89`. Local changes limited to the
workflow and this report, without staging, commit, or push. Entire remains
enabled; hooks/configuration were unchanged and no manual checkpoint was created.
At final inspection, Entire indicated a checkpoint pending synchronization:
`01M6694STZ5570RCCN602RFZ96`, the same ID recorded in the preceding Phase B
commit, without a reported synchronization error. No upload or repair was run.
No deployment, migration, Docker command, image publication, or remote change.

Recommendation: ready for local review and later authorization for versioning
and remote validation. Do not consider Phase C finally approved before a
successful GitHub Actions run. No later phase was started.

Suggested Conventional Commit, not executed:
`ci(frontend): add quality tests build and shared OpenAPI contract gates`.
