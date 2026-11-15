"""Run the real dashboard E2E suite on a disposable stack and dynamic port."""

import argparse
import json

from integration_support import ROOT, IntegrationProject, run_cli


def verify_ports(project: IntegrationProject) -> str:
    identifiers = project.run([*project.compose, "ps", "-q"], capture=True).split()
    assert len(identifiers) == 5
    base_url = ""
    for identifier in identifiers:
        data = json.loads(
            project.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    (
                        '{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                        '"ports":{{json .NetworkSettings.Ports}},'
                        '"networks":{{json .NetworkSettings.Networks}},'
                        '"user":{{json .Config.User}},"readonly":{{json .HostConfig.ReadonlyRootfs}}}'
                    ),
                    identifier,
                ],
                capture=True,
            )
        )
        bindings = [
            (port, binding)
            for port, values in (data["ports"] or {}).items()
            for binding in values or []
        ]
        if data["service"] == "frontend":
            assert len(bindings) == 1
            port, binding = bindings[0]
            assert port == "8080/tcp" and binding["HostIp"] == "127.0.0.1"
            assert data["user"] == "101" and data["readonly"] is True
            assert set(data["networks"]) == {f"{project.name}_api_access"}
            base_url = f"http://127.0.0.1:{int(binding['HostPort'])}"
        else:
            assert not bindings
        if data["service"] == "test-db":
            assert set(data["networks"]) == {f"{project.name}_data"}
    assert base_url
    project.assert_internal("data")
    project.assert_internal("targets")
    print(f"Only Nginx published: {base_url}", flush=True)
    return base_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    project = IntegrationProject("e2e", ["compose.frontend.test.yaml"])
    if args.cleanup:
        project.cleanup()
        return
    with project:
        project.run([*project.compose, "config", "--quiet"])
        project.run([*project.compose, "build", "api", "worker", "frontend"])
        project.allow_target("targets")
        project.run(
            [*project.compose, "up", "-d", "--wait", "--wait-timeout", "120", "test-db"]
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
        project.run([*project.compose, "up", "-d", "--wait", "--wait-timeout", "120"])
        project.run(
            [*project.compose, "exec", "-T", "api", "python", "-"],
            source=(ROOT / "scripts/seed-dashboard-e2e.py").read_text(),
        )
        project.run([*project.compose, "exec", "-T", "frontend", "nginx", "-t"])
        project.env["E2E_BASE_URL"] = verify_ports(project)
        project.env["INFRAPULSE_E2E_DISPOSABLE"] = "1"
        project.run(["npm", "--prefix", "frontend", "run", "test:e2e"])


if __name__ == "__main__":
    run_cli(main)
