import logging
import sys
from typing import Any

from nonebot.log import logger

log = logger.bind(ryxbot_event=True)


class LibraryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Do not forward third-party URLs, message bodies, exceptions or formatted text.
        logger.log(record.levelno, "library event")


def privacy_filter(record: dict[str, Any]) -> bool:
    record["exception"] = None
    if record["extra"].get("ryxbot_event"):
        return True
    if record["level"].no >= logging.WARNING:
        record["message"] = "framework/library event (details suppressed for privacy)"
        return True
    return False


def configure_logging(level: str = "INFO", sink: Any = None) -> None:
    logger.remove()
    logger.add(
        sys.stderr if sink is None else sink,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ssZ} | {level} | {message}",
        filter=privacy_filter,
        diagnose=False,
        backtrace=False,
    )
    logging.basicConfig(handlers=[LibraryLogHandler()], level=logging.WARNING, force=True)
    for name in ("httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access"):
        library = logging.getLogger(name)
        library.handlers.clear()
        library.propagate = True
        library.setLevel(logging.WARNING)
