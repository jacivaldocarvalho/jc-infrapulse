# Sprint 06 — Phase B: Backend CI

Date: 2026-10-30. Status: implementation ready for review and remote validation;
final approval pending a successful GitHub Actions run.

## Scope and files

- Created `.github/workflows/ci.yml`.
- Created this report, `docs/sprint06-phase-b-report.md`.
- No pre-existing file changed. No changes to the application, contracts,
  dependencies, migrations, Dockerfiles, Compose, or remote configuration.
- Frontend, PostgreSQL, Playwright, Docker scanning, and supply-chain security
  remain outside this implementation.

## Inspection and installation

Read `AGENTS.md`, `.entire/agent-guide.md`, `backend/pyproject.toml`,
`backend/requirements.lock`, documentation, scripts, and test/fixture sources.
Entire Brain was queried with `brief` and `recall`; it returned no additional
Phase A decisions specific to CI. Conclusions were checked against source.

Initial state: branch `develop`, HEAD
`709a4a76f6d57314426d7c92f9799d0c7cdaaaca`, clean working tree.

Installation follows the existing README guidance. In a clean checkout:

```bash
python3.12 -m venv /tmp/infrapulse-ci-venv
source /tmp/infrapulse-ci-venv/bin/activate
cd backend
python -m pip install -c requirements.lock -e '.[dev]'
```

The lock serves as constraints for declared and transitive dependencies; the
`dev` extra installs Ruff, mypy, pytest, and httpx. Editable installation avoids
build copies in the directory Ruff inspects. No new dependency was added.
All 49 locked versions matched installed package metadata.

Final validation ran on Ubuntu 24.04.5 LTS against a copy of versioned files
obtained with `git archive HEAD backend scripts`, without `.env`, using only the
new `/tmp/infrapulse-s06b-final/venv` environment. The existing `backend/.venv`
was queried for versions, with no installation or update in it.

| Tool | Local and isolated-environment version | CI configuration |
| --- | --- | --- |
| Python | 3.12.3 | 3.12, patch supplied by setup-python |
| Ruff | 0.17.3 | requirements.lock |
| mypy | 1.20.2 | requirements.lock |
| pytest | 9.2.0 | requirements.lock |

## Job structure

| Job / public name | Execution | Timeout |
| --- | --- | --- |
| `backend-quality` | Installation, Ruff, format, mypy, pip check, script Ruff | 15 min |
| `backend-unit` | Same installation, eight explicit modules, JUnit, upload | 15 min |
| `ci-required` | Aggregates only the two preceding jobs | 5 min |

All use `ubuntu-24.04`; both backend jobs use Python 3.12. Default command
working directory: `backend`. The `scripts/` checks and aggregator override it
to the root. The aggregator needs no checkout. Repeated checkout/setup/install
is limited to two independent runners; no helper action was added for this short
sequence.

Backend Ruff uses `backend/pyproject.toml`. mypy runs without arguments, honoring
existing `app`, `tests`, and `alembic` targets, strict mode, and the Pydantic
plugin. The four root Python scripts use current root Ruff defaults, with no
separate configuration. Backend rules were not imported for the scripts, and
scripts invoking Docker, seeding, or Playwright were not run. Existing mypy scope
does not include these scripts.

## Test selection

The workflow command names these modules; it does not rely only on skips or a
marker filter:

| Module | Passed cases in JUnit | Resources used |
| --- | --- | --- |
| `test_health.py` | 5 | ASGI and mocked session for readiness |
| `test_services.py` | 41 | ASGI and in-memory SQLite |
| `test_url_policy.py` | 42 | Local validation |
| `test_checker.py` | 45 | Controlled resolver and transport |
| `test_transport.py` | 5 | Loopback sockets, TLS, OpenSSL executable |
| `test_scheduler.py` | 6 | In-memory stores/checkers and temporary files |
| `test_monitoring_api.py` | 13 | ASGI and in-memory SQLite |
| `test_observability.py` | 27 | Controlled exporters, local listeners/processes |
| **Total** | **184** | **No PostgreSQL or external stack** |

Explicit exclusions:

- `test_postgres.py` and `test_monitoring_postgres.py`: `integration` marker,
  depend on `postgres_engine`, PostgreSQL, and migrations of the disposable project.
- `test_observability_stack.py`: depends on the real stack; uses a conditional
  skip based on `INFRAPULSE_OBSERVABILITY_INTEGRATION`, without the database
  `integration` marker.

`conftest.py` chooses SQLite in `registry_engine` when
`INFRAPULSE_RUN_INTEGRATION != 1`. The workflow pins that variable and
`INFRAPULSE_OBSERVABILITY_INTEGRATION` to `0`. Selected fixtures provide test
configuration; the clean-copy run proved no dependency on `.env`. No migration ran.

pytest runs directly in Bash with immediate failure and pipefail, without pipes
or wrappers that replace its exit code. Empty selection returns code 5;
nonexistent paths also fail. `--strict-markers` validates markers. JUnit is at
`backend/test-results/backend-unit.xml`; upload uses `always()`, seven-day
retention, and failure if the file is missing. An earlier pytest failure still
fails the job even if upload succeeds.

## Aggregator

`ci-required` uses `if: ${{ always() }}` at the job level and
`needs: [backend-quality, backend-unit]`. The step reads `toJSON(needs)` from an
environment variable and requires `success` for every dependency, with at least
one dependency. `failure`, `cancelled`, and `skipped` fail the gate.

This condition prevents automatic skipping of the gate when a dependency fails
or is canceled. Globally canceled runs remain subject to GitHub's scheduler;
completion of a canceled run is not promised. Gate logic never treats canceled
results as success.

