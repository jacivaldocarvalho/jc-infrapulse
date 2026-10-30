"""Validate Nginx, real CRUD/worker checks and ports in a disposable Compose project.

Requires frontend dependencies and Playwright Chromium to be installed.
Never reads backend/.env, touches development containers or prints credentials.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = f"infrapulse-frontend-{os.getpid()}"
ENV = dict(
    os.environ,
    INFRAPULSE_TEST_PASSWORD=secrets.token_hex(24),
    INFRAPULSE_E2E_DISPOSABLE="1",
    POSTGRES_PASSWORD=secrets.token_hex(24),
    GRAFANA_ADMIN_PASSWORD=secrets.token_hex(24),
)
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    "/dev/null",
    "-p",
    PROJECT,
    "-f",
    "compose.frontend.test.yaml",
]


def run(command: list[str], *, capture: bool = False, source: str | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=ENV,
        input=source,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return result.stdout if capture else ""


def development_state() -> str:
    return run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={ROOT.name}",
            "--format",
            "{{.ID}} {{.Names}}",
        ],
        capture=True,
    )


def verify_ports() -> None:
    containers = run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ],
        capture=True,
    ).split()
    assert len(containers) == 5, containers
    for identifier in containers:
        service = run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.service"}}',
                identifier,
            ],
            capture=True,
        ).strip()
        ports = (
            json.loads(
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .NetworkSettings.Ports}}",
                        identifier,
                    ],
                    capture=True,
                )
            )
            or {}
        )
        bindings = [
            (port, binding)
            for port, values in ports.items()
            for binding in values or []
        ]
        if service == "frontend":
            assert bindings == [
                ("8080/tcp", {"HostIp": "127.0.0.1", "HostPort": "8080"})
            ], bindings
            networks = json.loads(
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .NetworkSettings.Networks}}",
                        identifier,
                    ],
                    capture=True,
                )
            )
            assert set(networks) == {f"{PROJECT}_api_access"}, networks
            assert (
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.Config.User}} {{.HostConfig.ReadonlyRootfs}}",
                        identifier,
                    ],
                    capture=True,
                ).strip()
                == "101 true"
            )
        else:
            assert not bindings, (service, bindings)
        if service == "test-db":
            networks = json.loads(
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .NetworkSettings.Networks}}",
                        identifier,
                    ],
                    capture=True,
                )
            )
            assert set(networks) == {f"{PROJECT}_data"}, networks
        print(f"Runtime: {service} published={bindings}", flush=True)


def main() -> None:
    assert run(["git", "branch", "--show-current"], capture=True).strip() == "develop"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8080))
    before = development_state()
    print(
        f"Disposable project: {PROJECT}; development containers preserved", flush=True
    )
    config = json.loads(
        run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "config",
                "--format",
                "json",
            ],
            capture=True,
        )
    )
    expected = {"api": 8000, "grafana": 3000, "frontend": 8080}
    for name, service in config["services"].items():
        ports = service.get("ports", [])
        if name in expected:
            assert (
                len(ports) == 1
                and ports[0]["host_ip"] == "127.0.0.1"
                and int(ports[0]["published"]) == expected[name]
            )
        else:
            assert not ports, name
    assert set(config["services"]["db"]["networks"]) == {"internal"}
    assert set(config["services"]["frontend"]["networks"]) == {"api_access"}
    print(
        "Main Compose: only API:8000, Grafana:3000 and frontend:8080 on 127.0.0.1",
        flush=True,
    )
    run([*COMPOSE, "config", "--quiet"])
    try:
        run([*COMPOSE, "build", "api", "worker", "frontend"])
        run([*COMPOSE, "up", "-d", "--wait", "test-db"])
        run([*COMPOSE, "run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head"])
        run([*COMPOSE, "up", "-d", "--wait"])
        run(
            [*COMPOSE, "exec", "-T", "api", "python", "-"],
            source=(ROOT / "scripts/seed-dashboard-e2e.py").read_text(),
        )
        run([*COMPOSE, "exec", "-T", "frontend", "nginx", "-t"])
        verify_ports()
        run(["npm", "--prefix", "frontend", "run", "test:e2e"])
    except BaseException:
        run([*COMPOSE, "logs", "--tail", "40", "frontend"])
        raise
    finally:
        try:
            run([*COMPOSE, "down", "--volumes", "--remove-orphans"])
        finally:
            assert development_state() == before, (
                "Development containers changed during verification"
            )
            print(
                "Development container IDs unchanged; disposable stack removed",
                flush=True,
            )


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.CalledProcessError) as error:
        print(f"Dashboard verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
