"""One RED observation and one safe server span per functional request."""

import logging
import time
from typing import Any

from opentelemetry.trace import Link, StatusCode, get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.tracing import ROOT_CONTEXT, SERVER_KIND

EXCLUDED = {"/health", "/ready", "/metrics"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXCLUDED:
            await self.app(scope, receive, send)
            return
        state: Any = scope["app"].state
        metrics = state.metrics
        method = scope["method"] if scope["method"] in METHODS else "OTHER"
        # Accept only traceparent, discard baggage/tracestate and remote sampling decisions.
        headers = {
            k.decode("ascii").lower(): v.decode("ascii", errors="ignore")
            for k, v in scope["headers"]
            if k.lower() == b"traceparent"
        }
        remote = get_current_span(
            TraceContextTextMapPropagator().extract(headers)
        ).get_span_context()
        links = [Link(remote)] if remote.is_valid else []
        started, status = time.monotonic(), 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with state.telemetry.tracer.start_as_current_span(
            "http.request",
            context=ROOT_CONTEXT,
            kind=SERVER_KIND,
            links=links,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                await self.app(scope, receive, capture)
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                span.update_name(f"{method} {route}")
                span.set_attributes(
                    {
                        "http.request.method": method,
                        "http.route": route,
                        "http.response.status_code": status,
                    }
                )
                if status >= 500:
                    span.set_status(StatusCode.ERROR)
                logging.getLogger(__name__).info(
                    "http_request_completed",
                    extra={"http_method": method, "http_route": route, "http_status_code": status},
                )
                metrics.requests.labels(method, route, str(status)).inc()
                metrics.request_duration.labels(method, route, str(status)).observe(
                    time.monotonic() - started
                )
