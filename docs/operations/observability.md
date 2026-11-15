# Observability — Sprint 04

## Architecture and boundaries

The API and worker have separate Prometheus registries, internal listeners, and
OpenTelemetry providers. Prometheus scrapes `api:9000/metrics` and
`worker:9001/metrics` every 15s. Each process exports spans over OTLP HTTP to
`otel-collector:4318/v1/traces`; the Collector applies a memory limiter and
batching, then forwards over OTLP gRPC to `tempo:4317`. Grafana queries
Prometheus and Tempo. Prometheus sends alerts to Alertmanager. There is no
external notification integration.

The API still has no scheduler. The worker remains limited to one instance.
No migration was added. Per-service history remains in PostgreSQL through the
Sprint 02–03 APIs; it is not reconstructed from Prometheus.

## Local operation

From the repository root, copy `backend/.env.example` to `backend/.env` if it does
not already exist. Set distinct, locally generated passwords in
`POSTGRES_PASSWORD` and `GRAFANA_ADMIN_PASSWORD`. Do not commit the `.env` file.

```bash
docker compose --env-file backend/.env config --quiet
docker compose --env-file backend/.env build api worker
docker compose --env-file backend/.env up -d --wait db
docker compose --env-file backend/.env run --rm --no-deps api alembic upgrade head
docker compose --env-file backend/.env up -d --wait
python3 scripts/verify-observability-ports.py jc-infrapulse --allow-local-interfaces
```

The last command assumes the default project name `jc-infrapulse`. If you use
`-p`, replace the argument with your chosen name. Grafana:
`http://localhost:3000`, user `GRAFANA_ADMIN_USER` (default `admin`), and the
configured password. Changing the environment password does not automatically
reset an administrator already persisted in the volume.

Compose enables metrics and traces and sets internal bind to `0.0.0.0`. Outside
Compose, both are disabled by default. For Python development, explicitly set
`METRICS_ENABLED=true`, `TELEMETRY_ENABLED=true`, and an accessible Collector.
The default local bind is `127.0.0.1`; never expose operational interfaces on
untrusted networks. `/metrics` is not a functional API route on port 8000.

```bash
docker compose --env-file backend/.env logs -f api worker otel-collector
# Preserva volumes e dados:
docker compose --env-file backend/.env down
```

## Networks, ports, and resources

| Service | Internal port | Default publication | Networks |
|---|---|---|---|
| API | 8000 and metrics 9000 | Only `127.0.0.1:8000` | internal, api_access, telemetry |
| Worker | metrics 9001 | None | internal, monitoring_access, telemetry |
| PostgreSQL | 5432 | None | internal |
| Collector | OTLP HTTP 4318; health 13133 | None | telemetry |
| Tempo | HTTP 3200; OTLP gRPC 4317 | None | telemetry |
| Prometheus | 9090 | None | telemetry |
| Alertmanager | 9093 | None | telemetry |
| Grafana | 3000 | Only `127.0.0.1:3000` | telemetry, grafana_access |

`internal` and `telemetry` are internal Docker networks. `monitoring_access`
retains the checker's existing egress. PostgreSQL does not join `telemetry`.
Ports declared in image `EXPOSE` metadata are not published ports. The inspection
script verifies actual bindings and the database network without reading environment variables.

Collector is limited to 256 MiB (memory limiter at 192 MiB, 32 MiB spike);
Tempo and Prometheus to 512 MiB; Grafana to 384 MiB; Alertmanager to 128 MiB.
Observability services also have Compose CPU limits. Health checks verify
Prometheus, Alertmanager, and Grafana readiness. Collector provides `/` on 13133;
Tempo provides `/ready` on 3200. The selected images do not include a generic
HTTP tool for Docker health checks on those two services; integration checks
their endpoints over the internal network.

## Backend configuration

