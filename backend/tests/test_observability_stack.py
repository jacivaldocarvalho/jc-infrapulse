"""Real stack checks; invoked only in the isolated observability Compose project."""

import os
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INFRAPULSE_OBSERVABILITY_INTEGRATION") != "1",
    reason="Requires the isolated observability Compose project",
)


def eventually(check: Callable[[], Any], timeout: int = 90) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = check()
            assert result
            return result
        except (AssertionError, httpx.HTTPError, KeyError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def test_real_observability_stack() -> None:
    with httpx.Client(timeout=5, trust_env=False) as client:

        def get(url: str) -> Any:
            response = client.get(url)
            response.raise_for_status()
            return response.json()

        eventually(lambda: client.get("http://api:8000/ready").status_code == 200)
        eventually(lambda: client.get("http://otel-collector:13133/").status_code == 200)
        eventually(lambda: client.get("http://tempo:3200/ready").status_code == 200)
        for target in ("api:9000", "worker:9001"):
            assert client.get(f"http://{target}/metrics").status_code == 200
        response = client.post(
            "http://api:8000/api/v1/services",
            json={
                "name": "Integration fixture",
                "url": "http://monitoring-target:8080/",
                "check_interval_seconds": 10,
                "timeout_seconds": 2,
            },
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        try:

            def persisted() -> bool:
                data = get(f"http://api:8000/api/v1/services/{identifier}/status")
                return bool(data["last_status"] == "UP")

            eventually(persisted)
            targets = eventually(
                lambda: (
                    [
                        t
                        for t in get("http://prometheus:9090/api/v1/targets")["data"][
                            "activeTargets"
                        ]
                        if t["labels"]["job"].startswith("infrapulse-") and t["health"] == "up"
                    ]
                    if len(
                        [
                            t
                            for t in get("http://prometheus:9090/api/v1/targets")["data"][
                                "activeTargets"
                            ]
                            if t["labels"]["job"].startswith("infrapulse-") and t["health"] == "up"
                        ]
                    )
                    == 2
                    else []
                )
            )
            print("SCRAPE", [(t["labels"]["job"], t["health"], t["scrapeUrl"]) for t in targets])
            for expression in (
                'sum(http_server_requests_total{job="infrapulse-api"}) > 0',
                'infrapulse_checks_total{job="infrapulse-worker",status="UP"} > 0',
                'infrapulse_check_results_persisted_total{job="infrapulse-worker",status="UP"} > 0',
                'infrapulse_last_up_check_timestamp_seconds{job="infrapulse-worker"} > 0',
                "infrapulse_scheduler_last_healthy_cycle_timestamp_seconds"
                '{job="infrapulse-worker"} > 0',
            ):

                def query(expression: str = expression) -> Any:
                    data = client.get(
                        "http://prometheus:9090/api/v1/query", params={"query": expression}
                    )
                    return data.json()["data"]["result"]

                result = eventually(query)
                print("PROMQL", expression, result[0]["value"][1])
            for service in ("infrapulse-api", "infrapulse-worker"):

                def search(service: str = service) -> Any:
                    response = client.get(
                        "http://tempo:3200/api/search",
                        params={"q": f'{{resource.service.name = "{service}"}}'},
                    )
                    response.raise_for_status()
                    return response.json()["traces"]

                traces = eventually(search)
                trace_id = traces[0]["traceID"]
                response = client.get(
                    f"http://tempo:3200/api/traces/{trace_id}",
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                assert "resourceSpans" in response.json() or "batches" in response.json()
                assert "monitoring-target" not in response.text
                print("TEMPO", service, trace_id, response.status_code)
            password = os.environ["GRAFANA_ADMIN_PASSWORD"]

            def grafana(path: str) -> Any:
                response = client.get(
                    "http://grafana:3000" + path,
                    auth=("admin", password),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()

            eventually(lambda: grafana("/api/health")["database"] == "ok")
            for uid in ("prometheus", "tempo"):
                assert grafana(f"/api/datasources/uid/{uid}")["uid"] == uid
                if uid == "prometheus":
                    assert grafana(f"/api/datasources/uid/{uid}/health")["status"] == "OK"
                else:
                    # Tempo plugin does not implement backend CheckHealth in Grafana 12.1.
                    # Query a real trace through its configured datasource proxy instead.
                    proxied = grafana(f"/api/datasources/proxy/uid/tempo/api/traces/{trace_id}")
                    assert "batches" in proxied or "resourceSpans" in proxied
                print("DATASOURCE", uid, "OK")
            for name in ("api", "worker", "reliability"):

                def load_dashboard(name: str = name) -> Any:
                    return grafana(f"/api/dashboards/uid/infrapulse-{name}")

                dashboard = eventually(load_dashboard)
                assert dashboard["dashboard"]["panels"]
                for panel in dashboard["dashboard"]["panels"]:
                    for target in panel.get("targets", []):
                        expression = target["expr"].replace("$__rate_interval", "1m")
                        response = client.get(
                            "http://grafana:3000/api/datasources/proxy/uid/prometheus/api/v1/query",
                            params={"query": expression},
                            auth=("admin", password),
                        )
                        response.raise_for_status()
                        assert response.json()["status"] == "success"
                print("DASHBOARD", dashboard["dashboard"]["title"], "all panel queries valid")
            alerts = eventually(
                lambda: [
                    a
                    for a in get("http://alertmanager:9093/api/v2/alerts")
                    if a["labels"]["alertname"] == "InfraPulseIntegrationProbe"
                ]
            )
            print("ALERTMANAGER", alerts[0]["labels"]["alertname"], alerts[0]["status"]["state"])
        finally:
            client.delete(f"http://api:8000/api/v1/services/{identifier}").raise_for_status()


@pytest.mark.skipif(
    os.environ.get("INFRAPULSE_COLLECTOR_OUTAGE") != "1",
    reason="Requires intentionally stopped Collector",
)
def test_collector_outage_real_processes() -> None:
    with httpx.Client(timeout=5, trust_env=False) as client:
        assert client.get("http://api:8000/health").status_code == 200
        assert client.get("http://api:8000/ready").status_code == 200
        response = client.post(
            "http://api:8000/api/v1/services",
            json={
                "name": "Collector outage fixture",
                "url": "http://monitoring-target:8080/",
                "check_interval_seconds": 10,
                "timeout_seconds": 2,
            },
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        try:
            eventually(
                lambda: (
                    client.get(f"http://api:8000/api/v1/services/{identifier}/status").json()[
                        "last_status"
                    ]
                    == "UP"
                )
            )
            assert client.get("http://worker:9001/metrics").status_code == 200
            print("COLLECTOR OUTAGE: API ready, worker persisted UP, exporter available")
        finally:
            client.delete(f"http://api:8000/api/v1/services/{identifier}").raise_for_status()
