"""Owned bounded trace queue with a finite drain deadline and no SDK global threads."""

import time
from collections import deque
from threading import Condition, Thread

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter


class BoundedSpanProcessor(SpanProcessor):
    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter
        self.queue: deque[ReadableSpan] = deque(maxlen=256)
        self.condition = Condition()
        self.stopping = False
        self.exporting = False
        self.deadline = float("inf")
        self.thread = Thread(target=self._run, name="infrapulse-traces", daemon=True)
        self.thread.start()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        if not span.context or not span.context.trace_flags.sampled:
            return
        with self.condition:
            if not self.stopping:
                self.queue.append(span)
                self.condition.notify_all()

    def _run(self) -> None:
        while True:
            with self.condition:
                if self.stopping and (not self.queue or time.monotonic() >= self.deadline):
                    self.queue.clear()
                    self.condition.notify_all()
                    return
                if not self.queue:
                    self.condition.wait(0.5)
                    continue
                batch = [self.queue.popleft() for _ in range(min(64, len(self.queue)))]
                self.exporting = True
            try:
                self.exporter.export(batch)
            except Exception:
                # Exporter failures never log exception text or reach application callers.
                pass
            finally:
                with self.condition:
                    self.exporting = False
                    self.condition.notify_all()

    def force_flush(self, timeout_millis: int = 2000) -> bool:
        deadline = time.monotonic() + timeout_millis / 1000
        with self.condition:
            self.condition.notify_all()
            while self.queue or self.exporting:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def shutdown(self) -> None:
        with self.condition:
            self.stopping = True
            self.deadline = time.monotonic() + 2
            self.condition.notify_all()
        # OTLP request deadline <= 2s; drain checks deadline before each next batch.
        self.thread.join(4.1)
        self.exporter.shutdown()
