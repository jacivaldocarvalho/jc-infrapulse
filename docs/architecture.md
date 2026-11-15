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

## DevSecOps CI and Software Supply Chain

```mermaid
flowchart LR
    Source["Source: push or pull request"]
    Contract["api-contract"]
    Quality["backend-quality and backend-unit"]
    FrontendContract["frontend-quality and frontend-build"]
    FrontendTests["frontend-tests"]
    Integration["backend-postgres, playwright-e2e, and observability-integration"]
    Security["security-foundations: supply chain, secrets, and OCI regressions"]
    Images["docker-build: build, inspect, and smoke images"]
    Required["ci-required: blocking aggregate"]
    Audit["vulnerability-audit: informational"]
    Evidence["ci-evidence: SBOM and unsigned provenance on push"]
    Dispatch["Manual dispatch on main"]
    Historical["Pinned historical run artifacts and validator commits"]
    Verify["F3C.3.2: evidence and Cosign verification"]
    Planned["Production deployment / CD: Planned"]

    Source --> Contract
    Source --> Quality
    Source --> FrontendTests
    Source --> Integration
    Source --> Security
    Contract --> FrontendContract
    Contract --> Images
    Quality --> Required
    Contract --> Required
    FrontendContract --> Required
    FrontendTests --> Required
    Integration --> Required
    Security --> Required
    Images --> Required
    Images --> Audit
    Images --> Evidence
    Dispatch --> Verify
    Historical --> Verify

    style Planned fill:#fff3cd,stroke:#b7791f,stroke-width:3px,color:#332000
```

**Legend:** Solid arrows show workflow triggers, declared job dependencies, or the pinned inputs to isolated verification; arrows into `ci-required` show its blocking `needs` aggregate. `vulnerability-audit` is informational and outside that aggregate. `ci-evidence` generates and validates unsigned SBOM/provenance evidence only after a successful image build on branch pushes; it is also outside the aggregate. The separately dispatched F3C.3.2 workflow verifies pinned historical artifacts and Cosign signatures, without depending on the current CI run. Production deployment/CD is planned and is not connected to an implemented pipeline.

Workflows: [CI](../.github/workflows/ci.yml) and [F3C.3.2 isolated real validation](../.github/workflows/f3c3-real-validation.yml).

Provenance and coverage limits are described in the [provenance guide](provenance-f3b1.md) and [CI evidence guide](ci-evidence-f3c2.md).
