# Sprint 05 — Frontend Dashboard

Date: 2026-10-30. Branch: `develop`. Base: `7456565`.

## Delivery

Implemented a React/TypeScript/Vite SPA with React Router, TanStack Query,
Mantine Core/Form, and Mantine Charts/Recharts. The interface provides inventory,
service CRUD, current status, period metrics, paginated history, on-demand charts,
filters supported by the API, periodic refresh of current data, light/dark themes,
and a responsive layout.

The backend, its dependencies, migrations, and contracts remained unchanged. No
endpoint was added. Authentication and authorization were not implemented.

## Incremental execution — Phase 2

| Increment | Delivery | Check performed before the next delivery |
| --- | --- | --- |
| 1 | Foundation, layout, navigation, and themes | TypeScript, lint, and component test |
| 2 | Offline OpenAPI, typed client, and errors | Real generation, MSW tests for 204/422/cancellation, and semaphore |
| 3 | Lists, filters, and scopes | Tests of global totals, visible services, empty state, and error refresh |
| 4 | Registration, editing, and deletion | Temporal rules, minimal PATCH, backend 422, and confirmation |
| 5 | Details, metrics, history, and charts | Build, period/null rules, and mocked HTTP integration tests |
| 6 | Cache, polling, and refresh | Tests showing no historical polling and a limit of four actual MSW queries |
| 7 | Docker/Nginx/Compose and documentation | Multi-stage build, real Nginx, PostgreSQL/worker, Playwright, and regressions |

Tests in the first increment had one scenario; the suite grew to 19 tests in
eight files. Checks were repeated when changes or failures warranted another run.

## Contracts and semantics

The contract was checked against routes, schemas, repositories, and OpenAPI 3.1
generated in memory by the current application (eight paths, eleven operations).
`scripts/export-openapi.py` does not start a lifespan, server, connections, or
worker. TypeScript generation is reproducible with pinned dependencies and also
part of the Docker build. Two consecutive generations produced the same SHA256:

```text
266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34
```

The global inventory uses list totals; UP/DOWN/no data and errors are counts
explicitly limited to the visible page. Activity is independent of the result.
Query errors count neither as DOWN nor as missing history. Prior data is retained
with a stale-data notice.

Availability is the share of UP checks without time weighting. Missing values
are not converted to zero. Period p95 comes from the full-period query, never
from individual-window percentiles. Durations include failures, as in the backend.
Dates are transmitted in UTC and displayed with the local time zone indicated.

Metrics and charts share a global limit of four concurrent requests. The queue
and requests support cancellation. Only visible services receive status queries.
Historical windows remain fixed in cache, without focus or reconnection polling/
refetch; users can explicitly advance the period.

## Results observed — Phase 3

| Check | Result |
| --- | --- |
| Vitest + Testing Library + MSW | 19 tests passed in eight files |
| Playwright Chromium against real Nginx/FastAPI/PostgreSQL/worker | 6 scenarios passed |
| Axe WCAG 2 A/AA and 2.1 AA, light and dark dashboard | No violations detected in audited scenarios |
| TypeScript `tsc --noEmit` | No errors, including E2E code |
| ESLint | No errors or warnings in the final run |
| Prettier | All matching files formatted |
| Production Vite build | Passed, with on-demand pages and charts |
| Local pytest | 184 passed, 13 skipped because they require integration |
| pytest in disposable PostgreSQL 17 | 195 passed, 2 skipped because they require the observability stack |
| Backend Ruff | Lint and format passed, 52 files |
| Backend mypy | No issues in 52 files |
| `pip check` | No broken dependencies |
| Ruff on four new/modified Python scripts | Lint and format passed |
| Real observability stack | Normal and unavailable-Collector scenarios passed |
| Main and E2E Compose | Configuration validated without using or printing local passwords |
| Container Nginx | `nginx -t` passed |
| Actual ports/network/user | Frontend on 127.0.0.1:8080, user 101, read-only filesystem, HTTP-only network |
| Targeted bundle search | No checked markers for credentials, telemetry, or the private test target |
| `git diff --check` | No errors |

