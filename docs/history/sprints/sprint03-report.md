# Sprint 03 — Delivery report

## Implementation

HTTP/HTTPS GET monitoring runs in a worker separate from the API. The checker
validates DNS and pins the socket to an authorized IP while preserving Host/SNI
and TLS validation. It uses no redirects, retries, environment proxies,
authentication, or cookies. It limits time, body size, and concurrency without
persisting response bodies or sensitive exception details.

The scheduler selects active services, prevents local overlap, and applies
backpressure without an unbounded queue. Synchronous SQLAlchemy operations run
in threads with their own sessions. The worker handles SIGTERM/SIGINT, waits for
tasks, and maintains a local heartbeat. Compose keeps PostgreSQL isolated with
no new published ports.

Alembic revision `0002` adds checks with a cascading foreign key and an index
on service/time/UUID. Revision `0001` was preserved. History, metrics, and status
are exposed through Pydantic endpoints; periods without data do not imply 100% uptime.

## Validation performed

| Check | Final result |
| --- | --- |
| `.venv/bin/pytest -q` in `backend/` | **157 passed, 11 skipped**; skips only for PostgreSQL |
| Full suite in `compose.test.yaml`, PostgreSQL 17 | **168 passed**, no skips |
| SSRF, DNS rebinding, and local HTTP/TLS transport | Passed in both suites |
| `.venv/bin/ruff check .` / equivalent from the root | Passed |
| `.venv/bin/ruff format --check .` / equivalent from the root | 45 formatted files |
| `.venv/bin/mypy` | No errors in 45 files |
| Local and test-image `python -m pip check` | No incompatible dependencies |
| `docker compose --env-file backend/.env config --quiet` | Passed |
| `compose.test.yaml` configuration with ephemeral password | Passed |
| API and worker Docker build | Passed |
| `git diff --check` | Passed |

The PostgreSQL suite performed full and incremental migrations, preserved a
service through `0001 → 0002 → 0001 → 0002`, ran `alembic check`, and checked
constraints, indexes, UTC, persistence across sessions, cascading, and p95 in
the real database. The process test started a real worker, persisted an HTTP
request to a controlled server, and verified its heartbeat and successful
SIGTERM shutdown. Scheduler tests checked concurrency, intervals, inactivity,
no overlap, completion during shutdown, and cancellation after the deadline.

Tests did not access third-party public services. Installation and builds used
dependency and image registries. The test database used a password generated
only in the process environment, an exclusive internal network, tmpfs data,
and no published port. That project's containers and network were removed
afterward. No migrations were run on the development database.

An attempt to update the local package without build isolation failed because
the environment lacked setuptools. Installation with isolation succeeded; the
final `pip check` passed. No validation failure remained pending.

## SSRF coverage

| Control | Automated evidence |
| --- | --- |
| HTTP/HTTPS, credentials, and ports | Rejected before DNS/connection |
| Loopback, private, link-local, and metadata | Direct URLs and forbidden DNS responses |
| IPv6 and transition | Mapped addresses, 6to4, and known NAT64 prefixes blocked |
| All DNS IPs | Mixed public/forbidden response rejected before transport |
| Rebinding | Forbidden second response does not alter the first attempt's connection; next attempt rejected |
| No second resolution | `getaddrinfo` forbidden during controlled real TCP/TLS connections |
| Effective IP | Socket peer matches the backend-configured address |
| Host and SNI | Controlled TLS server inspects received hostname and header |
| TLS | Trusted certificate succeeds; untrusted certificate and wrong hostname rejected |
| Proxies | HTTP/HTTPS/ALL_PROXY variables point to an invalid target without affecting the direct connection |
| Redirects | 302 to metadata returns DOWN without following Location |
| Cookies/credentials | Two requests send neither Cookie nor Authorization despite Set-Cookie |
| Response limit | Excessive Content-Length and chunked streaming rejected |
| Internal exception | Requires both an exact host and an authorized network |
| Sensitive data | Fixed messages and correlated logs exclude the test token |

## Files created

- `backend/alembic/versions/0002_create_check_results.py`
- `backend/app/api/routes/monitoring.py`
- `backend/app/db/check_repository.py`
- `backend/app/models/check_result.py`
- `backend/app/monitoring/__init__.py`
- `backend/app/monitoring/checker.py`
- `backend/app/monitoring/scheduler.py`
- `backend/app/monitoring/transport.py`
- `backend/app/schemas/monitoring.py`
- `backend/app/services/metrics.py`
- `backend/app/worker.py`
- `backend/tests/test_checker.py`
- `backend/tests/test_monitoring_api.py`
- `backend/tests/test_monitoring_postgres.py`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_transport.py`
- `docs/monitoring.md`
- `docs/adr/0001-http-monitoring.md`
- `docs/sprint03-report.md`

## Files modified

- `README.md`
- `backend/.env.example`
- `backend/Dockerfile`
- `backend/alembic/env.py`
- `backend/app/core/config.py`
- `backend/app/core/logging.py`
- `backend/app/core/url_policy.py`
- `backend/app/main.py`
- `backend/pyproject.toml`
- `backend/requirements.lock`
- `backend/tests/conftest.py`
- `backend/tests/test_postgres.py`
- `compose.test.yaml`
- `compose.yaml`

## Decisions and limits

Full decisions: [ADR 0001](../../adr/0001-http-monitoring.md).
Operations and threats: [monitoring.md](../../operations/monitoring.md).

- One worker replica; multiple replicas need distributed coordination.
- No durable result queue or exactly-once guarantee. Attempts without a commit
  may be lost or repeated after restart.
- No automatic history retention in this sprint.
- Polling and saturation may delay checks; interval changes may wait for the
  preceding attempt's in-memory deadline.
- asyncio cancellation does not stop already started DNS/DB threads. Database
  operations have timeouts; the resolver depends on system configuration.
- The socket pins the IP but cannot control routing/NAT, custom translation
  prefixes, or public intermediaries. Infrastructure egress complements SSRF policy.
- Mean/p95 include failed-attempt durations. Availability is the share of UP
  checks without time weighting. `down_checks` includes every failure.
- Full Prometheus/OpenTelemetry, authentication, and a dashboard are outside this delivery.

Work remained on `develop`. Entire stayed enabled; hooks and configuration were
not changed. No manual checkpoint, commit, or push was performed.

Suggested commit, to be created only through the project's authorized workflow:

```text
feat(monitoring): add SSRF-safe HTTP worker and reliability API
```
