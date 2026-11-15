# Contributing to InfraPulse

InfraPulse uses `develop` for ongoing development and `main` for stable code. Keep changes small and reviewable, and prefer Conventional Commits. Review the [architecture](docs/architecture.md) and [documentation index](docs/README.md) before changing behavior or operations.

## Local setup

Follow the [local Docker Compose instructions](README.md#run-locally). Copy `backend/.env.example` to `backend/.env` and set local passwords; do not commit credentials or generated artifacts.

## Checks

For backend development, install the locked development dependencies as described in the [README](README.md#tests-and-quality), then run the relevant checks from `backend/`:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

For frontend changes, see [frontend operations](docs/operations/frontend.md) for setup and test commands. The [CI workflow](.github/workflows/ci.yml) runs additional checks. Report which checks you actually ran and any limitations in your pull request.

## Pull requests

Describe the change, its motivation, relevant documentation, and verification. Keep secrets and private evidence out of issues and pull requests. Use the [pull request template](.github/PULL_REQUEST_TEMPLATE.md). For security issues, follow [SECURITY.md](SECURITY.md).
