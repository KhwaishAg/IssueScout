"""JSON logging with a correlation ID on every line (FR-OPS-05, NFR-OBS-03).

The correlation ID lives in a context variable, so code deep in the pipeline logs it
without having it passed through every function signature.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from structlog.typing import EventDict, WrappedLogger

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def correlation_id(value: str) -> Iterator[None]:
    """Bind ``value`` (normally the webhook delivery ID) for the enclosed block."""
    token = _correlation_id.set(value)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def _add_correlation_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    event_dict["correlation_id"] = _correlation_id.get()
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Send structlog and stdlib logging to stdout as one JSON object per line."""
    shared: list[structlog.typing.Processor] = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        # No file argument: each logger looks up the current sys.stdout when it is created, so
        # a stream replaced later (a test runner, a redirected service) is never a closed file.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
