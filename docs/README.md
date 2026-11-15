# InfraPulse documentation

Start with the [architecture overview](architecture.md) for the system boundaries and data flow. The documents below describe the implementation, operational procedures, design decisions, and historical delivery record. Sprint reports record decisions and evidence at the time they were written; consult current architecture and operations guides for present behavior.

## Architecture and security evidence

| Document | Description |
| --- | --- |
| [Architecture overview](architecture.md) | System components, data flow, observability, and CI boundaries. |
| [Local SBOM generation (F3A)](sbom-f3a.md) | SBOM generation, coverage policy, and local verification. |
| [Local SBOM provenance (F3B.1)](provenance-f3b1.md) | Provenance statements and verification for SBOM artifacts. |
| [Local signatures and offline verification (F3B.2.2)](provenance-f3b2-2.md) | Signature and offline trust checks. |
| [CI evidence generation (F3C.2)](ci-evidence-f3c2.md) | Evidence artifact generation and validation boundaries. |
| [CI evidence signing (F3C.3.1)](ci-evidence-f3c3.md) | Local signing and verification of CI evidence. |
| [Disposable runner validation (F3C.3.2)](f3c3-real-validation.md) | Integrated validation of pinned evidence and tools. |
| [OCI archive canonicalization](oci-canonical-exporter.md) | Local canonical export of OCI image archives. |

## Architecture decision records

| Document | Description |
| --- | --- |
| [ADR 0001: HTTP monitoring](adr/0001-http-monitoring.md) | Independent worker and controlled HTTP transport. |
| [ADR 0002: Observability](adr/0002-observability-architecture.md) | Process-isolated metrics and tracing design. |
| [ADR 0003: Dashboard](adr/0003-frontend-dashboard.md) | SPA design over existing REST contracts. |

## Operations

| Document | Description |
| --- | --- |
| [HTTP monitoring](operations/monitoring.md) | Monitoring policy, network controls, and operations. |
| [Observability](operations/observability.md) | Local telemetry stack, metrics, traces, and alerting. |
| [Frontend dashboard](operations/frontend.md) | Frontend development, API contracts, and dashboard behavior. |

## Documentation naming conventions

These names are specific to InfraPulse. Historical filenames are preserved for traceability.

- **Sprint**: a development cycle, such as `sprint06` in [Sprint 06 phase B report](history/sprints/sprint06-phase-b-report.md).
- **Phase**: a major delivery stage, such as `phase-f3` in [Sprint 06 F3C.4.2B](history/sprints/sprint06-phase-f3c4-2b.md).
- **Subphase**: a technical subdivision within a phase, such as `f3c4` in `sprint06-phase-f3c4-2b.md`.
- **Iteration**: a specific implementation or refinement, such as `2b` in `sprint06-phase-f3c4-2b.md`.
- **Report**: a record of completed work and its results, such as [Sprint 06 phase D1 report](history/sprints/sprint06-phase-d1-report.md).
- **Handoff**: context and next steps for continuing work, as in the [Sprint 06 handoff](history/sprints/sprint06-handoff.md).
- **Reconciliation**: a record of how related changes or evidence were aligned, as in the [Sprint 06 F3C.3 reconciliation](history/sprints/sprint06-f3c3-reconciliation.md).

## Sprint history

| Document | Description |
| --- | --- |
| [Sprint 03 report](history/sprints/sprint03-report.md) | HTTP monitoring delivery record. |
| [Sprint 04 report](history/sprints/sprint04-report.md) | Observability and telemetry delivery record. |
| [Sprint 05 report](history/sprints/sprint05-report.md) | Frontend dashboard delivery record. |
| [Sprint 06 phase B](history/sprints/sprint06-phase-b-report.md) | Backend CI work. |
| [Sprint 06 phase C](history/sprints/sprint06-phase-c-report.md) | Frontend CI and OpenAPI contract work. |
| [Sprint 06 phase D1](history/sprints/sprint06-phase-d1-report.md) | PostgreSQL integration CI. |
| [Sprint 06 phase D2](history/sprints/sprint06-phase-d2-report.md) | Playwright and observability CI. |
| [Sprint 06 phase E](history/sprints/sprint06-phase-e-report.md) | Docker builds and image validation. |
| [Sprint 06 phase F1A](history/sprints/sprint06-phase-f1a-report.md) | First F1 delivery record. |
| [Sprint 06 phase F1B](history/sprints/sprint06-phase-f1b-report.md) | Follow-up F1 delivery record. |
| [Sprint 06 phase F2A](history/sprints/sprint06-phase-f2a-report.md) | First F2 delivery record. |
| [Sprint 06 phase F2B](history/sprints/sprint06-phase-f2b-report.md) | Follow-up F2 delivery record. |
| [Sprint 06 phase F2C](history/sprints/sprint06-phase-f2c-report.md) | F2 review corrections. |
| [Sprint 06 F3C.3 reconciliation](history/sprints/sprint06-f3c3-reconciliation.md) | Validator integration and OCI reconciliation. |
| [Sprint 06 handoff](history/sprints/sprint06-handoff.md) | F3C.3.2 and F3C.4 handoff. |
| [Sprint 06 F3C.4.2B](history/sprints/sprint06-phase-f3c4-2b.md) | Virtual OCI reconstruction. |
| [Sprint 06 F3C.4.2C](history/sprints/sprint06-phase-f3c4-2c.md) | Virtual OCI inventory. |
| [Sprint 06 F3C.4.2D](history/sprints/sprint06-phase-f3c4-2d.md) | Preliminary OCI package reconciliation. |
| [Sprint 06 F3C.4 evidence draft](history/sprints/sprint06-phase-f3c4-evidence-draft.md) | F3C.4.3 and F3C.4.4 evidence preparation. |
