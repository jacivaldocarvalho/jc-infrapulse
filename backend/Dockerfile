FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY pyproject.toml ./
COPY requirements*.lock install-locked.sh ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN bash install-locked.sh runtime

FROM base AS test
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*
COPY tests ./tests
RUN bash install-locked.sh dev
USER app
CMD ["pytest", "-v", "-rs", "-o", "cache_dir=/tmp/pytest_cache"]

FROM base AS runtime
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
