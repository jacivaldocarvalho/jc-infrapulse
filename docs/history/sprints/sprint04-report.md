# Sprint 04 — Observability & Telemetry

Validation date: 2026-10-30. Branch: `develop`.

## Outcome

Implemented API RED metrics, worker/scheduler operational metrics, internal
listeners with their own lifecycle, event-loop heartbeat, explicit OpenTelemetry
tracing, and correlated/sanitized JSON logs. Added Collector, Prometheus, Tempo,
Grafana, and Alertmanager to Compose with versioned configuration. The worker
remains single-instance and the SSRF-safe transport was unchanged.

The approved timestamp was implemented as
`infrapulse_last_up_check_timestamp_seconds`, separate from
`infrapulse_scheduler_last_healthy_cycle_timestamp_seconds`. Completed results,
persisted results, discards, and internal errors have distinct metrics.

## Validation performed

Commands were run from the root except where `backend/` is specified:

| Command | Final observed result |
|---|---|
| `cd backend && .venv/bin/pytest -q` | 184 passed, 13 skipped, 10.29s |
| PostgreSQL 17 `compose.test.yaml` suite, via the script below | 195 passed, 2 skipped, 13.22s |
| `.venv/bin/ruff check .` in backend (Python script also checked) | Passed |
| `.venv/bin/ruff format --check .` in backend | 52 formatted files |
| `.venv/bin/mypy` in backend | No errors in 52 files |
| `.venv/bin/python -m pip check` in backend | No broken requirements found |
| `docker compose --env-file /tmp/infrapulse-sprint04.env config --quiet` | Passed; no secret printed |
| Test Compose configuration | Passed by the script |
| `bash -n scripts/validate-observability.sh` | Passed |
| `git diff --check` | Passed |
| `git diff --exit-code -- AGENTS.md .entire .codex` | No changes in these paths |

The 13 local skips are the 11 PostgreSQL cases and two cases requiring the
observability stack. In the PostgreSQL suite, only the two stack cases are
skipped; they are run separately under their actual conditions.

The final `bash scripts/validate-observability.sh` run used projects
`infrapulse-observability-137868` and `infrapulse-observability-137868-pg`:

- `promtool check config /etc/prometheus/prometheus.yaml`: passed.
- `promtool check rules /etc/prometheus/alerts.yaml`: 11 rules passed.
- `promtool test rules /tests/rules.test.yaml`: passed, nine scenarios.
- Collector `validate --config=/etc/otelcol/config.yaml`: passed.
- `amtool check-config /etc/alertmanager/config.yaml`: passed.
- Full migrations and regressions on PostgreSQL 17: passed.
- Stack test: 1 passed, 1 skipped in 51.88s (outage not yet activated).
- Test with Collector stopped: 1 passed, 1 deselected in 1.06s.

Versions used: Prometheus 3.5.0, Alertmanager 0.28.1, Collector contrib 0.128.0,
Tempo 2.8.2, Grafana 12.1.0, and OpenTelemetry Python SDK 1.45.2. The
PostgreSQL suite checks that the actual major version is 17.

## Scraping and PromQL evidence

Targets returned by Prometheus after actual startup:

```text
infrapulse-api    up    http://api:9000/metrics
infrapulse-worker up    http://worker:9001/metrics
```

Values observed in the instant integration query:

```text
sum(http_server_requests_total{job="infrapulse-api"}) > 0                              2
infrapulse_checks_total{job="infrapulse-worker",status="UP"} > 0                       1
infrapulse_check_results_persisted_total{job="infrapulse-worker",status="UP"} > 0       1
infrapulse_last_up_check_timestamp_seconds{job="infrapulse-worker"} > 0                1793330920.1478071
infrapulse_scheduler_last_healthy_cycle_timestamp_seconds{job="infrapulse-worker"} > 0 1793330921.1450098
```

The test service was registered, checked over real HTTP, and queried through
the historical API; its latest status was UP. The registration was deleted
after the proof.

## Tracing, Grafana, and alert evidence

A TraceQL search by resource.service.name found both processes; individual
Tempo queries returned HTTP 200:

```text
infrapulse-api    54ac1a6d2b081bc4761b7e4678b169
infrapulse-worker cc502397c32d4e9a6a7f10a033278dc3
```

The backend does not export directly to Tempo: it sends to the Collector, whose
pipeline forwards to Tempo. The individual query also confirmed the monitored
target hostname was absent from the returned trace.

Prometheus and Tempo data sources were found by their configured UIDs. Prometheus
answered the data-source health check; a real trace was queried through the
Tempo data-source proxy. The generic backend health endpoint of the Tempo plugin
returns `plugin.notImplemented` in this version and was not interpreted as
proof of unavailability.

The `InfraPulse API`, `InfraPulse Worker`, and `InfraPulse Reliability` dashboards
were retrieved through the Grafana API. All panel expressions ran successfully
through Grafana's Prometheus proxy. No browser visual inspection was done;
validation covered provisioning and real queries.