The local build produced approximately 123 kB gzip for the main chunk and
118 kB gzip for charts, loaded separately; main CSS was approximately 36 kB gzip.
These sizes are not production performance measurements.

### Browser evidence

1. Registration through the interface with a controlled HTTP destination,
   PostgreSQL persistence, a real worker UP result, editing, deactivation,
   reload, cancellation, and deletion confirmation. Service GET and checks
   returned 404 after deletion.
2. Deterministic history: 25 checks, 20 UP and 5 TIMEOUT, 80% availability,
   22 ms mean and 32,8 ms p95, checked in the API and interface. Pagination
   showed 20 then 5 results. Charts made 12 queries per window.
3. No history: absent availability/latencies, empty table, and charts without
   data. An invalid period produced a recoverable error.
4. Global totals of 24 services separate from the 20/4 records on the pages.
   A 503 refresh error retained the latest data and was excluded from status counts. The active
   filter used the API and reset pagination.
5. The proxy rejected diagnostics, internal metrics, unknown routes, and
   unauthorized methods. Registration of a metadata IP returned 422 under the
   real SSRF policy. CSP and nosniff headers were checked in Nginx-served HTML.
6. Keyboard, skip-to-content link, focus and heading below the fixed header,
   mobile navigation at 390×844, no page overflow, themes, and axe auditing.

Local captures, ignored by Git:

- [Light dashboard](../../../frontend/test-results/dashboard-visible-scope-fi-07b8a-ion-and-stale-update-errors/dashboard-light.png)
- [Dark dashboard](../../../frontend/test-results/dashboard-desktop-and-mobi-6cf46-th-themes-and-accessibility/dashboard-dark.png)
- [Mobile dashboard](../../../frontend/test-results/dashboard-desktop-and-mobi-6cf46-th-themes-and-accessibility/dashboard-mobile.png)
- [History and charts](../../../frontend/test-results/dashboard-historical-metri-d9a79-hart-windows-and-pagination/historical-charts.png)
- [Real worker UP check](../../../frontend/test-results/dashboard-real-CRUD-throug-76d3a--and-an-actual-worker-check/real-worker-up.png)

Playwright report: `frontend/playwright-report/index.html`.
Detailed session logs, outside Git:

```text
/tmp/infrapulse-sprint05-e2e.log
/tmp/infrapulse-sprint05-postgres.log
/tmp/infrapulse-sprint05-observability.log
/tmp/infrapulse-sprint05-frontend-build.log
```

### Operational evidence

The E2E stack published only `127.0.0.1:8080`. The API, worker, controlled target,
and PostgreSQL published no ports. The frontend had only the `api_access` network;
the database had only the internal data network. The main Compose configuration
was validated with loopback-only bindings for API:8000, Grafana:3000, and
frontend:8080. This does not claim the new frontend ran in the development project.

The operational regression confirmed API/worker scrapes as UP, Tempo trace
retrieval, Grafana dashboard queries, and Alertmanager receiving an alert.
With the Collector stopped, the API remained ready and the worker persisted
another UP. All ports in that disposable operational stack were unpublished.

Disposable environments were removed, including tmpfs databases. The development
API and PostgreSQL retained their IDs, respectively `73b8b6ff3fd3` and
`b1ed7690ecac`, without restart. The local `.env` was not edited.

## Fixes identified during validation

Fixed: quoting in Nginx regular expressions; client compatibility with MSW
interception; asynchronous/accessible test selectors; text and button contrast
in both themes; heading position when using the skip-to-content link; required
time zone and nonempty subintervals in short periods. Results above are from
passing runs after their respective fixes.

## Security, workflow, and limits

AGENTS.md, hooks, Entire/Brain configuration, backend code, and contracts were
preserved. Brain was queried through brief/recall. Because its index was one
commit behind, decisions were checked directly in current files. A graph
failure did not prompt reindexing, installation, or configuration changes.

No commits, pushes, history rewrites, or manual checkpoints occurred.

Documented limitations:

- Global UP/DOWN, text search, configurable sorting, and a global status filter
  require approval of a backend extension; the interface does not promise them.
