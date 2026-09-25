import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Structured JSON logs with an explicit, bounded field allowlist.

    Only names on these tuples are copied out of the LogRecord, so a log call
    cannot smuggle arbitrary data (a payload, a URL, an id) into the emitted
    JSON simply by passing `extra=`.
    """

    # Authentication outcomes. `auth_subject_hash` is a truncated hash, never a subject.
    _AUTH_FIELDS = ("auth_outcome", "auth_reason", "auth_subject_hash")
    # Page-assembly phases and upstream fetches (SHARED-MIN-001). All values are
    # low-cardinality: a page name, an outcome from a fixed vocabulary, an HTTP
    # status, millisecond durations, and a decoded byte count. Deliberately no
    # resource id, URL, claim, or payload content.
    _PAGE_FIELDS = (
        "page",
        "page_outcome",
        "page_status",
        "page_duration_ms",
        "page_identity_duration_ms",
        "page_satellites_duration_ms",
        "upstream_resource",
        "upstream_outcome",
        "upstream_duration_ms",
        "upstream_bytes",
    )

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (*self._AUTH_FIELDS, *self._PAGE_FIELDS):
            value = getattr(record, field, None)
            if value is not None:
                event[field] = value
        return json.dumps(event, separators=(",", ":"), default=str)


def setup_logging(logger: logging.Logger) -> None:
    # Create a root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Create a console handler
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Create a formatter
    formatter = JsonFormatter()

    # Add the formatter to the console handler
    console_handler.setFormatter(formatter)

    # Add the console handler to the root logger and uvicorn logger
    root_logger.addHandler(console_handler)
    logger.addHandler(console_handler)
