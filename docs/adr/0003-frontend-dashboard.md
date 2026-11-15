# ADR 0003 — SPA dashboard over existing REST contracts

Status: accepted for Sprint 05, subject to the approved architecture and adjustments.

## Context

The backend provides paginated CRUD, latest status, and per-service history and metrics. It has no global status summary, text search, or time-series endpoint. SSRF protection, PostgreSQL isolation, a single worker, and internal telemetry are invariants.

## Decision

Implement a React/TypeScript/Vite SPA with declarative React Router, TanStack Query, and Mantine. Use Mantine Form and Mantine Charts/Recharts to reduce independent visual integrations. Separate pages, features, and shared code; keep remote state in Query and filters and pagination in the URL. Load pages and charts on demand. Do not introduce a global state manager or authentication.

Generate types with openapi-typescript from `app.openapi()` in the current checkout, without a server, lifespan, or database. Use openapi-fetch with the browser origin and normalized errors, including the backend's distinct existing 422 formats. TypeScript does not validate responses at runtime; the backend remains the source of truth. Apply local form rules for immediate feedback.

Distinguish the global inventory from page-specific exclusive states, activity from result, query errors from DOWN results. Preserve null values and the backend definitions of availability and p95. Query full-window p95 separately from the 12 chart windows. Limit all metrics requests to four concurrent requests with a cancelable queue.

Poll only visible current data, pausing in the background. Use fixed historical windows, with no polling or focus-based refetch for those queries. Refresh the window explicitly and invalidate caches after mutations. Do not assume a snapshot across requests or absolute immutability of persisted historical data.

Serve the SPA with unprivileged Nginx and a proxy restricted to routes the product uses. Keep `127.0.0.1:8080` as the frontend's only binding and provide access only to the API's HTTP network. Do not add CORS or change the backend or its contracts. Use PostgreSQL in tmpfs and disposable projects for Playwright E2E, alongside Vitest/RTL/MSW and axe tests.

## Consequences

Mantine speeds up consistency and accessibility, with a dependency on its styling system and a need to verify actual contrast. Charts increase query volume; on-demand loading, caching, and the concurrency limit control that cost. No global status summary or filters absent from the API are promised. Before scaling, approve an aggregation or batching design and load budget.

The SPA does not depend on Collector availability or browser access to telemetry. The stack remains local, without authentication or public exposure. External publication will require a specific access and security decision.
