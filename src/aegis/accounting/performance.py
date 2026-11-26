"""Performance metrics with explicit definitions (see docs/STRATEGY_SPECIFICATION.md §KPIs).

- Net Portfolio Return (NPR) = (NW_T + W - C) / C, over contributions C and withdrawals W.
- TWR: chain-linked sub-period returns, external flow assumed at the START of each sub-period:
      r_i = V_i / (V_{i-1} + F_i) - 1, where F_i is the net external flow booked during period i.
  Neutralises the size/timing of contributions; measures the strategy, not the saver.
- XIRR: money-weighted annual rate solving sum(CF_k / (1+r)^(d_k/365)) = 0, with contributions as
  negative cash flows (investor perspective), withdrawals positive and the terminal liquidable net
  worth as a final positive flow.
- Max drawdown computed on the TWR wealth index (not on net worth, which rises with contributions).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from aegis.core.money import ONE, ZERO


@dataclass(frozen=True)
class ValuePoint:
    t: dt.datetime
    value: Decimal  # liquidable net worth after the period's flows
    flow: Decimal  # net external flow during the period (contrib - withdrawals)


def twr_index(points: Sequence[ValuePoint]) -> list[tuple[dt.datetime, Decimal]]:
    """Wealth index starting at 1 from the first point with a positive invested base."""
    out: list[tuple[dt.datetime, Decimal]] = []
    idx = ONE
    prev: Decimal | None = None
    for p in points:
        if prev is None:
            if p.value > ZERO:
                prev = p.value
                out.append((p.t, idx))
            continue
        base = prev + p.flow
        if base > ZERO:
            idx = idx * (p.value / base)
        out.append((p.t, idx))
        prev = p.value
    return out


def twr(points: Sequence[ValuePoint]) -> Decimal | None:
    ix = twr_index(points)
    if len(ix) < 2:
        return None
    return ix[-1][1] - ONE


def annualize(total_return: Decimal, start: dt.datetime, end: dt.datetime) -> Decimal | None:
    days = (end - start).total_seconds() / 86400
    if days < 1 or total_return <= -ONE:
        return None
    return Decimal(str((1 + float(total_return)) ** (365.0 / days) - 1))


def max_drawdown(index: Sequence[tuple[dt.datetime, Decimal]]) -> Decimal:
    peak = None
    mdd = ZERO
    for _, v in index:
        peak = v if peak is None or v > peak else peak
        if peak and peak > ZERO:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def xirr(flows: Sequence[tuple[dt.datetime, Decimal]], *, tol: float = 1e-10, max_iter: int = 200) -> Decimal | None:
    """Robust XIRR: bracketed bisection on [-0.9999, 1e6] (safe for extreme asymmetric outcomes)."""
    if len(flows) < 2:
        return None
    amounts = [float(a) for _, a in flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None
    t0 = min(t for t, _ in flows)
    years = [(t - t0).total_seconds() / (365.0 * 86400) for t, _ in flows]

    def npv(r: float) -> float:
        total = 0.0
        for a, y in zip(amounts, years, strict=True):
            try:
                total += a / (1.0 + r) ** y
            except OverflowError:
                return math.inf if a > 0 else -math.inf
        return total

    lo, hi = -0.9999, 1.0
    f_lo = npv(lo)
    f_hi = npv(hi)
    while f_lo * f_hi > 0 and hi < 1e6:
        hi *= 4
        f_hi = npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return Decimal(str(round(mid, 10)))
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return Decimal(str(round((lo + hi) / 2, 10)))


def net_portfolio_return(net_worth: Decimal, contributions: Decimal, withdrawals: Decimal) -> Decimal | None:
    if contributions <= ZERO:
        return None
    return (net_worth + withdrawals - contributions) / contributions


def dca_benchmark(
    contributions: Sequence[tuple[dt.datetime, Decimal]],
    prices: Sequence[tuple[dt.datetime, Decimal]],
    final_price: Decimal,
    cost_pct: Decimal = Decimal("0.001"),
) -> Decimal | None:
    """Value of buying an asset (BTC/SOL) with the same contribution schedule.

    Uses the last known price at or before each contribution (no look-ahead)."""
    if not prices:
        return None
    ps = sorted(prices)
    units = ZERO
    j = 0
    for t, amt in sorted(contributions):
        while j + 1 < len(ps) and ps[j + 1][0] <= t:
            j += 1
        if ps[j][0] > t:
            return None  # no price known yet at contribution time
        units += amt * (ONE - cost_pct) / ps[j][1]
    return units * final_price * (ONE - cost_pct)
