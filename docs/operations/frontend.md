# Frontend Dashboard — Sprint 05

## Local development

Requirements: Node.js 24 (>=24.15), npm, and Python 3.12+ with the backend installed according to
`backend/requirements.lock`. The API may remain in an existing container; generating
types does not require starting the API, worker, or database and does not read backend credentials.

From the repository root:

```bash
npm --prefix frontend ci
npm --prefix frontend run generate:api
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`. The development proxy forwards `/api/v1` to the API
at `http://127.0.0.1:8000`. For another local target, set `API_PROXY_TARGET` in
the Vite process environment. This configuration belongs only to the Node server
and is not exposed in the bundle. The browser client always uses its own origin.

The generator uses `backend/.venv/bin/python` when available, or `python3`. The
`PYTHON` variable can explicitly select an interpreter with the backend installed.
`OPENAPI_SCHEMA_FILE` allows consumption of a contract exported by the Docker
Python stage. Never replace that file with a manually written schema.

## Contracts and quality

`scripts/export-openapi.py` imports the current application and calls `app.openapi()`
without a lifespan or database session. `frontend/scripts/generate-api.mjs` uses
`openapi-typescript` and writes `src/shared/api/generated.d.ts`, which Git ignores.
There is no manual copy of backend DTOs. Form functions represent interface state
and are converted to generated payloads before submission.

```bash
npm --prefix frontend run generate:api
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
npm --prefix frontend run build
```

The build always regenerates types before TypeScript checking. Resolved dependencies
are in `frontend/package-lock.json`; reproducible installation uses `npm ci`.
Vitest runs rule and component tests with Testing Library and MSW.

## Interface semantics

- Global inventory: total, active, and inactive counts obtained from `total` in
  three list queries with `limit=1`. These calls do not form a transactional snapshot.
- UP/DOWN/no data: exclusive counts for services on the visible page. Services
  with pending queries or failed refreshes are identified separately.
- UP corresponds only to the `UP` enum value. All other technical results appear
  as DOWN, with the original cause retained in history and details.
- Inactive is a registry property independent of the latest result.
- Query errors neither fabricate results nor remove the latest received data;
  the interface identifies that data as stale.
- `/status` reports the latest overall check. Metrics and history use an explicit
  `[start, end)` window of at most 90 days. Dates are sent in UTC and displayed
  in the browser's local time zone, which the labels indicate.
- Availability is the share of UP checks, without time weighting. With no checks,
  availability and durations are absent and displayed as `—`.
- Mean and p95 include all non-null durations, including failed checks. The
  period-wide p95 comes directly from `/metrics` for the complete window.
- Charts query 12 contiguous subperiods, with at most four simultaneous metrics
  requests across the application. Percentiles are never combined. Chart gaps
  are not filled with zero. An equivalent table is available.

## Refresh and cache

Lists and global inventory refresh every 30 seconds. Status is queried only for
visible services; inactive services are not polled for status. Refresh can be
paused. The library pauses polling in hidden tabs, and requests receive the
query's AbortSignal. The shared queue removes canceled work before network access.

Metrics, history, and charts use fixed windows, without polling or focus/reconnection
refetch. The “Refresh period to now” button explicitly advances the window.
History remains in cache for five minutes after its last use.
Delayed persistence may change a closed window: this view is a cached query,
not a guarantee of database immutability. A new window or browser reload fetches
current data. Mutations invalidate related service and monitoring caches;
deletion removes caches for the deleted service.

Pagination uses offsets. New checks may shift results between requests; the fixed
window reduces shifts caused by passing time. The API's `offset=10000` limit is
respected; narrow the period to reach data beyond it.

## Container and security

The Dockerfile uses Python stages to export OpenAPI, Node to generate types and
compile the SPA, and unprivileged Nginx to serve the result. Python, Node,
credentials, and proxy variables are unnecessary in the final image.

The main Compose stack adds only `127.0.0.1:8080:8080` for the frontend. It joins
only `api_access`, without PostgreSQL or telemetry networks. Its filesystem is
read-only, with ephemeral `/tmp` and `no-new-privileges`. The `/healthz` endpoint
belongs to Nginx and checks the static server; it does not replace FastAPI readiness.

The proxy allows only the service collection, UUID resource, and status/checks/metrics
reads. It does not forward `/health`, `/ready`, `/docs`, OpenAPI, metrics listeners,
or operational services. Methods are also limited by route. SPA pages fall back to
`index.html` for direct navigation and reloads. Nginx sets CSP, anti-frame,
nosniff, and referrer-policy headers; JSON logs exclude URL, query, body, and headers.

After setting local passwords as described in the README and applying migrations:

```bash
docker compose --env-file backend/.env build frontend
docker compose --env-file backend/.env up -d --wait frontend
```

This operation may start dependencies; during sprint implementation it was not
run against the existing development project. Neither the SPA nor API has
authentication in this sprint; publication remains limited to loopback.

## Isolated E2E

```bash
cd frontend
npx playwright install chromium
cd ..
python3 scripts/validate-dashboard.py
```

The script creates a uniquely named Compose project with ephemeral in-memory
passwords, PostgreSQL in tmpfs, real migrations, the API, worker, controlled HTTP
target, and Nginx. Docker assigns a free loopback port to Nginx; the script
discovers it and supplies `E2E_BASE_URL` to Playwright. No specific branch is
required. No development `.env` is used. On completion, including failures, only
resources belonging to the disposable project are removed, and development
container IDs are compared with their initial state. Run Docker validators
sequentially on the same host: creating or removing another network during tests
may cause `ERR_NETWORK_CHANGED` in Chromium. In CI, Playwright and observability
use separate runners.

The seed requires the E2E flag and exact `test-db/infrapulse_test` destination;
it creates 24 inactive services and 25 deterministic checks. Separately,
Playwright creates a service through the interface and confirms a persisted UP
result from the worker against real HTTP. The suite covers CRUD, metrics, history,
charts, filters, error refresh, restricted proxy, SSRF, reload, keyboard, mobile
screens, and axe in light and dark themes. Automated auditing does not replace a
complete human accessibility evaluation.

Screenshots and reports are written to `frontend/test-results/` and
`frontend/playwright-report/`, both ignored by Git. After execution, the HTML
can be opened with `npx playwright show-report frontend/playwright-report`.

## Limitations

The API has no text search, configurable sorting, global status filter, or global
UP/DOWN summary. The interface does not simulate those features from one page.
Charts cost 12 queries per service/period combination; a future batch aggregation
extension will require contract approval. There is no authentication, WebSocket,
external notification, or direct browser query to Prometheus.
