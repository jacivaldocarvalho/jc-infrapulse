# HTTP Monitoring — Sprint 03

## Running and configuring

The API and monitoring worker are separate processes. The API does not start
periodic tasks during its lifespan. Run migrations once before starting both:

```bash
docker compose --env-file backend/.env build api worker
docker compose --env-file backend/.env up -d --wait db
docker compose --env-file backend/.env run --rm --no-deps api alembic upgrade head
docker compose --env-file backend/.env up -d --wait api worker
docker compose --env-file backend/.env logs -f worker
```

Locally, from `backend/`, use `.venv/bin/python -m app.worker`. To stop it,
send SIGTERM/SIGINT or run `docker compose --env-file backend/.env stop worker`.
Do not run more than one worker instance, including during deployments.

| Variable | Default | Effect |
| --- | --- | --- |
| `MONITORING_CONCURRENCY` | `10` | Maximum checks in progress; range 1–100 |
| `MONITORING_POLL_SECONDS` | `5` | Selection cycle; range 1–60 seconds |
| `MONITORING_CONNECT_TIMEOUT` | `3` | TCP connection and TLS handshake limit, per stage |
| `MONITORING_RESPONSE_MAX_BYTES` | `1048576` | Maximum response body bytes transferred |
| `MONITORING_SHUTDOWN_SECONDS` | `15` | Deadline for tasks to finish before cancellation |
| `MONITORING_ALLOWED_PORTS` | `[80,443]` | Allowed destination ports, JSON |
| `MONITORING_ALLOWED_HOSTS` | `[]` | Additional restriction to exact hosts; empty adds no restriction |
| `SERVICE_INTERNAL_HOSTS` | `[]` | Exact hosts eligible for the internal exception |
| `MONITORING_INTERNAL_NETWORKS` | `[]` | Private networks authorized for the internal exception |
| `MONITORING_HEARTBEAT_PATH` | `/tmp/infrapulse-worker-heartbeat` | Local health file |

Each service's `timeout_seconds` is the total deadline, including DNS, TCP, TLS,
headers, and body reading. The connection timeout is bounded by that deadline.
Closing sockets may add up to one second of cleanup. The body is streamed and
discarded, never persisted. A transport read may exceed the body limit by at
most one 64 KiB block before rejection; the process does not accumulate the
body. HTTP Core/h11's HTTP/1.1 parser limits headers. There is no automatic
decompression.

The worker runs as the backend image's unprivileged user, publishes no ports,
and joins both the internal database network and an HTTP egress bridge.
PostgreSQL remains on the internal network only, with no published port.

## Scheduling, backpressure, and restart

The interval is measured between starts, identified by `checked_at`. Only active
services are selected. Before running a task, the worker rereads its registry
entry, including activity, URL, timeout, and interval. Changes after that read
take effect on the next run; deactivating a service does not cancel a request
already in progress.

Selection happens once per cycle when capacity is available. It fetches only as
many services as fit the remaining capacity, prioritizing services without
history and then the oldest last check. An index supports finding the latest
result without aggregating the entire history. There is no unbounded queue.
Under saturation, checks are delayed; missed cycles are not all replayed.
Accuracy is limited by polling, check duration, and capacity.

The set of running tasks prevents overlap for each service. An in-memory
monotonic deadline prevents immediate repetition even if persistence fails.
The next selection can account for interval changes only after that deadline,
calculated with the previous attempt's configuration, expires. SQLAlchemy
sessions are created and closed in the threads responsible for each operation.
No session is shared and no transaction spans an HTTP connection.

After a restart, the latest persisted check restores the interval. Services
without history or past due become eligible immediately, subject to capacity.
Attempts interrupted before commit have no record and may be repeated. Database
failures are logged without sensitive details; there is no durable result queue
or exactly-once guarantee. A result for a service removed during the request is
discarded. Later deletion cascades to history.

SIGTERM/SIGINT stop new scheduling, wait for tasks within the grace deadline,
and cancel the remainder. Already started database/resolver threads cannot be
stopped by asyncio cancellation. PostgreSQL operations have timeouts; the system
resolver depends on its operational configuration. Compose allows 75 seconds
before forcibly stopping the container.

The scheduler cycle updates the heartbeat even when all capacity is occupied.
The health check validates the PID and file age; consecutive database selection
failures make the heartbeat stale. This measures process health, not destination
availability or successful persistence of every result. Compose reports
unhealthy status; that status alone does not automatically restart the process.

**Only one monitoring instance is supported.** Replicas require leases,
distributed locks, or a coordinated queue with idempotency; a local semaphore
does not coordinate separate processes.

## Results and metrics

| `status` | Meaning |
| --- | --- |
| `UP` | HTTP 2xx, response completed within limits |
| `DOWN` | Non-2xx HTTP response, including 3xx without following Location |
| `TIMEOUT` | Total or connection deadline exceeded |
| `DNS_ERROR` | Resolution failed or returned no addresses |
| `TLS_ERROR` | Handshake, certificate, or hostname failure |
| `CONNECTION_ERROR` | TCP failure, reset, or transport read/write error |
| `HTTP_ERROR` | Invalid protocol, excessive response, or policy-rejected destination |

