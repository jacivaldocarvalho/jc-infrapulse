# InfraPulse — Agent Instructions

## Project
InfraPulse is an API Reliability & Observability Platform.

## Architecture
- Backend: Python 3.12+ and FastAPI
- Database: PostgreSQL
- ORM: SQLAlchemy 2.x
- Migrations: Alembic
- Validation: Pydantic v2
- Tests: pytest and httpx
- Quality: Ruff and mypy
- Infrastructure: Docker Compose
- Observability: Prometheus and OpenTelemetry

## Engineering Principles
1. Follow clean architecture and separation of concerns.
2. Prefer simple, maintainable implementations.
3. Use type hints in all Python functions.
4. Never hardcode credentials or secrets.
5. Keep configuration in environment variables.
6. Implement automated tests for business logic.
7. Use structured logging.
8. Provide health and readiness endpoints.
9. Document relevant architectural decisions.
10. Do not introduce unnecessary dependencies.

## Development Workflow
- Use main for stable code.
- Use develop for ongoing development.
- Prefer Conventional Commits.
- Work in small, reviewable increments.
- Run tests and linters before proposing a commit.
- Never push without explicit approval.
- Never rewrite Git history without approval.
- Never disable Entire hooks or alter checkpoints.
- Keep generated artifacts and secrets out of Git.

## AI Agent Requirements
Before implementation:
- Inspect the repository.
- Explain the proposed approach.
- Identify files to create or modify.

After implementation:
- Summarize changes.
- Report tests and their results.
- Report limitations or pending work.
- Suggest an appropriate Conventional Commit.
- Do not claim tests passed unless executed.

## MVP Scope
- FastAPI application
- PostgreSQL integration
- Service registration
- HTTP health checks
- Basic reliability metrics
- Automated tests
- Docker Compose environment

## Out of Scope for First Iteration
- Frontend implementation
- Kubernetes deployment
- Terraform provisioning
- Authentication and authorization
- External notification integrations
