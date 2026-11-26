"""Research metric (experiment E2): upside forgone by selling vs. return obtained by reinvesting.

COUNTERFACTUAL vs EFFECTIVE are kept apart:
- `forgone_*` comes from shadow rows of cohort after_partial_sell / after_exit (units actually sold):
  liquidable value of those units at the horizon minus the net proceeds realised. Positive = upside
  forgone by selling; negative = loss avoided. Shadows never touch the ledger or net worth.
- `reinvested_*` is EFFECTIVE accounting: purchases funded from cash:recycled (ledger postings) and the
  P&L of the positions they funded (realised + current liquidable), attributed pro rata to the recycled
  share of each purchase.
The two are measured on different clocks (fixed horizon vs. to-date) and are not a like-for-like sum;
E2 compares them across variants under identical conditions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from aegis.accounting.ledger import Account
from aegis.core.money import ZERO
from aegis.storage import schema as s

SALE_COHORTS = ("after_partial_sell", "after_exit")


def forgone_upside(conn: Connection, horizon: str) -> dict[str, Any]:
    out: dict[str, Any] = {"horizon": horizon, "observed": 0, "unknown": 0, "censored": 0,
                           "net_proceeds_eur": ZERO, "liquidable_at_h_eur": ZERO, "forgone_liquidable_eur": ZERO,
                           "forgone_gross_eur": ZERO}
    rows = conn.execute(select(s.shadow_positions).where(s.shadow_positions.c.cohort.in_(SALE_COHORTS))).all()
    for r in rows:
        if (r.outcome or {}).get("sale", {}).get("kind") != "units_sold":
            continue
        hz = ((r.outcome or {}).get("horizons") or {}).get(horizon, {"status": "pending"})
        if hz["status"] == "observed" and hz.get("liquidable_eur") is not None:
            out["observed"] += 1
            liq = Decimal(hz["liquidable_eur"])
            out["net_proceeds_eur"] += r.notional_eur
            out["liquidable_at_h_eur"] += liq
            out["forgone_liquidable_eur"] += liq - r.notional_eur
            out["forgone_gross_eur"] += r.qty * Decimal(hz["price_eur"]) - r.notional_eur
        elif hz["status"] in ("unknown", "observed"):  # observed without liquidity data counts as unknown
            out["unknown"] += 1
        else:
            out["censored"] += 1
    return out


def reinvestment_return(conn: Connection) -> dict[str, Any]:
    """Purchases funded by recycled cash and the attributed P&L of the positions they funded."""
    exec_pos = {}
    for lot in conn.execute(select(s.position_lots.c.execution_id, s.position_lots.c.position_id)):
        exec_pos[lot.execution_id] = lot.position_id
    positions = {p.id: p for p in conn.execute(select(s.positions))}
    reinvested = attributed = ZERO
    funded = set()
    for tx in conn.execute(select(s.ledger_transactions).where(s.ledger_transactions.c.kind == "buy")):
        posts = {r.account: r.amount_eur for r in conn.execute(
            select(s.ledger_postings.c.account, s.ledger_postings.c.amount_eur)
            .where(s.ledger_postings.c.transaction_id == tx.id))}
        rec = -posts.get(str(Account.CASH_RECYCLED), ZERO)
        if rec <= 0:
            continue
        p = positions.get(exec_pos.get(tx.ref_id))
        if p is None or p.invested_eur_total <= 0:
            continue
        unrealized = (p.last_liquidation_eur - p.cost_basis_eur
                      if p.closed_at is None and p.last_liquidation_eur is not None else ZERO)
        pnl = p.realized_pnl_eur + unrealized
        reinvested += rec
        attributed += pnl * rec / p.invested_eur_total  # share of the position financed by this recycled cash
        funded.add(p.id)
    return {"reinvested_from_recycled_eur": reinvested, "positions_funded": len(funded),
            "attributed_pnl_eur": attributed,
            "note": "effective accounting; P&L attributed pro rata to the recycled share of each purchase"}
