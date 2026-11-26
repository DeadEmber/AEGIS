"""Shadow Portfolio: counterfactual tracking of opportunities NOT taken and of positions after exit.

Strictly separated from the operational ledger: shadow rows never create ledger postings and never
count towards net worth.

Multi-horizon evaluation (audit 2026-11-26). For each shadow and each horizon h (24 h, 7, 30, 90, 180,
365 days by default) the outcome is one of:
- `observed`: a FRESH observation exists inside the window [opened+h, opened+h+tolerance(h)]; records
  the gross price multiple and the liquidable multiple (CPMM exit after impact and fees);
- `unknown`: the window elapsed without a fresh observation (data gap, delisted pool...). No stale price
  is ever used as an outcome;
- `pending`: the horizon has not been reached yet (right-censored at as_of).
Peaks and drawdowns are updated only from fresh observations. A shadow is closed only when every horizon
is resolved (observed or unknown). Before the fix, shadows were closed at 14 days using the last price
seen, however old (reproduced: x2 recorded at day 15 from a day-1 price).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection

from aegis.config import ShadowConfig
from aegis.core.money import ZERO, q_eur
from aegis.storage import schema as s

Valuer = Callable[[Decimal, Decimal, Decimal | None], Decimal | None]  # (qty, price_eur, liquidity_eur) -> EUR


def label(hours: int) -> str:
    return f"{hours}h" if hours < 48 else f"{hours // 24}d"


def tolerance(hours: int, cfg: ShadowConfig) -> dt.timedelta:
    return dt.timedelta(hours=max(cfg.min_tolerance_hours, hours * cfg.tolerance_fraction))


def opened_today(conn: Connection, cohort: str, t: dt.datetime) -> int:
    start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(conn.execute(select(func.count()).select_from(s.shadow_positions).where(and_(
        s.shadow_positions.c.cohort == cohort, s.shadow_positions.c.opened_at >= start))).scalar_one())


def open_shadow(conn: Connection, *, token_id: int, cohort: str, reason: str, t: dt.datetime, price_eur: Decimal,
                notional_eur: Decimal, est_cost_eur: Decimal, liquidity_usd: Decimal | None,
                cfg: ShadowConfig | None = None, qty: Decimal | None = None,
                sale: dict[str, Any] | None = None) -> int | None:
    """Open a counterfactual. Default: a hypothetical ticket of `notional_eur` bought at `price_eur`.
    With `sale` (units actually SOLD by the operational portfolio): `qty` = units sold, `price_eur` =
    realised net price per unit and `notional_eur` = net proceeds, so horizon multiples > 1 measure the
    upside forgone by selling and < 1 the loss avoided. Several sales of the same token are tracked."""
    cfg = cfg or ShadowConfig()
    if price_eur is None or price_eur <= 0:
        return None
    cap = cfg.daily_cap.get(cohort, cfg.default_daily_cap)
    if cap is not None and opened_today(conn, cohort, t) >= cap:
        return None
    if sale is None:
        exists = conn.execute(select(s.shadow_positions.c.id).where(and_(
            s.shadow_positions.c.token_id == token_id, s.shadow_positions.c.cohort == cohort,
            s.shadow_positions.c.closed_at.is_(None)))).first()
        if exists:
            return None
    if qty is None:
        qty = max(ZERO, notional_eur - est_cost_eur) / price_eur
    horizons = {label(h): {"status": "pending", "hours": h} for h in cfg.horizons_hours}
    outcome: dict[str, Any] = {"horizons": horizons, "running": {"max_drawdown": "0"}, "version": 2}
    if sale is not None:
        outcome["sale"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in sale.items()}
    return int(conn.execute(s.shadow_positions.insert().values(
        token_id=token_id, cohort=cohort, reason=reason[:500], opened_at=t, entry_price_eur=price_eur,
        notional_eur=notional_eur, est_entry_cost_eur=q_eur(est_cost_eur), qty=qty, peak_price_eur=price_eur, peak_at=t,
        last_price_eur=price_eur, last_liquidity_usd=liquidity_usd, last_observed_at=t, outcome=outcome,
    )).inserted_primary_key[0])


def _resolve(row: Any, hz: dict[str, Any], now: dt.datetime, obs: tuple[Decimal, Decimal | None, Decimal | None,
             dt.datetime] | None, valuer: Valuer | None, running: dict[str, Any], cfg: ShadowConfig,
             peak: Decimal, last_observed_at: dt.datetime | None) -> dict[str, Any]:
    if hz["status"] != "pending":
        return hz
    target = row.opened_at + dt.timedelta(hours=hz["hours"])
    end = target + tolerance(hz["hours"], cfg)
    if obs is not None:
        price, liq_usd, liq_eur, at = obs
        if target <= at <= end:
            entry = row.entry_price_eur
            liq_val = valuer(row.qty, price, liq_eur) if valuer is not None else None
            return {
                "status": "observed", "hours": hz["hours"], "observed_at": at.isoformat(),
                "price_eur": str(price), "gross_multiple": str(price / entry),
                "liquidable_eur": None if liq_val is None else str(liq_val),
                "liquidable_multiple": None if liq_val is None else str(liq_val / row.notional_eur),
                "liquidity_usd": None if liq_usd is None else str(liq_usd),
                "peak_gross_multiple_to_h": str(max(peak, price) / entry),
                "max_drawdown_to_h": running.get("max_drawdown", "0"),
            }
    if now > end:
        return {"status": "unknown", "hours": hz["hours"], "reason": "no fresh observation inside the window",
                "window_end": end.isoformat(),
                "last_observed_at": last_observed_at.isoformat() if last_observed_at else None}
    return hz


def update_shadows(conn: Connection, now: dt.datetime,
                   observations: dict[int, tuple[Decimal, Decimal | None, Decimal | None, dt.datetime]],
                   valuer: Valuer | None = None, cfg: ShadowConfig | None = None) -> int:
    """`observations`: token_id -> (price_eur, liquidity_usd, liquidity_eur, observed_at), all FRESH this cycle."""
    cfg = cfg or ShadowConfig()
    n = 0
    for r in conn.execute(select(s.shadow_positions).where(s.shadow_positions.c.closed_at.is_(None))).all():
        outcome = dict(r.outcome or {})
        if outcome.get("version") != 2:  # legacy rows (pre-fix): keep, mark as legacy, never evaluate stale
            outcome = {"horizons": {label(h): {"status": "pending", "hours": h} for h in cfg.horizons_hours},
                       "running": {"max_drawdown": "0"}, "version": 2, "legacy": True}
        running = dict(outcome.get("running") or {})
        obs = observations.get(r.token_id)
        if obs is not None and (obs[0] is None or obs[0] <= 0 or obs[3] < r.last_observed_at):
            obs = None  # invalid or older than what we already have
        vals: dict[str, Any] = {}
        peak, last_seen = r.peak_price_eur, r.last_observed_at
        if obs is not None:
            price, liq_usd, _, at = obs
            vals.update(last_price_eur=price, last_liquidity_usd=liq_usd, last_observed_at=at)
            last_seen = at
            if price > peak:
                vals.update(peak_price_eur=price, peak_at=at)
                peak = price
            dd = 1 - price / peak if peak > 0 else ZERO
            running["max_drawdown"] = str(max(Decimal(running.get("max_drawdown", "0")), dd))
        hz = {k: _resolve(r, v, now, obs, valuer, running, cfg, peak, last_seen)
              for k, v in (outcome.get("horizons") or {}).items()}
        outcome.update(horizons=hz, running=running)
        vals["outcome"] = outcome
        if hz and all(v["status"] != "pending" for v in hz.values()):
            vals["closed_at"] = now
        conn.execute(update(s.shadow_positions).where(s.shadow_positions.c.id == r.id).values(**vals))
        n += 1
    return n


def checkpoint_tokens(conn: Connection, now: dt.datetime, cfg: ShadowConfig | None = None, limit: int = 300) -> list[int]:
    """Tokens whose shadow has a horizon window open right now and no observation for it yet."""
    cfg = cfg or ShadowConfig()
    out: list[int] = []
    for r in conn.execute(select(s.shadow_positions.c.token_id, s.shadow_positions.c.opened_at,
                                 s.shadow_positions.c.outcome).where(s.shadow_positions.c.closed_at.is_(None))):
        for hz in ((r.outcome or {}).get("horizons") or {}).values():
            if hz.get("status") != "pending":
                continue
            target = r.opened_at + dt.timedelta(hours=hz["hours"])
            if target <= now <= target + tolerance(hz["hours"], cfg):
                out.append(int(r.token_id))
                break
        if len(out) >= limit:
            break
    return sorted(set(out))


def summary(conn: Connection, cfg: ShadowConfig | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Per cohort and horizon: observed / unknown / censored counts and outcome distribution of observed."""
    cfg = cfg or ShadowConfig()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in conn.execute(select(s.shadow_positions.c.cohort, s.shadow_positions.c.outcome)):
        hz = ((r.outcome or {}).get("horizons") or {})
        for h in cfg.horizons_hours:
            k = label(h)
            v = hz.get(k, {"status": "pending"})
            g = out.setdefault(r.cohort, {}).setdefault(k, {"observed": 0, "unknown": 0, "censored": 0,
                                                            "gross": [], "liquidable": []})
            if v["status"] == "observed":
                g["observed"] += 1
                g["gross"].append(float(v["gross_multiple"]))
                if v.get("liquidable_multiple") is not None:
                    g["liquidable"].append(float(v["liquidable_multiple"]))
            elif v["status"] == "unknown":
                g["unknown"] += 1
            else:
                g["censored"] += 1
    for cohort in out.values():
        for g in cohort.values():
            gross, liq = sorted(g.pop("gross")), sorted(g.pop("liquidable"))
            g["gross_median"] = gross[len(gross) // 2] if gross else None
            g["gross_ge_x2"] = sum(1 for x in gross if x >= 2)
            g["gross_ge_x10"] = sum(1 for x in gross if x >= 10)
            g["liquidable_median"] = liq[len(liq) // 2] if liq else None
            g["liquidable_ge_x2"] = sum(1 for x in liq if x >= 2)
            g["liquidable_lt_half"] = sum(1 for x in liq if x < 0.5)
    return out