| Variable | Local default | Meaning |
|---|---|---|
| METRICS_ENABLED | false | Starts the process listener |
| METRICS_HOST | 127.0.0.1 | Bind; Compose forces 0.0.0.0 |
| API_METRICS_PORT | 9000 | API port; 0 allows an ephemeral test port |
| WORKER_METRICS_PORT | 9001 | Worker port; 0 allows an ephemeral test port |
| TELEMETRY_ENABLED | false | Enables the OTLP exporter |
| TELEMETRY_ENDPOINT | http://otel-collector:4318/v1/traces | Trace endpoint, including path |
| TELEMETRY_SAMPLE_RATIO | 0.1 | Local probability, range 0–1 |
| TELEMETRY_TIMEOUT_SECONDS | 1 | Per-export deadline, maximum 2s |
| DEPLOYMENT_ENVIRONMENT | development | Environment attribute in resources |

Different ports require updating Prometheus targets as well. Service, version,
and environment are trace resources. `service.name` is `infrapulse-api` or
`infrapulse-worker`; the version comes from package metadata. Both
`deployment.environment` and `deployment.environment.name` are provided for
compatibility. Resources are not automatically enriched with environment,
process commands, credentials, or destinations.

## Metrics catalog

| Metric | Type / unit | Labels | Meaning |
|---|---|---|---|
| http_server_requests_total | counter / requests | method, route, status_code | One observation per functional request |
| http_server_request_duration_seconds | histogram / s | method, route, status_code | Monotonic time to ASGI completion/error |
| infrapulse_checks_total | counter / checks | status | Checker returned a result, before persistence |
| infrapulse_check_duration_seconds | histogram / s | status | `response_time_ms / 1000`, includes DNS and failures |
| infrapulse_checks_in_flight | gauge / tasks | none | Occupied capacity, including database and persistence |
| infrapulse_worker_capacity | gauge / tasks | none | Configured concurrency limit |
| infrapulse_scheduler_cycles_total | counter / cycles | outcome | success, saturated, error, cancelled |
| infrapulse_scheduler_errors_total | counter / errors | stage | selection, target, checker, persist, heartbeat |
| infrapulse_scheduler_cycle_duration_seconds | histogram / s | none | Monotonic cycle duration, including failures |
| infrapulse_scheduler_last_healthy_cycle_timestamp_seconds | gauge / epoch UTC s | none | Last cycle without internal failure; includes saturation and file heartbeat |
| infrapulse_last_up_check_timestamp_seconds | gauge / epoch UTC s | none | Time this worker confirmed an UP commit |
| infrapulse_check_results_persisted_total | counter / results | status | Commit confirmed by the store |
| infrapulse_check_results_discarded_total | counter / results | none | Service deleted before persistence |
| infrapulse_check_tasks_cancelled_total | counter / tasks | none | Cancellations acknowledged by execution |
| infrapulse_active_services | gauge / services | none | Count sampled each cycle; -1 before the first read |
| infrapulse_event_loop_last_tick_timestamp_seconds | gauge / epoch UTC s | none | Updated every second by the event loop |
| infrapulse_process_start_timestamp_seconds | gauge / epoch UTC s | none | Process telemetry initialization |

`status` has only the seven existing states: UP, DOWN, TIMEOUT, DNS_ERROR,
TLS_ERROR, CONNECTION_ERROR, HTTP_ERROR. Prometheus adds `job` and `instance`
labels; they do not identify the monitored service. Histograms generate `le`,
`_bucket`, `_sum`, and `_count`; the client also generates `_created` timestamps
for counters and histograms. There are no URL, IP, UUID, client, or textual-error labels.

The registries share a catalog; query worker metrics with
`job="infrapulse-worker"` and RED with `job="infrapulse-api"`. Unlabeled series
start at zero, and timestamps with no event stay zero. Restarts reset local
metrics; use `rate`/`increase` for counters. The full history is not queried
to reconstruct them. Only `UP` represents target success.

