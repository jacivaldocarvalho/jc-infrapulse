# InfraPulse architecture

This overview describes the local implementation and CI workflow in this checkout. The Compose stack is intended for development; the product does not provide authentication or authorization. See the decisions in the [ADRs](adr/0001-http-monitoring.md) and the [monitoring](operations/monitoring.md), [observability](operations/observability.md), and [frontend](operations/frontend.md) guides.

## System overview

```mermaid
flowchart LR
    Browser[Browser] --> Frontend[Frontend and Nginx proxy]
    Frontend --> API[FastAPI API]
    API --> DB[(PostgreSQL)]
    Worker[Monitoring worker] --> DB
    Worker --> Target[Monitored services]
    API --> Collector[OpenTelemetry Collector]
    Worker --> Collector
    Collector --> Tempo[Tempo]
    Prometheus[Prometheus] --> API
    Prometheus --> Worker
    Prometheus --> Alertmanager[Alertmanager]
    Grafana[Grafana] --> Prometheus
    Grafana --> Tempo
```

The frontend, API, and Grafana are published on loopback by the Compose configuration. The worker is a separate process, and the current deployment design uses one replica. The arrows from Prometheus to the API and worker represent scraping metric listeners that are separate from the functional ports.

## API and worker

```mermaid
flowchart TD
    Client[Client] --> Registry[Service registry API]
    Registry --> DB[(PostgreSQL)]
    Scheduler[Worker scheduler] --> DB
    Scheduler --> Policy[DNS resolution and URL policy]
    Policy --> Transport[HTTP connection to an authorized IP]
    Transport --> Target[Monitored service]
    Transport --> Results[Check result]
    Results --> DB
    DB --> Monitoring[History, status, and metrics API]
    Monitoring --> Client
```

The policy validates the target before connecting while preserving the hostname for Host and TLS. The worker does not use proxies or redirects for checks. The API computes historical metrics from persisted results; PostgreSQL has no automatic retention. Details and limits are in [ADR 0001](adr/0001-http-monitoring.md) and the [monitoring guide](operations/monitoring.md).

## Observability

```mermaid
flowchart LR
    API[API] -->|OTLP traces| Collector[Collector]
    Worker[Worker] -->|OTLP traces| Collector
    Collector --> Tempo[Tempo]
    Prometheus[Prometheus] -->|scrapes metrics| API
    Prometheus -->|scrapes metrics| Worker
    Prometheus -->|alerts| Alertmanager[Alertmanager]
    Grafana[Grafana] -->|queries| Prometheus
    Grafana -->|queries| Tempo
```

Traces are best effort. The default Alertmanager receiver is local, with no external notification integration. See [ADR 0002](adr/0002-observability-architecture.md) and the [operations guide](operations/observability.md).

## CI/CD

```mermaid
flowchart LR
    Change[Push or pull request] --> Quality[Quality, tests, and API contract]
    Change --> Integration[PostgreSQL, frontend, and observability tests]
    Change --> Security[security-foundations]
    Change --> Images[Image build and validation]
    Quality --> Required[ci-required]
    Integration --> Required
    Security --> Required
    Images --> Required
    Images --> Audit[informational vulnerability-audit]
    Images --> Evidence[unsigned ci-evidence]
```

The diagram summarizes dependencies in the [CI workflow](../.github/workflows/ci.yml): `ci-required` aggregates its dependent jobs, while vulnerability auditing and evidence generation have separate results. It does not represent deployment or publication. Provenance and coverage limits are described in the [provenance guide](provenance-f3b1.md) and [CI evidence guide](ci-evidence-f3c2.md).