- Each chart costs up to 12 queries; there is no cross-request snapshot guarantee.
- Checks persisted late may change closed periods. Historical cache does not imply
  database immutability; reloading obtains a new read.
- Check pagination respects a maximum offset of 10000; narrow the period beyond it.
- E2E ran in Chromium. Firefox/WebKit and full human accessibility/screen-reader
  assessment were not performed.
- No authentication, public publication, HA, notifications, or worker changes.
- The development integration was not started automatically; the pre-existing
  local stack was preserved. The existing `.env` had no Grafana password set;
  validation used only ephemeral passwords without changing that file.

## Reproduction and Conventional Commit — Phase 4

Full instructions are in [frontend.md](../../operations/frontend.md), with
decisions in [ADR 0003](../../adr/0003-frontend-dashboard.md).

```bash
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
cd frontend
npx playwright install chromium
cd ..
python3 scripts/validate-dashboard.py
```

Suggestion, not executed:

```text
feat(frontend): add service reliability dashboard
```

## File inventory

### Modified (5)

- `.gitignore`
- `README.md`
- `compose.observability.test.yaml`
- `compose.yaml`
- `scripts/verify-observability-ports.py`

### Created (60)

- `.dockerignore`
- `compose.frontend.test.yaml`
- `docs/adr/0003-frontend-dashboard.md`
- `docs/frontend.md`
- `docs/sprint05-report.md`
- `frontend/.dockerignore`
- `frontend/.env.example`
- `frontend/.prettierignore`
- `frontend/.prettierrc.json`
- `frontend/Dockerfile`
- `frontend/e2e/dashboard.spec.ts`
- `frontend/eslint.config.js`
- `frontend/functional-proxy.conf`
- `frontend/index.html`
- `frontend/nginx.conf`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/playwright.config.ts`
- `frontend/public/favicon.svg`
- `frontend/scripts/generate-api.mjs`
- `frontend/src/app/Layout.test.tsx`
- `frontend/src/app/Layout.tsx`
- `frontend/src/app/providers.tsx`
- `frontend/src/app/query-client.ts`
- `frontend/src/app/router.tsx`
- `frontend/src/features/monitoring/Charts.tsx`
- `frontend/src/features/monitoring/PeriodFilter.tsx`
- `frontend/src/features/monitoring/queries.test.tsx`
- `frontend/src/features/monitoring/queries.ts`
- `frontend/src/features/monitoring/windows.test.ts`
- `frontend/src/features/monitoring/windows.ts`
- `frontend/src/features/services/DeleteService.tsx`
- `frontend/src/features/services/ServiceForm.test.tsx`
- `frontend/src/features/services/ServiceForm.tsx`
- `frontend/src/features/services/form.test.ts`
- `frontend/src/features/services/form.ts`
- `frontend/src/features/services/queries.ts`
- `frontend/src/main.tsx`
- `frontend/src/pages/ServiceDetailPage.tsx`
- `frontend/src/pages/ServiceFormPage.tsx`
- `frontend/src/pages/ServicesPage.test.tsx`
- `frontend/src/pages/ServicesPage.tsx`
- `frontend/src/shared/api/client.test.ts`
- `frontend/src/shared/api/client.ts`
- `frontend/src/shared/api/errors.ts`
- `frontend/src/shared/lib/limiter.test.ts`
- `frontend/src/shared/lib/limiter.ts`
- `frontend/src/shared/lib/presentation.ts`
- `frontend/src/shared/ui/Feedback.tsx`
- `frontend/src/shared/ui/StatusBadge.tsx`
- `frontend/src/styles.css`
- `frontend/src/test/render.tsx`
- `frontend/src/test/server.ts`
- `frontend/src/test/setup.ts`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `frontend/vitest.config.ts`
- `scripts/export-openapi.py`
- `scripts/seed-dashboard-e2e.py`
- `scripts/validate-dashboard.py`

### Generated and ignored

`frontend/src/shared/api/generated.d.ts`, `frontend/dist/`, `frontend/node_modules/`,
`frontend/test-results/` e `frontend/playwright-report/`. They are not part of the versioned delivery; the documented commands reproduce them.
