"""Capital-efficiency metrics (definitions in docs/STRATEGY_SPECIFICATION.md §9b).

External contributions (cash:investment) and internal reinvestment (cash:recycled) are separated
using the ledger postings of each purchase, so recycling never inflates contributions and partial
funding is measured exactly. All durations are computed from immutable timestamps (lots, disposals,
executions); nothing here is predictive.
"""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from aegis.accounting.ledger import Account, Ledger
from aegis.core.clock import ensure_utc
from aegis.core.money import ZERO
from aegis.storage import schema as s

DAY = 86400.0


def _ratio(a: Decimal, b: Decimal) -> Decimal | None:
    return None if b == ZERO else a / b


def _time_weighted(points: list[tuple[dt.datetime, Decimal]]) -> Decimal | None:
    """Average of a piecewise-constant series sampled at snapshot times."""
    if len(points) < 2:
        return points[0][1] if points else None
    total = ZERO
    span = (points[-1][0] - points[0][0]).total_seconds()
    if span <= 0:
        return points[-1][1]
    for (t0, v0), (t1, _) in itertools.pairwise(points):
        total += v0 * Decimal(str((t1 - t0).total_seconds()))
    return total / Decimal(str(span))


@dataclass(frozen=True)
class CapitalEfficiency:
    as_of: dt.datetime
    period_days: float | None
    contributions_total_eur: Decimal
    purchases_total_eur: Decimal
    purchases_from_contributions_eur: Decimal
    purchases_from_recycled_eur: Decimal
    investments_total: int
    investments_funded_by_recycled: int
    investments_fully_recycled: int
    capital_recycling_ratio: Decimal | None
    recycled_inflows_total_eur: Decimal
    capital_available_for_reinvestment_eur: Decimal
    recycled_pending_settlement_eur: Decimal
    avg_committed_capital_cost_eur: Decimal | None
    avg_committed_capital_liquidable_eur: Decimal | None
    capital_turnover: Decimal | None
    capital_turnover_annualized: Decimal | None
    avg_holding_period_days_realized: float | None
    avg_age_open_lots_days: float | None
    positions_total: int
    positions_capital_recovered: int
    mean_days_to_capital_recovery: float | None
    median_days_to_capital_recovery: float | None
    avg_partial_recovery_fraction: Decimal | None
    avg_decision_to_execution_s: float | None
    avg_execution_to_availability_s: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute(conn: Connection, as_of: dt.datetime, ledger: Ledger | None = None,
            spendable: dict[Account, Decimal] | None = None, pending: Decimal = ZERO) -> CapitalEfficiency:
    as_of = ensure_utc(as_of)
    ledger = ledger or Ledger()
    # --- purchases split by funding source (from ledger postings of 'buy' transactions)
    rows = conn.execute(
        select(s.ledger_transactions.c.id, s.ledger_postings.c.account, s.ledger_postings.c.amount_eur)
        .join(s.ledger_postings, s.ledger_postings.c.transaction_id == s.ledger_transactions.c.id)
        .where(s.ledger_transactions.c.kind == "buy")
    ).all()
    by_tx: dict[int, dict[str, Decimal]] = {}
    for r in rows:
        by_tx.setdefault(r.id, {})
        by_tx[r.id][r.account] = by_tx[r.id].get(r.account, ZERO) + r.amount_eur
    from_inv = from_rec = ZERO
    n_rec = n_full_rec = 0
    for posts in by_tx.values():
        inv = -posts.get(str(Account.CASH_INVESTMENT), ZERO)
        rec = -posts.get(str(Account.CASH_RECYCLED), ZERO)
        from_inv += inv
        from_rec += rec
        if rec > 0:
            n_rec += 1
            if inv == 0:
                n_full_rec += 1
    purchases = from_inv + from_rec
    contributions = -ledger.balance(conn, Account.EQUITY_CONTRIBUTIONS)
    sells = conn.execute(select(s.executions.c.cash_eur).join(s.orders).where(s.orders.c.side == "SELL")).all()
    inflows = sum((r.cash_eur for r in sells), ZERO)

    # --- committed capital (time-weighted over portfolio snapshots)
    snaps = conn.execute(select(s.portfolio_snapshots.c.as_of, s.portfolio_snapshots.c.positions_cost_eur,
                                s.portfolio_snapshots.c.deposits_eur, s.portfolio_snapshots.c.positions_liquidation_eur)
                         .where(s.portfolio_snapshots.c.as_of <= as_of).order_by(s.portfolio_snapshots.c.as_of)).all()
    cost_pts = [(r.as_of, r.positions_cost_eur + r.deposits_eur) for r in snaps]
    liq_pts = [(r.as_of, r.positions_liquidation_eur + r.deposits_eur) for r in snaps]
    avg_cost = _time_weighted(cost_pts)
    avg_liq = _time_weighted(liq_pts)
    period_days = (snaps[-1].as_of - snaps[0].as_of).total_seconds() / DAY if len(snaps) >= 2 else None
    turnover = _ratio(purchases, avg_cost) if avg_cost else None
    turnover_ann = None
    if turnover is not None and period_days and period_days >= 1:
        turnover_ann = turnover * Decimal(str(365.0 / period_days))

    # --- holding periods (cost-weighted)
    disp = conn.execute(select(s.lot_disposals.c.disposed_at, s.lot_disposals.c.cost_basis_eur,
                               s.position_lots.c.acquired_at)
                        .join(s.position_lots, s.position_lots.c.id == s.lot_disposals.c.lot_id)).all()
    w = sum((float(r.cost_basis_eur) for r in disp), 0.0)
    ahp = (sum((r.disposed_at - r.acquired_at).total_seconds() / DAY * float(r.cost_basis_eur) for r in disp) / w
           if w > 0 else None)
    open_lots = conn.execute(select(s.position_lots.c.acquired_at, s.position_lots.c.cost_remaining_eur)
                             .where(s.position_lots.c.qty_remaining > 0)).all()
    wo = sum((float(r.cost_remaining_eur) for r in open_lots), 0.0)
    age_open = (sum((as_of - r.acquired_at).total_seconds() / DAY * float(r.cost_remaining_eur) for r in open_lots) / wo
                if wo > 0 else None)

    # --- capital recovery (full and partial)
    pos = conn.execute(select(s.positions.c.opened_at, s.positions.c.capital_recovered_at,
                              s.positions.c.invested_eur_total, s.positions.c.realized_proceeds_eur)
                       .where(s.positions.c.book == "operational")).all()
    rec_days = [(p.capital_recovered_at - p.opened_at).total_seconds() / DAY for p in pos if p.capital_recovered_at]
    fracs = [min(Decimal(1), p.realized_proceeds_eur / p.invested_eur_total) for p in pos if p.invested_eur_total > 0]

    # --- timing: decision -> execution -> capital availability
    ex = conn.execute(select(s.orders.c.requested_at, s.executions.c.executed_at, s.executions.c.capital_available_at)
                      .join(s.executions, s.executions.c.order_id == s.orders.c.id)).all()
    d2e = [(r.executed_at - r.requested_at).total_seconds() for r in ex]
    e2a = [(r.capital_available_at - r.executed_at).total_seconds() for r in ex if r.capital_available_at]

    sp = spendable if spendable is not None else {a: ledger.balance(conn, a) for a in
                                                  (Account.CASH_INVESTMENT, Account.CASH_RECYCLED)}
    return CapitalEfficiency(
        as_of=as_of, period_days=period_days, contributions_total_eur=contributions, purchases_total_eur=purchases,
        purchases_from_contributions_eur=from_inv, purchases_from_recycled_eur=from_rec,
        investments_total=len(by_tx), investments_funded_by_recycled=n_rec, investments_fully_recycled=n_full_rec,
        capital_recycling_ratio=_ratio(from_rec, contributions), recycled_inflows_total_eur=inflows,
        capital_available_for_reinvestment_eur=sum(sp.values(), ZERO), recycled_pending_settlement_eur=pending,
        avg_committed_capital_cost_eur=avg_cost, avg_committed_capital_liquidable_eur=avg_liq,
        capital_turnover=turnover, capital_turnover_annualized=turnover_ann,
        avg_holding_period_days_realized=ahp, avg_age_open_lots_days=age_open, positions_total=len(pos),
        positions_capital_recovered=len(rec_days),
        mean_days_to_capital_recovery=statistics.fmean(rec_days) if rec_days else None,
        median_days_to_capital_recovery=statistics.median(rec_days) if rec_days else None,
        avg_partial_recovery_fraction=(sum(fracs, ZERO) / len(fracs)) if fracs else None,
        avg_decision_to_execution_s=statistics.fmean(d2e) if d2e else None,
        avg_execution_to_availability_s=statistics.fmean(e2a) if e2a else None,
    )


def investment_velocity(net_profit: Decimal, capital: Decimal, days: float) -> Decimal | None:
    """Ex-post capital velocity: net profit per EUR committed per day (descriptive, not predictive)."""
    if capital <= 0 or days <= 0:
        return None
    return net_profit / capital / Decimal(str(days))


