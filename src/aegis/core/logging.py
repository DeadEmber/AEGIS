"""Structured JSON logging. Secrets are never logged: values of keys that look sensitive are masked."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_SENSITIVE = ("key", "secret", "token", "password", "private", "seed", "mnemonic", "authorization")


def _mask(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE) and k.lower() not in ("token_address", "token_id"):
            out[k] = "***"
        else:
            out[k] = v
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(_mask(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)


def event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})
