import json
import logging
import logging.config
from datetime import UTC, datetime

from opentelemetry.trace import get_current_span


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = get_current_span().get_span_context()
        correlation = (
            {
                "trace_id": format(context.trace_id, "032x"),
                "span_id": format(context.span_id, "016x"),
            }
            if context.is_valid
            else {}
        )
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                **correlation,
                **{
                    key: getattr(record, key)
                    for key in (
                        "run_id",
                        "service_id",
                        "check_status",
                        "response_time_ms",
                        "http_method",
                        "http_route",
                        "http_status_code",
                    )
                    if hasattr(record, key)
                },
            }
        )


def configure_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "null": {"class": "logging.NullHandler"},
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                },
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "opentelemetry": {"handlers": ["null"], "propagate": False},
                "urllib3": {"handlers": ["null"], "propagate": False},
                "httpx": {"handlers": ["null"], "propagate": False},
                "httpcore": {"handlers": [], "propagate": True, "level": "WARNING"},
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
        }
    )
