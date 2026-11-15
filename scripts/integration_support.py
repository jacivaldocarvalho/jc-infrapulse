"""Shared lifecycle for disposable integration projects; never reads a local .env."""

import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self

ROOT = Path(__file__).resolve().parents[1]
_SECRET_VALUES: set[str] = set()


def redact_credentials(text: str) -> str:
    for value in _SECRET_VALUES:
        text = text.replace(value, "[MASKED]")
    return text


class _RedactedDiagnosticHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        print(redact_credentials(self.format(record)), file=sys.stderr, flush=True)


logger = logging.getLogger("infrapulse.integration")
logger.setLevel(logging.ERROR)
logger.addHandler(_RedactedDiagnosticHandler())
logger.propagate = False


def run_cli(main: Callable[[], None]) -> None:
    try:
        main()
    except subprocess.CalledProcessError as error:
        logger.exception("Validator subprocess failed")
        code = error.returncode
        if 1 <= code <= 255:
            raise SystemExit(code) from None
        if code < 0 and -code in signal.valid_signals():
            # A process exit cannot express subprocess's negative signal status.
            raise SystemExit(128 - code) from None
        print(
            f"Invalid subprocess return code {code}; using failure status 1",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except Exception:
        logger.exception("Validator failed")
        raise SystemExit(1) from None


class IntegrationProject:
    def __init__(self, kind: str, files: list[str]) -> None:
        self.name = os.environ.get(
            "INFRAPULSE_TEST_PROJECT", f"infrapulse-{kind}-{secrets.token_hex(8)}"
        )
        if not re.fullmatch(rf"infrapulse-{kind}-[a-z0-9-]+", self.name):
            raise ValueError("Unexpected disposable project name")
        self.marker = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / (
            self.name + ".owned"
        )
        self.env = dict(os.environ)
        for key in (
            "POSTGRES_PASSWORD",
            "GRAFANA_ADMIN_PASSWORD",
            "INFRAPULSE_TEST_PASSWORD",
        ):
            self.env[key] = secrets.token_hex(32)
            _SECRET_VALUES.add(self.env[key])
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::add-mask::{self.env[key]}", flush=True)
        self.compose = ["docker", "compose", "--env-file", "/dev/null", "-p", self.name]
        for file in files:
            self.compose.extend(["-f", file])
        self.before: dict[str, str] = {}

    def run(
        self, command: list[str], *, capture: bool = False, source: str | None = None
    ) -> str:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=self.env,
            input=source,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            check=True,
            timeout=900,
        )
        return result.stdout if capture else ""

    def resources(self, resource: str) -> str:
        command = ["docker", resource, "ls", "-q"]
        if resource == "container":
            command.append("-a")
        return self.run(
            [*command, "--filter", f"label=com.docker.compose.project={self.name}"],
            capture=True,
        ).strip()

    def snapshot(self) -> dict[str, str]:
        identifiers = self.run(["docker", "ps", "-aq"], capture=True).split()
        return {
            identifier: self.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Id}} {{.State.StartedAt}} {{.RestartCount}} {{.State.Status}}",
                    identifier,
                ],
                capture=True,
            )
            for identifier in identifiers
        }

    def __enter__(self) -> Self:
        for resource in ("container", "network", "volume"):
            if self.resources(resource):
                raise RuntimeError(f"Refusing pre-existing project {self.name}")
        self.before = self.snapshot()
        with self.marker.open("x") as marker:
            marker.write(self.name)
        print(f"Disposable project: {self.name}", flush=True)
        signal.signal(signal.SIGTERM, self.terminate)
        return self

    @staticmethod
    def terminate(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    def cleanup(self) -> None:
        if not self.marker.exists():
            print(f"No owned project to clean: {self.name}", flush=True)
            return
        if self.marker.read_text() != self.name:
            raise RuntimeError("Invalid project ownership marker")
        self.run(
            [*self.compose, "down", "--volumes", "--remove-orphans", "--timeout", "30"]
        )
        for resource in ("container", "network", "volume"):
            if self.resources(resource):
                raise RuntimeError(f"Disposable {resource} remains")
        self.marker.unlink()
        print(f"Cleanup verified: {self.name}", flush=True)

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        secondary_errors: list[Exception] = []
        try:
            self.cleanup()
        except Exception as cleanup_error:
            logger.exception("Project cleanup failed")
            secondary_errors.append(cleanup_error)
        try:
            after = self.snapshot()
            if any(after.get(key) != value for key, value in self.before.items()):
                raise RuntimeError("Pre-existing containers changed")
            print(
                "Pre-existing container IDs/start times/restarts/state unchanged",
                flush=True,
            )
        except Exception as inspection_error:
            logger.exception("Post-cleanup inspection failed")
            secondary_errors.append(inspection_error)
        if error is None and secondary_errors:
            raise secondary_errors[0]

    def allow_target(self, network: str) -> None:
        self.run([*self.compose, "up", "-d", "--wait", "monitoring-target"])
        identifier = self.run(
            [*self.compose, "ps", "-q", "monitoring-target"], capture=True
        ).strip()
        addresses = json.loads(
            self.run(
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
        address = ipaddress.ip_address(addresses[f"{self.name}_{network}"]["IPAddress"])
        if address.version != 4 or not address.is_private:
            raise RuntimeError("Expected private IPv4 controlled target")
        self.env["INFRAPULSE_TEST_NETWORKS"] = json.dumps([f"{address}/32"])
        print(f"Controlled target authorized only at {address}/32", flush=True)

    def assert_internal(self, network: str) -> None:
        internal = self.run(
            [
                "docker",
                "network",
                "inspect",
                "--format",
                "{{.Internal}}",
                f"{self.name}_{network}",
            ],
            capture=True,
        ).strip()
        if internal != "true":
            raise RuntimeError(f"Expected an internal {network} network")
