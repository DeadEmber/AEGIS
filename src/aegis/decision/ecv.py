"""Expected Capital Velocity (ECV) — EXPERIMENTAL indicator.

    ECV = E[net profit] / (capital committed × E[duration in days])        unit: 1/day

- Open positions: capital committed = CURRENT net liquidable value (opportunity cost of staying in),
  not the historical cost; E[net profit] = E[future net liquidation value] − current value.
- Candidates: capital committed = the all-in ticket (includes the recoverable rent deposit, which is
  immobilised while the position lives).

The value is produced only from an explicit estimate source:
- `model`: a registered, validated champion model (none exists yet);
- `empirical_baseline`: population statistics with enough observations (identical for every
  candidate, so it carries no ranking information — reported for portfolio-level analysis only).
Otherwise the result is `unavailable`. No fictitious probabilities are ever used.

Planned extensions (MODEL_REGISTRY / ROADMAP experiment E1): outcome probabilities, expected gain and
loss magnitudes, temporal uncertainty (quantiles / survival), costs, liquidity, partial capital
recoveries and the opportunity cost of alternative candidates. ECV is never an exclusive criterion.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Connection

from aegis.core.money import ZERO
from aegis.research.temporal import Episode, empirical_duration
from aegis.storage import schema as s


@dataclass(frozen=True)
class OutcomeEstimate:
    expected_net_profit_per_eur: float | None  # E[net profit] / capital committed
    expected_duration_days: float | None
    duration_low_days: float | None = None
    duration_high_days: float | None = None
    source: str = "none"  # none | empirical_baseline | model
    n: int = 0
    note: str = ""


@dataclass(frozen=True)
class ECVResult:
    value_per_day: float | None
    status: str  # unavailable | experimental | model
    capital_basis: str  # liquidable_value | ticket
    capital_eur: Decimal | None
    expected_net_profit_eur: float | None
    expected_duration_days: float | None
    note: str = ""

    def as_score(self) -> dict[str, object]:
        return {"ECV": None if self.value_per_day is None else round(self.value_per_day, 6),
                "ECV_status": self.status, "ECV_basis": self.capital_basis}


def ecv_value(expected_net_profit: float, capital: float, duration_days: float) -> float | None:
    if capital <= 0 or duration_days <= 0 or not math.isfinite(expected_net_profit):
        return None
    return expected_net_profit / (capital * duration_days)


def compute_ecv(capital: Decimal | None, est: OutcomeEstimate, basis: str) -> ECVResult:
    if capital is None or capital <= ZERO:
        return ECVResult(None, "unavailable", basis, capital, None, None, "no capital reference")
    if est.source == "none" or est.expected_net_profit_per_eur is None or est.expected_duration_days is None:
        return ECVResult(None, "unavailable", basis, capital, None, None, est.note or "no reliable estimate")
    profit = est.expected_net_profit_per_eur * float(capital)
    v = ecv_value(profit, float(capital), est.expected_duration_days)
    status = "model" if est.source == "model" else "experimental"
    return ECVResult(v, status if v is not None else "unavailable", basis, capital, profit, est.expected_duration_days,
                     est.note)


class NoEstimates:
    """Default provider: no validated model exists, so every ECV is `unavailable`."""

    def candidate(self, token_id: int) -> OutcomeEstimate:
        return OutcomeEstimate(None, None, note="no validated outcome/duration model")

    def position(self, position_id: int) -> OutcomeEstimate:
        return OutcomeEstimate(None, None, note="no conditional model for open positions")


class EmpiricalBaseline(NoEstimates):
    """Population-level baseline from CLOSED positions (+ Kaplan-Meier durations including open ones).

    Known biases (documented, reason for 'experimental'): closed positions over-represent fast exits;
    the same value applies to every candidate (no discrimination); paper results only."""

    def __init__(self, conn: Connection, as_of: dt.datetime, episodes: list[Episode], min_closed: int = 30):
        closed = conn.execute(select(s.positions.c.realized_pnl_eur, s.positions.c.invested_eur_total)
                              .where(s.positions.c.closed_at.is_not(None), s.positions.c.closed_at <= as_of)).all()
        self.n = len(closed)
        dur = empirical_duration(episodes, "investment_duration", min_events=min_closed)
        if self.n < min_closed or dur.status != "empirical_baseline" or dur.median_hours is None:
            self._est = OutcomeEstimate(None, None, n=self.n,
                                        note=f"insufficient data ({self.n} closed positions, duration {dur.status})")
            return
        rets = [float(r.realized_pnl_eur / r.invested_eur_total) for r in closed if r.invested_eur_total > 0]
        self._est = OutcomeEstimate(
            statistics.fmean(rets), dur.median_hours / 24, (dur.ci_low_hours or 0) / 24 or None,
            (dur.ci_high_hours or 0) / 24 or None, "empirical_baseline", self.n,
            "population baseline: identical for all candidates; biased towards fast exits")

    def candidate(self, token_id: int) -> OutcomeEstimate:
        return self._est
