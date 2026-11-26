"""Capital Allocation Engine.

- The 100 EUR monthly contribution is a MAXIMUM budget, not an obligation to invest.
- Pacing spreads purchases over the month (daily cap = remaining month budget / remaining days ×
  pacing multiplier) so that entries sample many market regimes instead of the first hour of the month.
- Recycled capital (sale proceeds) is reinvested at most `recycled_daily_fraction` of its balance
  per day. Reserves (profit, tax) are never spendable.
- Ticket slots are split across buckets (exploration 40% / selection 45% / second chance 15%).
  Unused slots flow to exploration first so selection never crowds out mass diversification.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from aegis.accounting.ledger import Account, Ledger
from aegis.config import RiskLimits, StrategyConfig
from aegis.core.clock import ensure_utc
from aegis.core.money import ZERO
from aegis.storage import schema as s

BUCKETS = ("exploration", "selection", "second_chance")


@dataclass
class Budget:
    as_of: dt.datetime
    month_budget: Decimal
    spent_month_investment: Decimal
    spent_today_investment: Decimal
    spent_today_recycled: Decimal
    investment_available_today: Decimal
    recycled_available_today: Decimal
    regime_multiplier: Decimal
    slots: dict[str, int] = field(default_factory=dict)

    @property
    def total_available(self) -> Decimal:
        return self.investment_available_today + self.recycled_available_today

    @property
    def total_slots(self) -> int:
        return sum(self.slots.values())


def _spent(conn: Connection, account: Account, start: dt.datetime, end: dt.datetime) -> Decimal:
    rows = conn.execute(
        select(s.ledger_postings.c.amount_eur)
        .join(s.ledger_transactions, s.ledger_transactions.c.id == s.ledger_postings.c.transaction_id)
        .where(and_(s.ledger_postings.c.account == str(account), s.ledger_transactions.c.kind == "buy",
                    s.ledger_transactions.c.occurred_at >= start, s.ledger_transactions.c.occurred_at < end))
    )
    return -sum((r.amount_eur for r in rows), ZERO)


def split_slots(n: int, cfg: StrategyConfig) -> dict[str, int]:
    w = cfg.capital.buckets
    raw = {"exploration": w.exploration * n, "selection": w.selection * n, "second_chance": w.second_chance * n}
    out = {k: int(v.to_integral_value(rounding=ROUND_DOWN)) for k, v in raw.items()}
    rest = n - sum(out.values())
    for k in sorted(raw, key=lambda k: (raw[k] - out[k], k == "exploration"), reverse=True)[:rest]:
        out[k] += 1
    return out


def reassign_unused(slots: dict[str, int], demand: dict[str, int]) -> dict[str, int]:
    """Give unused slots to exploration first, then selection, then second chance."""
    used = {k: min(slots.get(k, 0), demand.get(k, 0)) for k in BUCKETS}
    spare = sum(slots.values()) - sum(used.values())
    for k in ("exploration", "selection", "second_chance"):
        extra = min(spare, demand.get(k, 0) - used[k])
        if extra > 0:
            used[k] += extra
            spare -= extra
    return used


class AllocationEngine:
    def __init__(self, cfg: StrategyConfig, limits: RiskLimits, ledger: Ledger | None = None):
        self.cfg = cfg
        self.limits = limits
        self.ledger = ledger or Ledger()

    def budget(self, conn: Connection, t: dt.datetime, regime_multiplier: Decimal = Decimal(1)) -> Budget:
        t = ensure_utc(t)
        cap = self.cfg.capital
        month_start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + dt.timedelta(days=1)
        days_in_month = calendar.monthrange(t.year, t.month)[1]
        days_left = days_in_month - t.day + 1
        spent_month = _spent(conn, Account.CASH_INVESTMENT, month_start, day_end)
        spent_before_today = _spent(conn, Account.CASH_INVESTMENT, month_start, day_start)
        spent_today_inv = spent_month - spent_before_today
        spent_today_rec = _spent(conn, Account.CASH_RECYCLED, day_start, day_end)
        inv_balance = self.ledger.balance(conn, Account.CASH_INVESTMENT)
        rec_balance = self.ledger.balance(conn, Account.CASH_RECYCLED)
        remaining_month = max(ZERO, min(cap.monthly_contribution_eur - spent_before_today, inv_balance + spent_today_inv))
        daily_cap = remaining_month / days_left * cap.pacing_multiplier * regime_multiplier
        inv_today = max(ZERO, min(daily_cap - spent_today_inv, inv_balance))
        rec_cap = (rec_balance + spent_today_rec) * cap.recycled_daily_fraction * regime_multiplier
        rec_today = max(ZERO, min(rec_cap - spent_today_rec, rec_balance))
        b = Budget(t, cap.monthly_contribution_eur, spent_month, spent_today_inv, spent_today_rec, inv_today,
                   rec_today, regime_multiplier)
        n = int(((inv_today + rec_today) / cap.ticket_eur).to_integral_value(rounding=ROUND_DOWN))
        n = min(n, self.limits.max_buys_per_cycle)
        b.slots = split_slots(n, self.cfg)
        return b


def market_opportunity_index(ret_h1: list[float], buyer_share_h1: list[float]) -> float | None:
    """MOI (heuristic): breadth of positive 1h returns and median buyer share across the observed universe."""
    if len(ret_h1) < 10 or len(buyer_share_h1) < 10:
        return None
    breadth = sum(1 for r in ret_h1 if r > 0) / len(ret_h1)
    bs = sorted(buyer_share_h1)[len(buyer_share_h1) // 2]
    return max(0.0, min(1.0, 0.5 * breadth + 0.5 * max(0.0, min(1.0, (bs - 0.35) / 0.3))))


def regime_multiplier(moi: float | None) -> Decimal:
    if moi is None:
        return Decimal(1)
    if moi < 0.25:
        return Decimal("0.5")
    if moi > 0.65:
        return Decimal("1.25")
    return Decimal(1)
