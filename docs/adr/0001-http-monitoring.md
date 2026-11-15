# ADR 0001 — Pinned HTTP transport and independent worker

Status: accepted for Sprint 03.

## Context

The registry validates URLs, but that validation does not prevent private DNS responses or DNS rebinding. Monitoring must also honor intervals without running duplicate schedulers in Uvicorn processes. The project already uses synchronous SQLAlchemy and psycopg with PostgreSQL and local SQLite tests.

## Decision

Use HTTP Core HTTP/1.1 with a custom network backend: resolve DNS, validate every address, and connect the socket directly to an authorized numeric IP. Preserve the original hostname for Host/SNI and TLS validation. Use one connection per attempt, without proxies, redirects, retries, authentication, or cookies. Declare HTTP Core as a direct dependency because its use is no longer merely transitive through HTTPX in tests.

Run one separate asyncio worker. Keep synchronous SQLAlchemy, isolating operations in threads with their own sessions rather than introducing a second persistence driver or model. Select only tasks that fit available capacity. Use the last persisted result and in-memory monotonic deadlines for intervals, without holding transactions open during HTTP requests.

Persist a textual enum with a CHECK constraint for test portability, UUIDs, TIMESTAMPTZ, and a composite index on service/time/UUID. Delete results in a cascade with the service. Compute aggregates in the database, using PostgreSQL continuous p95; SQLite uses the same interpolation by querying no more than two values.

## Consequences

Each attempt incurs DNS/TCP/TLS cost, a choice that simplifies the link between validation and connection. There is no automatic fallback to another IP for the same domain. An unavailable IP can cause failure even when other addresses are usable. Failures before commit can lose results; there is no durable queue. Concurrent registry changes are observed on the next read, without retroactively canceling a connection already started. History retention will be addressed later.

A single worker replica is an explicit operational constraint. Before replication, implement leases or distributed coordination and review idempotency. Infrastructure egress controls should reinforce the application's SSRF controls; special routing and public intermediaries lie outside the socket guarantee.

Operational details, status classification, and threats are documented in [monitoring.md](../operations/monitoring.md).
