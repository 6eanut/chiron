"""Structured logging helpers for CHIRON.

Centralizes logger creation so every module emits a consistent ``chiron.*``
namespace, with optional JSON formatting for the orchestrator.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``chiron`` namespace."""
    return logging.getLogger(f"chiron.{name}")


def configure_logging(level: str = "INFO", *, json_fmt: bool = False) -> None:
    """Configure the root ``chiron`` logger once.

    ``json_fmt`` emits single-line JSON records (useful when the CLI is run
    by an automated pipeline that parses output).
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("chiron")
    root.setLevel(numeric)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if json_fmt:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.propagate = False


class _JsonFormatter(logging.Formatter):
    """Serialize log records to a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)