"""Stable hashing of configurations / decision inputs for versioning and reproducibility."""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dt.datetime):
        return o.isoformat()
    if isinstance(o, enum.Enum):
        return o.value
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, set | frozenset):
        return sorted(o)
    raise TypeError(f"not serialisable: {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, default=_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(obj: Any, length: int = 16) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()[:length]
