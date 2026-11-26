"""Data-quality checks: staleness and cross-provider consistency."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from aegis.marketdata.models import PoolObservation


@dataclass(frozen=True)
class QualityIssue:
    kind: str
    severity: str  # info|warn|error
    details: dict[str, str]


def is_stale(observed_at: dt.datetime | None, now: dt.datetime, max_age: dt.timedelta) -> bool:
    return observed_at is None or now - observed_at > max_age


def cross_check(primary: PoolObservation, secondary: PoolObservation, *, max_price_dev_pct: Decimal = Decimal(5),
                max_liq_dev_pct: Decimal = Decimal(35)) -> list[QualityIssue]:
    """Compare two providers' view of the SAME pool.

    Pool-level liquidity (GT `reserve_in_usd`, DS `liquidity.usd`) is two-sided and agreed within 0.3%
    on 2026-11-26. Token-level fields (GT `total_reserve_in_usd`, Jupiter `liquidity`) are ~one-sided
    and must not be compared with pool liquidity. Tolerances remain wide because snapshots are not
    simultaneous."""
    issues: list[QualityIssue] = []
    if primary.pool_address != secondary.pool_address:
        return issues
    for name, a, b, tol in (
        ("price", primary.price_usd, secondary.price_usd, max_price_dev_pct),
        ("liquidity", primary.liquidity_usd, secondary.liquidity_usd, max_liq_dev_pct),
    ):
        if a is None or b is None or a <= 0 or b <= 0:
            issues.append(QualityIssue(f"{name}_unverifiable", "info", {"a": str(a), "b": str(b)}))
            continue
        dev = abs(a - b) / min(a, b) * 100
        if dev > tol:
            issues.append(QualityIssue(f"{name}_divergence", "warn",
                                       {"a": str(a), "b": str(b), "dev_pct": f"{dev:.2f}",
                                        "sources": f"{primary.source}/{secondary.source}"}))
    return issues