Buckets for every histogram, in seconds:
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, +Inf`.

The RED route is the resolved template, such as `/api/v1/services/{service_id}`.
Unknown routes use `unmatched`; nonstandard methods use `OTHER`. `/health`,
`/ready`, and `/metrics` are excluded from RED, spans, and sanitized access logs,
including query strings on those routes. Per-service REST metrics remain
instrumented like any functional route, using its template. Failures before a
response count as 500; after streaming begins, the actual sent status is counted.
Exceptions do not create fictitious DOWN results.

## Health, lifecycle, and privacy

`up=1` shows that the exporter responded, not that the event loop, database, or
targets are healthy. Compare tick age, healthy-cycle age, and `/ready`. The
Sprint 03 file heartbeat remains: successful cycles update it, and it remains
the source for the worker's local health check.

Listeners run on their own threads. SIGTERM/SIGINT stop scheduling; the scheduler
retains its `MONITORING_SHUTDOWN_SECONDS` deadline for tasks and cancels the rest.
Gauges are decremented in `finally`. Then the asyncio pulse, listener, and
exporter stop, the engine is disposed, and the file is removed. Compose retains
a 75s stop grace. The exporter adds approximately 4s (2s to drain and up to 2s
for an in-progress export), plus listener shutdown.

The trace queue holds at most 256 spans, in batches of up to 64. When full, it
drops the oldest. The owned processor enforces a shutdown deadline: the installed
SDK implementation does not apply the requested force_flush timeout. Exports run
outside the event loop with a bounded network timeout. Spans not delivered by
the deadline are dropped; an unavailable Collector does not block startup,
readiness, or checks. Trace delivery is not guaranteed.

Instrumentation is explicit, without HTTP/SQLAlchemy monkey patching. Spans
contain only fixed names, route templates, method, HTTP status, or `check.status`.
Database operations are session/selection/read/persistence spans, without SQL,
parameters, DSN, or exception messages. Headers, bodies, URLs, query strings,
resolved IPs, and monitored service names are not captured.

External context accepts only `traceparent` as a link to a new local trace.
`tracestate` and baggage are discarded. Client sampling decisions do not control
local sampling. Internal context propagates through asyncio and `to_thread`.
No telemetry header is injected into monitored destinations.

JSON logs include trace_id/span_id when valid context exists, including unsampled
spans. Raw Uvicorn access logs are suppressed; the application emits sanitized
method/route template/status. Internal exporter and HTTP client logs are suppressed
to avoid exception messages containing destinations or secrets. Checker logs
retain run_id/service_id without placing them in labels or spans.

## Dashboards and queries

Grafana provisions `prometheus` and `tempo` data sources and dashboards:

- InfraPulse API: rate, 5xx ratio, p50/p95/p99, routes, exporter, and loop age.
- InfraPulse Worker: throughput, states, p95, tasks, errors, and cycle/loop health.
- InfraPulse Reliability: aggregate sample-based UP share, persisted results,
  technical failures, and active-service count.

Without traffic, ratios and quantiles may show no data and should not be read as
zero. Reliability does not provide historical availability per service. That
data remains in PostgreSQL-backed REST APIs. Traces can be searched in Explore,
Tempo data source, by `{resource.service.name = "infrapulse-worker"}`. Log trace_ids
support targeted investigation if the trace was sampled.

In Grafana 12.1, the Tempo plugin does not implement the generic backend health
endpoint. The test validates a real trace query through the data-source proxy,
as well as PromQL queries for every panel through the Prometheus proxy.

PromQL examples:

```promql
sum(rate(http_server_requests_total{job="infrapulse-api"}[5m]))
histogram_quantile(0.95, sum by(le)(rate(http_server_request_duration_seconds_bucket{job="infrapulse-api"}[5m])))
sum by(status)(rate(infrapulse_checks_total{job="infrapulse-worker"}[5m]))
time() - infrapulse_scheduler_last_healthy_cycle_timestamp_seconds{job="infrapulse-worker"}
```

## Alerts and notification configuration

Thresholds can be changed by editing `observability/prometheus/alerts.yaml` and
recreating or restarting Prometheus after validation with promtool. PromQL has
no implicit environment-variable interpolation. Initial rules:

| Alert | Condition | for |
|---|---|---|
| API/WorkerDown | target `up=0` | 2m |
| API/WorkerNoData | up series absent for 5m | 2m additional |
| EventLoopStalled | tick >60s, process >120s, scrape UP | 2m |
| SchedulerStalled | cycle >120s, process >120s, scrape UP | 2m |
| NoSuccessfulChecks | active services, recent checks, no persisted UP for 10m, process >10m | 2m |
| APIHighErrorRate | 5xx >5%, at least 100 requests in the 5m window | 5m |
| APIHighLatency | p95 >1s, at least 100 requests in 5m | 5m |
| SchedulerErrors | internal error observed in the last 5m | 2m |
| WorkerSaturated | occupancy >90% | 10m |

NoSuccessfulChecks does not necessarily mean the worker is defective: targets
may be DOWN or persistence may fail. Correlate states, internal errors, and
persisted results. It does not fire without active services or recent checks.
Latency and HTTP error alerts do not fire with insufficient traffic. Missing
series are separate from a failed scrape. Without Prometheus itself, rules are
not evaluated; external monitoring of the stack is future work.

Alertmanager groups by alertname/job, waits 30s, regroups every 5m, and repeats
notifications every 4h. Critical/warning severities have explicit routes;
critical inhibits warning for the same job/instance. The `local-only` receiver
sends no messages: alerts remain queryable in the internal API/UI. This is not
external delivery. For a real receiver, configure an integration in an unversioned
local file/secret, use the receiver's credential-file support, and mount the
secret read-only. Validate with `amtool check-config` before applying the
authorized configuration. Do not put tokens in versioned YAML.

## Retention, limits, and troubleshooting

- Prometheus: 15 days and a 2 GB TSDB limit, whichever comes first; persistent volume.
- Tempo: 72h block retention, with WAL and blocks on a local volume. Compaction is
  asynchronous; retention is not a hard disk limit. Monitor free space.
- Grafana and Alertmanager: volumes for local users/configuration and silences.
- PostgreSQL: Sprint 03 retention policy still has no automatic cleanup.
- No Loki or centralized log storage; use JSON runtime logs.
- One worker replica and one process per registry; multiprocess Uvicorn requires
  additional aggregation architecture. This stack is not highly available.
- Database/resolver threads are not stopped by asyncio cancellation, as in
  Sprint 03. A commit may finish after cancellation without being counted by
  the persistence counter. Metrics are operational, without an exactly-once guarantee.

If scraping fails, check `METRICS_HOST` bind, target ports, telemetry network,
and startup logs. If `up=1` but loop/cycle timestamps are old, investigate a
stall or database failure. If there are no traces, check sampling, endpoint with
`/v1/traces`, Collector on 13133, Tempo on `/ready`, and both components' logs.
The queue is volatile; restarting the Collector cannot recover spans already
dropped by the backend. If Grafana authentication fails after changing the
password, check the existing volume. Do not publish administrative interfaces
to bypass connectivity problems.

## Reproducible validation

```bash
bash scripts/validate-observability.sh
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pip check
```

The script creates a unique project, ephemeral credentials, and PostgreSQL 17
in tmpfs; validates config/rules and scenarios with promtool, Collector, and
Alertmanager; runs the stack, queries metrics/traces/dashboards/alerts, checks
log correlation, real bindings, Collector unavailability, recovery, and shutdown.
The full PostgreSQL suite remains in the separate D1 validator. The script
cleans only its project and volumes on completion. It does not reuse development
PostgreSQL.

Docker allocates the network without a fixed subnet. The validator authorizes
only the `monitoring-target` hostname, its actual `/32` IP, and port 8080. That
exception exists only in the test override. It does not read local `.env` or
publish ports. The two tests are selected by name without skips; the normal
test is repeated after Collector recovery. JUnit reports go to
`OBSERVABILITY_RESULTS_DIR` or a temporary directory reported by the script.
Run sequentially with Playwright when sharing the same Docker daemon.

References: [Collector](https://opentelemetry.io/docs/collector/configuration/),
[Prometheus rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/),
and [Tempo API](https://grafana.com/docs/tempo/latest/api_docs/).