Later phases may extend only the `needs` list, preserving the public name and
evaluation of all results. No future job was anticipated. Status and `always()`
semantics were reviewed in the
[official expressions documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions).

## Commands and evidence

Inspection: `git status --short`, `git branch --show-current`, `git rev-parse HEAD`,
`rg --files`, `rg -n`, `cat`/`sed` reads, `--version` commands,
`entire agent-help`, `entire brain brief ... --json`, and
`entire brain recall ... --json`. `entire agent-help brain` was unsupported;
the Brain queries documented in the guide worked.

SHAs were checked with `git ls-remote` against official tags for the three
repositories `actions/checkout`, `actions/setup-python`, and `actions/upload-artifact`.

Quality/test commands were extracted from YAML and run by
`python3 /tmp/infrapulse-s06b-validate.py` in the isolated copy. Full equivalents:

```bash
# Em backend/, com o novo ambiente isolado no PATH:
ruff check .
ruff format --check .
mypy
python -m pip check
python -m pytest -v -ra --strict-markers -m 'not integration' \
  tests/test_health.py \
  tests/test_services.py \
  tests/test_url_policy.py \
  tests/test_checker.py \
  tests/test_transport.py \
  tests/test_scheduler.py \
  tests/test_monitoring_api.py \
  tests/test_observability.py \
  --junitxml=test-results/backend-unit.xml

# Na raiz da cópia limpa:
ruff check scripts
ruff format --check scripts

# No repositório de trabalho:
git diff --check
```

| Final local validation | Result | Evidence |
| --- | --- | --- |
| Clean installation with constraints and `[dev]` | PASS | Editable installation completed; 49 versions checked |
| `ruff check .` | PASS | All checks passed |
| `ruff format --check .` | PASS | 52 files already formatted |
| `mypy` | PASS | No issues in 52 files |
| `python -m pip check` | PASS | No broken requirements found |
| Root script Ruff | PASS | Lint passed; four files already formatted |
| Selected suite | PASS | 184 passed in 9.60s; zero failures/errors/skips |
| JUnit | PASS | XML parsed; 184 cases, zero failures/errors/skips |
| Empty selection | PASS | Same command with a nonmatching filter returns 5 |
| YAML and structure | PASS | Existing PyYAML, assertions on triggers/permissions/jobs/needs/cache/SHAs |
| Shell | PASS | `bash -n` and ShellCheck on ten `run` blocks |
| Aggregator | PASS | Exact block run against 16 status combinations, plus empty needs and failed future dependency |
| `git diff --check` | PASS | No whitespace errors |
| Full actionlint validation | BLOCKED | Tool unavailable; not installed |
| Real GitHub Actions run | NOT RUN | Pending review and authorized publication |
| PostgreSQL, stack, frontend, Playwright, and scanners | NOT RUN | Outside Phase B |

Temporary local evidence, outside Git:

- `/tmp/infrapulse-s06b-final/evidence/summary.json`.
- `/tmp/infrapulse-s06b-final/evidence/*.log` and validated shell blocks.
- `/tmp/infrapulse-s06b-final/backend/test-results/backend-unit.xml`.
- `/tmp/infrapulse-s06b-final/backend/test-results/empty-selection.xml`.
- Local helper: `/tmp/infrapulse-s06b-validate.py`.

Preparation incidents: the sandbox failed before inspection ran, requiring
approved execution outside it. An initial attempt without an explicit path
interpreted `backend[dev]` as an index package; that package was uninstalled
from the temporary environment, and the attempt was discarded as validation.
A first helper version appended the negative filter after a line break and
returned 127; the helper was corrected and the final test returned 5. These
incidents changed neither the existing local environment nor the workflow
pytest command.

## Security, limits, and residual risks

- Triggers only for PRs targeting `develop`/`main` and pushes to those branches;
  no path filters that would omit the required gate.
- `permissions: contents: read`; both checkouts use `persist-credentials: false`.
- All actions pinned by full SHA with readable version comments: checkout v4.2.2,
  setup-python v5.6.0, upload-artifact v4.6.2.
- pip cache derives from `backend/pyproject.toml` and `backend/requirements.lock`.
- Workflow/ref concurrency cancels obsolete runs.
- No `pull_request_target`, secrets, package/image publication,
  `continue-on-error`, `|| true`, warning filters, or arbitrary coverage.
- JUnit is the only uploaded artifact; no `.env`, working directory, or
  credentials are uploaded. Result JSON reaches the gate through an environment variable.
- No code fix was needed for Ubuntu 24.04 compatibility.
- PyYAML, assertions, Bash, and ShellCheck do not replace GitHub validation of
  schema and expressions. Actual checkout, cache, Python download, upload,
  concurrency, and scheduling/cancellation still need remote exercise.
- The explicit list needs review when new test modules are added; it fails on
  empty selection but does not automatically include future modules.
- The existing lock does not pin the build backend to an exact version or package
  hashes; Python 3.12 receives patch updates and the runner image evolves.
  Installation follows existing policy without anticipating supply-chain changes.
- Local sockets and certificate generation depend on runner conditions and
  OpenSSL. They were exercised locally, not on a hosted runner.

## Git and recommendation

Branch and HEAD preserved: `develop`,
`709a4a76f6d57314426d7c92f9799d0c7cdaaaca`.
Only the two new files from this phase remain untracked for review.
No commit, push, manual checkpoint, Entire hook/configuration change,
migration, Docker command, container, volume, network, credential,
branch-protection, secret, or remote-configuration change.

Recommendation: ready for review and subsequent authorized remote validation.
Phase B should receive final approval only after the GitHub Actions workflow
succeeds. No later phase was started.

Suggested Conventional Commit, not executed:
`ci(backend): add quality and unit gates for sprint 06`.
