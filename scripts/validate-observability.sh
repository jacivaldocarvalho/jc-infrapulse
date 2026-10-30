#!/usr/bin/env bash
# Run from the repository root. Uses only disposable projects and ephemeral passwords.
set -euo pipefail
project="infrapulse-observability-$$"
pg_project="${project}-pg"
credentials=$(mktemp /tmp/infrapulse-observability.XXXXXX)
chmod 600 "$credentials"
python3 - "$credentials" <<'PY'
import secrets
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(''.join(f'{key}={secrets.token_hex(24)}\n' for key in ('POSTGRES_PASSWORD', 'GRAFANA_ADMIN_PASSWORD', 'INFRAPULSE_TEST_PASSWORD')))
PY
obs=(docker compose --env-file "$credentials" -p "$project" -f compose.yaml -f compose.observability.test.yaml)
pg=(docker compose --env-file "$credentials" -p "$pg_project" -f compose.test.yaml)
cleanup() {
    "${obs[@]}" down --volumes --remove-orphans
    "${pg[@]}" down --volumes --remove-orphans
    unlink "$credentials"
}
trap cleanup EXIT
"${obs[@]}" config --quiet
"${pg[@]}" config --quiet
# Validators are the same versions as the runtime components.
docker run --rm --network none -v "$PWD/observability/prometheus:/etc/prometheus:ro" --entrypoint /bin/promtool prom/prometheus:v3.5.0 check config /etc/prometheus/prometheus.yaml
docker run --rm --network none -v "$PWD/observability/prometheus:/etc/prometheus:ro" --entrypoint /bin/promtool prom/prometheus:v3.5.0 check rules /etc/prometheus/alerts.yaml
docker run --rm --network none -v "$PWD/observability/prometheus:/etc/prometheus:ro" -v "$PWD/observability/tests:/tests:ro" --entrypoint /bin/promtool prom/prometheus:v3.5.0 test rules /tests/rules.test.yaml
docker run --rm --network none -v "$PWD/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" otel/opentelemetry-collector-contrib:0.128.0 validate --config=/etc/otelcol/config.yaml
docker run --rm --network none -v "$PWD/observability/alertmanager/config.yaml:/etc/alertmanager/config.yaml:ro" --entrypoint /bin/amtool prom/alertmanager:v0.28.1 check-config /etc/alertmanager/config.yaml
"${pg[@]}" build tests
"${pg[@]}" up --abort-on-container-exit --exit-code-from tests
"${pg[@]}" down --volumes
"${obs[@]}" build api worker monitoring-target observability-tests
"${obs[@]}" up -d --wait db
"${obs[@]}" run --rm --no-deps api alembic upgrade head
"${obs[@]}" up -d --wait
python3 scripts/verify-observability-ports.py "$project"
"${obs[@]}" run --rm --no-deps observability-tests
# Real unavailable Collector: API remains responsive; worker continues persisting.
"${obs[@]}" stop otel-collector
"${obs[@]}" run --rm --no-deps -e INFRAPULSE_COLLECTOR_OUTAGE=1 observability-tests pytest -q -s tests/test_observability_stack.py -k collector_outage -o cache_dir=/tmp/pytest_cache
"${obs[@]}" stop worker
"${obs[@]}" logs --tail 5 worker
"${obs[@]}" start otel-collector worker
