# ADR 0002 — Process-isolated operational telemetry

Status: accepted for Sprint 04, subject to the approved architecture and adjustments.

## Context

The independent API and worker need RED metrics, scheduler health, and traces without introducing per-service labels, exposing destinations, or changing the pinned transport from Sprint 03. An accessible exporter does not prove event-loop health. Telemetry dependencies must not become product availability dependencies.

## Decision

Use prometheus-client with a separate registry and HTTP listener for each process. Listener threads start and stop with the API lifespan and worker. Do not expose `/metrics` on the functional port. Publish only the API and Grafana on loopback; place Prometheus, Collector, Tempo, and Alertmanager on an internal network separate from the database.

Instrument ASGI, scheduler, checker, and persistence operations explicitly with the OpenTelemetry API/SDK. Do not enable automatic transport or SQL instrumentation. Record only route templates, normalized methods, status codes, and fixed operation names. Do not record exception messages, SQL, URLs, headers, or bodies. Do not inject tracing into monitored destinations. Resources identify role, version, and environment; they do not use automatic environment or process detectors.

Treat an external traceparent as a link to a local trace, without baggage/tracestate or client-controlled sampling. Propagate context internally through asyncio/to_thread. Emit sanitized access logs correlated with trace/span and suppress raw access, client, and exporter messages.

Separate metrics for completed results, confirmed commits, the latest persisted UP, the latest healthy cycle, and event-loop heartbeat. Preserve the heartbeat file, backpressure, concurrency, SSRF policy, and task wait deadline during shutdown.

Export through OTLP HTTP with a 256-span queue, batches of up to 64, and an owned processor with a drain limited to 2s plus one in-progress export (maximum timeout 2s). The installed SDK version implements force_flush without applying the supplied timeout; an explicitly owned processor avoids relying on that behavior during shutdown. Discard the backlog when the deadline expires and stop the thread.

The Collector applies a memory limiter before batching, uses bounded queueing and retries, and exports to Tempo. Persist Prometheus and Tempo on volumes, provision Grafana from files, and forward alerts to a local receiver without external notifications.

## Consequences

This maintains isolation and limited cardinality at the cost of manual instrumentation and responsibility for the export processor. Traces are best effort; a full queue, sampling, shutdown, or unavailable Collector may cause loss. No distributed trace is sent to monitored services. External links permit correlation without accepting the external context as a trusted parent.

Database spans bound operations and sessions without capturing individual SQL statements. Counting active services adds one query per cycle. Prometheus quantiles are bucket-based approximations; they are not the PostgreSQL history percentile_cont.

One worker instance and one process per registry remain limits. The local stack is not highly available and has neither automatic PostgreSQL retention nor centralized logs. Alertmanager receives and deduplicates alerts, but its default receiver does not notify people. Details are in [observability.md](../operations/observability.md).