The `InfraPulseIntegrationProbe` alert, defined only in the test environment,
was evaluated by Prometheus and appeared as `active` in the Alertmanager API.
The local receiver sends no external notifications and no integration was created.

## Failures, shutdown, and security

With the Collector actually stopped, `/health` and `/ready` returned 200 and a
new controlled service received a persisted UP result from the worker. The
metrics endpoint remained available. The worker then stopped by SIGTERM;
`scheduler_stopped` and `worker_stopped` were observed, the latter at
03:29:21.236857 UTC.

Automated tests cover:

- Single RED count, templates, unknown 404, HTTP 500, and histograms.
- Every checker status, including a real timeout of controlled DNS.
- Completion/commit/discard counters and timestamps with separate semantics.
- Selection, checker, persistence, and file-heartbeat failures.
- Concurrency, saturation, decrement in finally, and cancellation.
- Listener startup/shutdown and binding failure with cleanup.
- Incomplete scrape during shutdown with no remaining thread.
- Bounded queue, deadline-limited force_flush, and exporter-thread shutdown.
- Unavailable Collector in unit tests and the real stack.
- Allowed attributes, absence of sensitive details, and trace/span correlation.
- External links without baggage/tracestate or remote sampling control.
- Readiness without request or database spans.
- Existing SSRF/DNS rebinding, pinned IP, Host/SNI, TLS, and proxy regressions.
- No traceparent, tracestate, or baggage in monitored HTTP requests.

Runtime inspection returned `published=[]` for all nine services in the
integration environment. PostgreSQL was exclusively on the `*_internal` network.
API/worker and Prometheus shared `*_telemetry`, proving actual scraping
connectivity without published ports.

The main Compose configuration was validated separately: only
`127.0.0.1:8000` (API) and `127.0.0.1:3000` (Grafana) have declared bindings;
9000, 9001, 4318, 3200, 9090, 9093, and 5432 are not published. The existing
development stack on port 8000 was preserved; the new stack was fully started
under an isolated project with host ports removed. Thus, runtime verification
concerns the isolated stack and main-profile verification concerns configuration,
without claiming the existing environment was restarted.

## Limitations and operations

- One worker instance and one process per registry; no HA or multiprocess setup.
- Traces are sampled and best effort; loss is possible with a full queue,
  unavailability, or an exhausted shutdown deadline.
- Database spans bound operations/sessions; they do not capture individual SQL.
- Metrics are aggregated and do not replace per-service PostgreSQL history.
- Database threads cannot be canceled: commits may finish after cancellation
  without confirmation in the counter, as documented in the architecture.
- Collector/Tempo have health endpoints queried by integration, without built-in
  Docker probes in the selected images.
- Local Tempo has asynchronous retention, without a hard disk cap; PostgreSQL
  still lacks automatic retention as in Sprint 03.
- No external receiver, centralized log storage, or visual UI test.

Disposable projects and volumes are removed by the script on completion.
Detailed execution logs remain at
`/tmp/infrapulse-sprint04-final-validation.log`, outside Git; the trace_ids above
are evidence from the disposable run and are unavailable after cleanup. The
operations guide and script allow the full proof to be reproduced.

## Documentation and suggested commit

Produced `docs/observability.md`, this report, and
`docs/adr/0002-observability-architecture.md`. README, example configuration,
and the Sprint 03 operations reference were updated.

Suggestion: `feat(observability): add operational metrics tracing and monitoring stack`

No commit, push, or manual checkpoint was performed. AGENTS.md, Entire, and hooks
were preserved. The file list follows.

## Files modified

- `README.md`
- `backend/.env.example`
- `backend/app/core/config.py`
- `backend/app/core/logging.py`
- `backend/app/db/session.py`
- `backend/app/main.py`
- `backend/app/monitoring/scheduler.py`
- `backend/app/worker.py`
- `backend/pyproject.toml`
- `backend/requirements.lock`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_transport.py`
- `compose.yaml`
- `docs/monitoring.md`

## Files created

- `backend/app/observability/__init__.py`
- `backend/app/observability/http.py`
- `backend/app/observability/metrics.py`
- `backend/app/observability/processor.py`
- `backend/app/observability/tracing.py`
- `backend/tests/test_observability.py`
- `backend/tests/test_observability_stack.py`
- `compose.observability.test.yaml`
- `docs/adr/0002-observability-architecture.md`
- `docs/observability.md`
- `docs/sprint04-report.md`
- `observability/alertmanager/config.yaml`
- `observability/collector/config.yaml`
- `observability/grafana/dashboards/api.json`
- `observability/grafana/dashboards/reliability.json`
- `observability/grafana/dashboards/worker.json`
- `observability/grafana/provisioning/dashboards/dashboards.yaml`
- `observability/grafana/provisioning/datasources/sources.yaml`
- `observability/prometheus/alerts.yaml`
- `observability/prometheus/prometheus.yaml`
- `observability/tempo/config.yaml`
- `observability/tests/alerts.yaml`
- `observability/tests/prometheus.yaml`
- `observability/tests/rules.test.yaml`
- `scripts/validate-observability.sh`
- `scripts/verify-observability-ports.py`
