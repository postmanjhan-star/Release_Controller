import logging
import sys

from app.core.redaction import redact_log


class PrivacyFormatter(logging.Formatter):
    """Wrap a handler's format so third-party records and tracebacks are safe."""

    def __init__(self, formatter: logging.Formatter | None = None, *, secrets=()) -> None:
        super().__init__()
        self.formatter = formatter or logging.Formatter()
        self.secrets = tuple(value for value in secrets if value)

    def format(self, record: logging.LogRecord) -> str:
        return redact_log(self.formatter.format(record), self.secrets)


def configure_logging(log_level: str, *, stream=None, secrets=()) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=stream if stream is not None else sys.stdout,
        force=True,
    )
    # Uvicorn can own handlers that do not propagate to the root logger.
    # Wrap them as well, retaining its access-log format and stderr routing.
    handlers = set(logging.getLogger().handlers)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        handlers.update(logging.getLogger(name).handlers)
    for handler in handlers:
        formatter = handler.formatter
        if isinstance(formatter, PrivacyFormatter):
            formatter = formatter.formatter
        handler.setFormatter(PrivacyFormatter(formatter, secrets=secrets))
