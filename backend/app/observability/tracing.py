"""Explicit spans with no exception capture, query text or destination attributes."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer

from app.core.config import Settings
from app.observability.processor import BoundedSpanProcessor


@contextmanager
def operation(tracer: Tracer, name: str) -> Iterator[Span]:
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except BaseException:
            span.set_status(StatusCode.ERROR)
            logging.getLogger(__name__).warning("operation_failed")
            raise


class Telemetry:
    def __init__(self, settings: Settings, role: str, exporter: SpanExporter | None = None) -> None:
        self.provider: TracerProvider | None = None
        if settings.telemetry_enabled or exporter is not None:
            self.provider = TracerProvider(
                resource=Resource(
                    {
                        "service.name": f"infrapulse-{role}",
                        "service.version": version("infrapulse-api"),
                        "deployment.environment": settings.deployment_environment,
                        "deployment.environment.name": settings.deployment_environment,
                    }
                ),
                sampler=ParentBased(TraceIdRatioBased(settings.telemetry_sample_ratio)),
                shutdown_on_exit=False,
            )
            self.provider.add_span_processor(
                BoundedSpanProcessor(
                    exporter
                    or OTLPSpanExporter(
                        endpoint=settings.telemetry_endpoint,
                        timeout=settings.telemetry_timeout_seconds,
                    ),
                )
            )
        self.tracer = (
            self.provider.get_tracer("infrapulse") if self.provider else trace.NoOpTracer()
        )

    async def close(self) -> None:
        if self.provider is not None:
            # Owned processor drains with a two-second deadline plus one in-flight export.
            await asyncio.to_thread(self.provider.shutdown)


ROOT_CONTEXT = Context()
SERVER_KIND = SpanKind.SERVER
