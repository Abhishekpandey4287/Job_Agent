"""Structured logging configuration."""

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(log_level: str = "INFO", log_dir: Path | None = None) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "job_agent.log")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(file_handler)

    structlog.configure(
        processors=processors + [structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
