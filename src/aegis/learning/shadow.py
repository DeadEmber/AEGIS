"""Shadow Portfolio: counterfactual tracking of candidates NOT bought and of positions after exit.

Strictly separated from the operational ledger: shadow rows never create ledger postings and never
count towards net worth. Outcomes feed the Continuous Learning Engine (labels for missed
opportunities, correct rejections, premature exits).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from aegis.core.money import ZERO, q_eur
from aegis.storage import schema as s

HORIZON = dt.timedelta(days=14)


def open_shadow(conn: Connection, *, token_id: int, cohort: str, reason: str, t: dt.datetime, price_eur: Decimal,
                notional_eur: Decimal, est_cost_eur: Decimal, liquidity_usd: Decimal | None) -> int | None:
    if price_eur is None or price_eur <= 0:
        return None
    exists = conn.execute(select(s.shadow_positions.c.id).where(and_(
        s.shadow_positions.c.token_id == token_id, s.shadow_positions.c.cohort == cohort,
        s.shadow_positions.c.closed_at.is_(None)))).first()
    if exists:
        return None
    qty = max(ZERO, notional_eur - est_cost_eur) / price_eur
    return int(conn.execute(s.shadow_positions.insert().values(
        token_id=token_id, cohort=cohort, reason=reason[:500], opened_at=t, entry_price_eur=price_eur,
        notional_eur=notional_eur, est_entry_cost_eur=q_eur(est_cost_eur), qty=qty, peak_price_eur=price_eur, peak_at=t,
        last_price_eur=price_eur, last_liquidity_usd=liquidity_usd, last_observed_at=t, outcome={},
    )).inserted_primary_key[0])


def update_shadows(conn: Connection, t: dt.datetime, prices: dict[int, tuple[Decimal, Decimal | None]]) -> int:
    n = 0
    rows = conn.execute(select(s.shadow_positions).where(s.shadow_positions.c.closed_at.is_(None))).all()
    for r in rows:
        px = prices.get(r.token_id)
        vals: dict[str, object] = {}
        if px is not None and px[0] is not None and px[0] > 0:
            price, liq = px
            vals.update(last_price_eur=price, last_liquidity_usd=liq, last_observed_at=t)
            if price > r.peak_price_eur:
                vals.update(peak_price_eur=price, peak_at=t)
        if t - r.opened_at >= HORIZON:
            last = vals.get("last_price_eur", r.last_price_eur)
            peak = vals.get("peak_price_eur", r.peak_price_eur)
            vals.update(closed_at=t, outcome={
                "peak_multiple": str(peak / r.entry_price_eur), "final_multiple": str(last / r.entry_price_eur),
                "hours_to_peak": round(((vals.get("peak_at") or r.peak_at) - r.opened_at).total_seconds() / 3600, 2),
                "note": "gross price multiples; liquidity/impact not applied (see BACKTEST_METHODOLOGY.md)",
            })
        if vals:
            conn.execute(update(s.shadow_positions).where(s.shadow_positions.c.id == r.id).values(**vals))
            n += 1
    return n