Status gives the technical cause. For availability, only `UP` succeeds;
`down_checks` counts every result other than `UP`, not just the `DOWN` enum.
A policy-blocked service receives no request and has an `HTTP_ERROR` result
with a generic message. Unexpected execution or persistence failures produce
logs without fabricating monitoring results.

`checked_at` is the UTC start of the attempt; `created_at` is assigned by the
database. `response_time_ms` measures elapsed execution to the result with a
monotonic clock, including DNS and failures; it is not just time to first byte.
Mean and p95 use all non-null durations in the period, including failed checks.
p95 uses continuous interpolation (`percentile_cont(0.95)` in PostgreSQL).

```text
uptime_percentage = up_checks / total_checks * 100
```

This measure is the share of successful checks, not time-weighted availability.
With no checks: zero counters and `null` availability, latencies, and latest
status for the period.

## REST contract

| GET endpoint | Response |
| --- | --- |
| `/api/v1/services/{id}/checks` | `items`, `total`, `limit`, `offset` |
| `/api/v1/services/{id}/metrics` | Counters, availability, mean, p95, latest result in the period |
| `/api/v1/services/{id}/status` | `service_id`, `last_status`, `last_checked_at`, without a time window |

History and metrics accept `start` and `end` in ISO 8601 with a time zone. Defaults
are now minus 24 hours and now. Supply both bounds for a historical period. The
window must be positive and no longer than 90 days, using
`start <= checked_at < end`. Responses are normalized to UTC.

History: `limit` from 1 to 100, default 20; `offset` from 0 to 10000, default
zero. Ordering is `checked_at DESC, id DESC`, and `total` covers the full window.
Results persisted between requests may shift offset pages. Aggregate queries
and the latest result use separate statements under READ COMMITTED; a concurrent
write may appear between them. There is no snapshot across requests.

A missing service returns 404; invalid UUIDs or parameters return 422. An
existing service without history returns empty data with HTTP 200. Inactive
services retain queryable data.

```bash
curl 'http://localhost:8000/api/v1/services/UUID/checks?limit=20&offset=0'
curl 'http://localhost:8000/api/v1/services/UUID/metrics?start=2026-10-01T00:00:00Z&end=2026-10-02T00:00:00Z'
curl 'http://localhost:8000/api/v1/services/UUID/status'
```

## SSRF threat model

Untrusted inputs include registered URLs and DNS/HTTP responses. An attacker
could register a public domain resolving to an internal network, change DNS
responses, offer redirects to metadata, or send slow or large responses.

Defenses:

1. Revalidate the URL from the database: HTTP/HTTPS only, without credentials,
   and within operator-configured ports and hosts.
2. Resolve once per attempt and validate every returned IPv4/IPv6 address.
   A mixed response containing any forbidden IP rejects the entire destination.
3. Block private addresses without an explicit exception; always block loopback,
   link-local, multicast, reserved, unspecified, known metadata, and known
   mapped IPv6/6to4/Teredo/NAT64 addresses.
4. Connect a nonblocking socket to the approved numeric IP using a custom
   backend. The transport does not call DNS. Host and SNI use the original
   hostname; certificates and hostname are verified normally.
5. Create one connection per attempt, without retries, redirects, environment
   proxies, credentials, cookies, or reuse across services/checks.
6. Limit time, concurrency, and bytes; discard the body. Logs contain IDs,
   status, and durations. Persisted messages are fixed; monitoring does not
   log exceptions, URLs, queries, headers, or bodies. Internal HTTP Core debug
   logs are suppressed to avoid leaking targets.

Both authorizations are required to allow an internal destination:

```dotenv
SERVICE_INTERNAL_HOSTS=["api.internal"]
MONITORING_INTERNAL_NETWORKS=["10.20.30.0/24"]
```

The hostname must match exactly, and every resolved private IP must belong to
the authorized network. Loopback and metadata remain forbidden even with
exceptions. Do not use broad networks to bypass the policy. Local socket tests
inject a controlled backend; this substitution is not available through
environment variables or the API contract.

Model limits: the application and operator/environment are trusted. Public IPs
may act as proxies or be specially routed by infrastructure. Custom IPv6
translation prefixes and provider-specific metadata services require additional
network restrictions. The application does not control routes, NAT, system DNS,
or host network configuration. Use egress policies to reinforce restrictions
in real environments. URLs remain stored in the registry; avoid putting secrets
in URLs. Authentication/authorization remain outside the MVP.

## Validation

Tests do not call public services. Mocks cover classification, DNS policy, and
rebinding; controlled local servers verify the actual socket, Host, SNI,
certificates, absence of a second resolution, and absence of proxies. OpenSSL
generates temporary certificates that are not versioned; the Docker `test` target
explicitly declares this dependency, while the runtime target is unchanged.

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pip check
```

For PostgreSQL, use the README sequence with `compose.test.yaml`. The database
has an exclusive internal network, an ephemeral password, tmpfs, and no published
port. The suite checks full and incremental upgrade/downgrade
`0001 → 0002 → 0001 → 0002`, registry preservation, constraints, cascade,
PostgreSQL p95, and a worker in a real process. This sprint has no automatic
retention: monitor history growth.

Sprint 04 adds operational telemetry without changing these monitoring
guarantees. See [observability.md](observability.md).
