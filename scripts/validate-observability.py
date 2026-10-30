"""Validate real telemetry, outage and recovery in an owned disposable stack."""

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from integration_support import (
    ROOT,
    IntegrationProject,
    redact_credentials,
    run_cli,
)

logger = logging.getLogger("infrapulse.integration")


def run_case(project: IntegrationProject, case: str, label: str, results: Path) -> None:
    container = f"{project.name}-{label}"
    primary_error: Exception | None = None
    try:
        output = project.run(
            [
                *project.compose,
                "run",
                "--name",
                container,
                "--no-deps",
                "-e",
                f"INFRAPULSE_COLLECTOR_OUTAGE={int(label == 'outage')}",
                "observability-tests",
                "pytest",
                "-v",
                "-s",
                "--strict-markers",
                f"tests/test_observability_stack.py::{case}",
                "-o",
                "cache_dir=/tmp/pytest_cache",
                "--junitxml=/tmp/result.xml",
            ],
            capture=True,
        )
        print(redact_credentials(output), flush=True)
    except Exception as error:
        primary_error = error
        if isinstance(error, subprocess.CalledProcessError):
            print(redact_credentials(error.stdout or ""), flush=True)
        logger.exception("Required observability test failed")
    try:
        project.run(
            [
                "docker",
                "cp",
                f"{container}:/tmp/result.xml",
                str(results / f"{label}.xml"),
            ]
        )
    except Exception:
        logger.exception("JUnit collection failed")
        if primary_error is None:
            raise
    if primary_error is not None:
        raise primary_error
    tree = ET.parse(results / f"{label}.xml")
    assert len(list(tree.getroot().iter("testcase"))) == 1
    assert not any(
        list(tree.getroot().iter(tag)) for tag in ("failure", "error", "skipped")
    )


def correlate(project: IntegrationProject) -> None:
    candidates: dict[str, list[str]] = {}
    for role in ("api", "worker"):
        logs = project.run([*project.compose, "logs", "--no-color", role], capture=True)
        candidates[role] = list(
            dict.fromkeys(re.findall(r'"trace_id": "([a-f0-9]{32})"', logs))
        )[-30:]
        assert candidates[role], f"No correlated {role} log records"
    # Check actual log IDs against Tempo from a container on the private network.
    probe = (
        "import httpx, json, sys, time\n"
        "candidates=json.loads(sys.stdin.read())\n"
        "with httpx.Client(timeout=5, trust_env=False) as client:\n"
        " for role, identifiers in candidates.items():\n"
        "  deadline=time.monotonic()+30\n"
        "  while True:\n"
        "   found=next((identifier for identifier in identifiers if "
        "client.get('http://tempo:3200/api/traces/'+identifier).status_code == 200), None)\n"
        "   if found:\n"
        "    print('LOG_TRACE_CORRELATION', role, found); break\n"
        "   if time.monotonic() > deadline: raise RuntimeError('No trace for '+role)\n"
        "   time.sleep(1)\n"
    )
    project.run(
        [
            *project.compose,
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "observability-tests",
            "python",
            "-c",
            probe,
        ],
        source=json.dumps(candidates),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    project = IntegrationProject(
        "observability", ["compose.yaml", "compose.observability.test.yaml"]
    )
    project.env.update(
        POSTGRES_DB="infrapulse", POSTGRES_USER="infrapulse", GRAFANA_ADMIN_USER="admin"
    )
    if args.cleanup:
        project.cleanup()
        return
    results = Path(
        os.environ.get(
            "OBSERVABILITY_RESULTS_DIR",
            tempfile.mkdtemp(prefix="infrapulse-observability-results-"),
        )
    )
    results.mkdir(parents=True, exist_ok=True)
    with project:
        project.run([*project.compose, "config", "--quiet"])
        project.run(
            [
                *project.compose,
                "build",
                "api",
                "worker",
                "monitoring-target",
                "observability-tests",
            ]
        )
        project.allow_target("telemetry")
        for service, entrypoint, command in (
            (
                "prometheus",
                "/bin/promtool",
                ["check", "config", "/etc/prometheus/prometheus.yaml"],
            ),
            (
                "prometheus",
                "/bin/promtool",
                ["check", "rules", "/etc/prometheus/alerts.yaml"],
            ),
            (
                "prometheus",
                "/bin/promtool",
                ["test", "rules", "/tests/rules.test.yaml"],
            ),
            (
                "otel-collector",
                "/otelcol-contrib",
                ["validate", "--config=/etc/otelcol/config.yaml"],
            ),
            (
                "alertmanager",
                "/bin/amtool",
                ["check-config", "/etc/alertmanager/config.yaml"],
            ),
        ):
            project.run(
                [
                    *project.compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    entrypoint,
                    service,
                    *command,
                ]
            )
        project.run(
            [*project.compose, "up", "-d", "--wait", "--wait-timeout", "120", "db"]
        )
        project.run(
            [
                *project.compose,
                "run",
                "--rm",
                "--no-deps",
                "api",
                "alembic",
                "upgrade",
                "head",
            ]
        )
        project.run([*project.compose, "up", "-d", "--wait", "--wait-timeout", "180"])
        project.run(
            [
                "python3",
                str(ROOT / "scripts/verify-observability-ports.py"),
                project.name,
            ]
        )
        for network in ("internal", "telemetry"):
            project.assert_internal(network)
        run_case(project, "test_real_observability_stack", "normal", results)
        correlate(project)
        project.run([*project.compose, "stop", "otel-collector"])
        run_case(project, "test_collector_outage_real_processes", "outage", results)
        project.run([*project.compose, "start", "otel-collector"])
        run_case(project, "test_real_observability_stack", "recovery", results)
        project.run([*project.compose, "stop", "worker"])
        logs = project.run(
            [*project.compose, "logs", "--no-color", "worker"], capture=True
        )
        assert '"message": "worker_stopped"' in logs
        assert '"message": "scheduler_stopped"' in logs
        print("Verified graceful worker and scheduler shutdown", flush=True)
        project.run(
            [*project.compose, "up", "-d", "--wait", "--wait-timeout", "120", "worker"]
        )
        print(f"Observability JUnit reports: {results}", flush=True)


if __name__ == "__main__":
    run_cli(main)
