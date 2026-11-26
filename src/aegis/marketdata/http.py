"""Resilient HTTP client shared by all market-data adapters.

Features: per-source minimum spacing (rate limit), retries with exponential backoff + jitter on
429/5xx/timeouts (honours Retry-After), TTL cache, error classification, latency and health stats.
Never fabricates data: after exhausting retries it raises `SourceError`.
"""

from __future__ import annotations

import datetime as dt
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from aegis.core import logging as alog
from aegis.core.clock import UTC

log = alog.get("aegis.http")


class SourceError(Exception):
    def __init__(self, source: str, kind: str, message: str, status: int | None = None):
        super().__init__(f"[{source}] {kind}: {message}")
        self.source = source
        self.kind = kind  # rate_limited|timeout|network|http_5xx|http_4xx|parse|not_found|rpc_error
        self.status = status

    @property
    def transient(self) -> bool:
        return self.kind in ("rate_limited", "timeout", "network", "http_5xx", "rpc_rate_limited")


@dataclass
class SourceStats:
    source: str
    calls: int = 0
    errors: int = 0
    rate_limited: int = 0
    cache_hits: int = 0
    consecutive_errors: int = 0
    latency_ms_ewma: float | None = None
    last_ok_at: dt.datetime | None = None
    last_error_at: dt.datetime | None = None
    last_error: str | None = None
    latencies: list[float] = field(default_factory=list)

    def ok(self, latency_ms: float) -> None:
        self.calls += 1
        self.consecutive_errors = 0
        self.last_ok_at = dt.datetime.now(UTC)
        self.latency_ms_ewma = latency_ms if self.latency_ms_ewma is None else 0.8 * self.latency_ms_ewma + 0.2 * latency_ms
        self.latencies = (self.latencies + [latency_ms])[-200:]

    def fail(self, err: SourceError) -> None:
        self.calls += 1
        self.errors += 1
        self.consecutive_errors += 1
        self.last_error_at = dt.datetime.now(UTC)
        self.last_error = str(err)[:500]
        if err.kind in ("rate_limited", "rpc_rate_limited"):
            self.rate_limited += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": self.source, "calls": self.calls, "errors": self.errors, "rate_limited": self.rate_limited,
            "cache_hits": self.cache_hits, "consecutive_errors": self.consecutive_errors,
            "latency_ms_ewma": None if self.latency_ms_ewma is None else Decimal(str(round(self.latency_ms_ewma, 1))),
            "last_ok_at": self.last_ok_at, "last_error_at": self.last_error_at, "last_error": self.last_error,
        }


@dataclass(frozen=True)
class CallMeta:
    url: str
    status: int
    latency_ms: float
    from_cache: bool
    fetched_at: dt.datetime


class ResilientHttp:
    def __init__(
        self,
        source: str,
        base_url: str,
        *,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        backoff_cap_s: float = 60.0,
        timeout_s: float = 20.0,
        headers: dict[str, str] | None = None,
        user_agent: str = "aegis-research/0.2",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        cache_size: int = 2048,
    ):
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self._sleep = sleep
        self._mono = monotonic
        self._last = -1e9
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, Any, CallMeta]] = OrderedDict()
        self._cache_size = cache_size
        self.stats = SourceStats(source)
        h = {"Accept": "application/json", "User-Agent": user_agent}
        h.update(headers or {})
        self._client = httpx.Client(timeout=timeout_s, headers=h, transport=transport, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def _wait_turn(self) -> None:
        with self._lock:
            delta = self._mono() - self._last
            if delta < self.min_interval_s:
                self._sleep(self.min_interval_s - delta)
            self._last = self._mono()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(self.backoff_cap_s, max(0.0, retry_after))
        base = min(self.backoff_cap_s, self.backoff_base_s * (2**attempt))
        return base * (0.5 + random.random() / 2)  # jitter

    def request_json(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                     json_body: Any = None, cache_ttl_s: float = 0.0,
                     validate: Callable[[Any], None] | None = None) -> tuple[Any, CallMeta]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        key = f"{method} {url} {sorted((params or {}).items())} {json_body!r}"
        if cache_ttl_s > 0:
            hit = self._cache.get(key)
            if hit and hit[0] > self._mono():
                self.stats.cache_hits += 1
                return hit[1], CallMeta(url, hit[2].status, 0.0, True, hit[2].fetched_at)
        last_err: SourceError | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            t0 = self._mono()
            retry_after: float | None = None
            try:
                resp = self._client.request(method, url, params=params, json=json_body)
                latency = (self._mono() - t0) * 1000
                if resp.status_code == 429:
                    ra = resp.headers.get("retry-after")
                    retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                    raise SourceError(self.source, "rate_limited", "HTTP 429", 429)
                if resp.status_code == 404:
                    raise SourceError(self.source, "not_found", f"HTTP 404 {url}", 404)
                if resp.status_code >= 500:
                    raise SourceError(self.source, "http_5xx", f"HTTP {resp.status_code}", resp.status_code)
                if resp.status_code >= 400:
                    raise SourceError(self.source, "http_4xx", f"HTTP {resp.status_code}: {resp.text[:200]}",
                                      resp.status_code)
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SourceError(self.source, "parse", f"invalid JSON: {exc}", resp.status_code) from exc
                if validate is not None:
                    validate(data)
                meta = CallMeta(url, resp.status_code, latency, False, dt.datetime.now(UTC))
                self.stats.ok(latency)
                if cache_ttl_s > 0:
                    self._cache[key] = (self._mono() + cache_ttl_s, data, meta)
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                return data, meta
            except httpx.TimeoutException as exc:
                last_err = SourceError(self.source, "timeout", str(exc) or "timeout")
            except httpx.TransportError as exc:
                last_err = SourceError(self.source, "network", str(exc) or type(exc).__name__)
            except SourceError as exc:
                last_err = exc
            self.stats.fail(last_err)
            alog.event(log, "source_call_failed", 30, source=self.source, kind=last_err.kind, attempt=attempt,
                       url=url)
            if not last_err.transient or attempt == self.max_retries:
                break
            self._sleep(self._backoff(attempt, retry_after))
        assert last_err is not None
        raise last_err

    def get_json(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> tuple[Any, CallMeta]:
        return self.request_json("GET", path, params=params, **kw)

    def post_json(self, path: str, body: Any, **kw: Any) -> tuple[Any, CallMeta]:
        return self.request_json("POST", path, json_body=body, **kw)
