"""FastAPI backend for the dashboard. Read-only except the audited kill-switch endpoint.

All monetary values are serialised as exact decimal strings. Every response states whether the data
comes from a simulated or a live-market database (`data_mode`) — the UI must display it.
"""

from __future__ import annotations

import datetime as dt
import secrets
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, and_, desc, func, or_, select

from aegis import __version__
from aegis.accounting.ledger import Account, Ledger
from aegis.accounting.performance import ValuePoint, max_drawdown, net_portfolio_return, twr, twr_index, xirr
from aegis.accounting.portfolio import PortfolioService
from aegis.config import RiskLimits, Settings, StrategyConfig
from aegis.core.clock import UTC
from aegis.features.library import REGISTRY
from aegis.risk.controls import BUYS_SUSPENDED, KILL_SWITCH
from aegis.storage import repo
from aegis.storage import schema as s


def _ser(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, dt.datetime):
        return v.astimezone(UTC).isoformat()
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_ser(x) for x in v]
    return v


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [_ser(dict(r._mapping)) for r in rows]


class DecimalJSON(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(_ser(content))


class KillSwitchBody(BaseModel):
    on: bool
    reason: str


def _scheduling_summary(c: Any) -> dict[str, Any]:
    """Configured vs effective monitoring: per class interval, overdue items, lateness and misses."""
    now = dt.datetime.now(UTC)
    last = c.execute(select(s.portfolio_snapshots.c.as_of).order_by(desc(s.portfolio_snapshots.c.as_of)).limit(1)).first()
    ref = last.as_of if last is not None and last.as_of < now - dt.timedelta(days=1) else now  # simulated DB clock
    out: dict[str, dict[str, Any]] = {}
    for r in c.execute(select(s.scheduler_queue)):
        g = out.setdefault(r.reason, {"n": 0, "configured_interval_s": r.interval_seconds, "overdue": 0,
                                      "max_overdue_s": 0, "max_misses": 0, "lateness": []})
        g["n"] += 1
        if r.next_due_at <= ref:
            g["overdue"] += 1
            g["max_overdue_s"] = max(g["max_overdue_s"], int((ref - r.next_due_at).total_seconds()))
        g["max_misses"] = max(g["max_misses"], r.consecutive_misses or 0)
        if r.last_lateness_seconds is not None:
            g["lateness"].append(r.last_lateness_seconds)
    for g in out.values():
        lat = sorted(g.pop("lateness"))
        g["last_lateness_median_s"] = lat[len(lat) // 2] if lat else None
        g["last_lateness_max_s"] = lat[-1] if lat else None
    hb = c.execute(select(s.worker_heartbeats.c.last_cycle)).first()
    return {"by_class": out, "last_cycle": (hb.last_cycle or {}).get("scheduling") if hb else None}


def create_app(engine: Engine, settings: Settings, strategy: StrategyConfig | None = None,
               limits: RiskLimits | None = None) -> FastAPI:
    strategy = strategy or StrategyConfig()
    limits = limits or RiskLimits()
    svc = PortfolioService(strategy)
    ledger = Ledger()
    app = FastAPI(title="AEGIS API", version=__version__, default_response_class=DecimalJSON)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.api_cors_origins.split(",") if o.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    def conn():  # type: ignore[no-untyped-def]
        with engine.connect() as c:
            yield c

    def meta(c: Any) -> dict[str, Any]:
        return {"data_mode": repo.get_flag(c, "data_mode") or "empty", "execution_mode": str(settings.execution_mode),
                "env": str(settings.env), "generated_at": dt.datetime.now(UTC)}

    @app.get("/health")
    def health(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        hb = c.execute(select(s.worker_heartbeats)).all()
        return {"status": "ok", "version": __version__, **meta(c), "workers": _rows(hb)}

    @app.get("/overview")
    def overview(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        snaps = c.execute(select(s.portfolio_snapshots).order_by(s.portfolio_snapshots.c.as_of)).all()
        last = snaps[-1] if snaps else None
        kpi: dict[str, Any] = {}
        if last is not None:
            pts = [ValuePoint(r.as_of, r.net_worth_liquidable_eur, r.external_flow_since_last_eur) for r in snaps]
            idx = twr_index(pts)
            flows = [(r.occurred_at, -r.amount_eur) for r in c.execute(select(s.contributions))]
            flows.append((last.as_of, last.net_worth_liquidable_eur + last.withdrawals_total_eur))
            kpi = {
                "net_portfolio_return": net_portfolio_return(last.net_worth_liquidable_eur, last.contributions_total_eur,
                                                             last.withdrawals_total_eur),
                "twr": twr(pts), "max_drawdown_twr": max_drawdown(idx) if idx else None,
                "xirr": xirr(flows) if (last.as_of - snaps[0].as_of).days >= 30 else None,
                "xirr_note": "XIRR shown only after >=30 days of history (annualising shorter periods is misleading)",
                "cost_to_capital_ratio": (last.costs_total_eur / last.contributions_total_eur
                                          if last.contributions_total_eur else None),
            }
        bal = ledger.balances(c)
        counts = {
            "tokens_tracked": repo.count(c, s.tokens),
            "open_positions": repo.count(c, s.positions, s.positions.c.closed_at.is_(None)),
            "closed_positions": repo.count(c, s.positions, s.positions.c.closed_at.is_not(None)),
            "buys": repo.count(c, s.orders, and_(s.orders.c.side == "BUY", s.orders.c.status == "FILLED")),
            "sells": repo.count(c, s.orders, and_(s.orders.c.side == "SELL", s.orders.c.status == "FILLED")),
            "rejected_orders": repo.count(c, s.orders, s.orders.c.status.in_(["REJECTED", "EXPIRED"])),
        }
        recycled_in = sum((r.amount_eur for r in c.execute(
            select(s.ledger_postings.c.amount_eur).join(s.ledger_transactions)
            .where(and_(s.ledger_postings.c.account == str(Account.CASH_RECYCLED), s.ledger_transactions.c.kind == "sell")))),
            Decimal(0))
        return {**meta(c), "snapshot": _ser(dict(last._mapping)) if last else None, "kpi": kpi,
                "balances": {str(k): v for k, v in bal.items()}, "counts": counts,
                "capital_recycled_total_eur": recycled_in}

    @app.get("/portfolio/history")
    def history(c=Depends(conn), limit: int = Query(2000, le=20000)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        rows = c.execute(select(s.portfolio_snapshots.c.as_of, s.portfolio_snapshots.c.net_worth_liquidable_eur,
                                s.portfolio_snapshots.c.contributions_total_eur, s.portfolio_snapshots.c.objective_eur,
                                s.portfolio_snapshots.c.positions_liquidation_eur, s.portfolio_snapshots.c.positions_mark_eur,
                                s.portfolio_snapshots.c.open_positions, s.portfolio_snapshots.c.reconciliation_ok)
                         .order_by(desc(s.portfolio_snapshots.c.as_of)).limit(limit)).all()
        return {**meta(c), "points": list(reversed(_rows(rows)))}

    def _positions(c: Any, where: Any, limit: int) -> list[dict[str, Any]]:
        q = (select(s.positions, s.tokens.c.address, s.tokens.c.symbol, s.tokens.c.market_state,
                    s.tokens.c.meta.label("token_meta"))
             .join(s.tokens, s.tokens.c.id == s.positions.c.token_id).where(where)
             .order_by(desc(s.positions.c.opened_at)).limit(limit))
        out = []
        for r in c.execute(q):
            d = dict(r._mapping)
            d["scores"] = (d.pop("token_meta") or {}).get("scores", {})
            liq = d.get("last_liquidation_eur")
            d["unrealized_pnl_eur"] = None if liq is None else liq - d["cost_basis_eur"]
            d["rpr"] = None if not d.get("last_mark_eur") else (liq or Decimal(0)) / d["last_mark_eur"]
            out.append(_ser(d))
        return out

    @app.get("/portfolio/positions")
    def positions(c=Depends(conn), status: str = "open", limit: int = Query(500, le=5000)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        where = s.positions.c.closed_at.is_(None) if status == "open" else s.positions.c.closed_at.is_not(None)
        return {**meta(c), "positions": _positions(c, where, limit)}

    @app.get("/portfolio/positions/{pid}")
    def position(pid: int, c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        p = _positions(c, s.positions.c.id == pid, 1)
        if not p:
            raise HTTPException(404, "position not found")
        lots = c.execute(select(s.position_lots).where(s.position_lots.c.position_id == pid)).all()
        lot_ids = [lot.id for lot in lots]
        disp = c.execute(select(s.lot_disposals).where(s.lot_disposals.c.lot_id.in_(lot_ids))).all() if lot_ids else []
        execs = c.execute(select(s.executions, s.orders.c.side, s.orders.c.idempotency_key)
                          .join(s.orders).where(s.orders.c.token_id == p[0]["token_id"])
                          .order_by(s.executions.c.executed_at)).all()
        decs = c.execute(select(s.decisions).where(s.decisions.c.position_id == pid)
                         .order_by(desc(s.decisions.c.decided_at)).limit(100)).all()
        return {**meta(c), "position": p[0], "lots": _rows(lots), "disposals": _rows(disp), "executions": _rows(execs),
                "decisions": _rows(decs)}

    @app.get("/discovery/tokens")
    def tokens(c=Depends(conn), state: str | None = None, eligible: bool | None = None,  # type: ignore[no-untyped-def]
               limit: int = Query(300, le=5000)) -> dict[str, Any]:
        q = select(s.tokens).order_by(desc(s.tokens.c.last_seen_at)).limit(limit)
        if state:
            q = q.where(s.tokens.c.market_state == state)
        out = []
        for r in c.execute(q):
            d = dict(r._mapping)
            el = (d.get("meta") or {}).get("eligibility", {})
            if eligible is not None and bool(el.get("eligible")) != eligible:
                continue
            snap = repo.latest_snapshot(c, r.id)
            sec = repo.latest_security(c, r.id)
            d.update(eligibility=el, scores=(d.get("meta") or {}).get("scores", {}),
                     price_usd=snap.price_usd if snap else None, liquidity_usd=snap.liquidity_usd if snap else None,
                     fdv_usd=snap.fdv_usd if snap else None, market_cap_usd=snap.market_cap_usd if snap else None,
                     volume_h24_usd=snap.volume_h24_usd if snap else None,
                     price_change_h1=snap.price_change_h1 if snap else None,
                     price_change_h24=snap.price_change_h24 if snap else None,
                     observed_at=snap.observed_at if snap else None, security_verdict=sec.verdict if sec else "UNKNOWN")
            d.pop("meta", None)
            out.append(_ser(d))
        states = dict(c.execute(select(s.tokens.c.market_state, func.count()).group_by(s.tokens.c.market_state)).all())
        return {**meta(c), "tokens": out, "state_counts": states}

    @app.get("/tokens/{tid}")
    def token(tid: int, c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        t = c.execute(select(s.tokens).where(s.tokens.c.id == tid)).first()
        if t is None:
            raise HTTPException(404, "token not found")
        snaps = c.execute(select(s.market_snapshots).where(s.market_snapshots.c.token_id == tid)
                          .order_by(desc(s.market_snapshots.c.observed_at)).limit(300)).all()
        feats = c.execute(select(s.features).where(s.features.c.token_id == tid)
                          .order_by(desc(s.features.c.as_of)).limit(1)).first()
        sec = c.execute(select(s.security_assessments).where(s.security_assessments.c.token_id == tid)
                        .order_by(desc(s.security_assessments.c.assessed_at)).limit(5)).all()
        states = c.execute(select(s.token_states).where(s.token_states.c.token_id == tid)
                           .order_by(desc(s.token_states.c.valid_from)).limit(50)).all()
        pools = c.execute(select(s.pools).where(s.pools.c.token_id == tid)).all()
        decs = c.execute(select(s.decisions).where(s.decisions.c.token_id == tid)
                         .order_by(desc(s.decisions.c.decided_at)).limit(50)).all()
        return {**meta(c), "token": _ser(dict(t._mapping)), "pools": _rows(pools),
                "snapshots": list(reversed(_rows(snaps))), "features": _ser(dict(feats._mapping)) if feats else None,
                "security": _rows(sec), "states": _rows(states), "decisions": _rows(decs)}

    @app.get("/decisions")
    def decisions(c=Depends(conn), action: str | None = None, limit: int = Query(200, le=5000)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        q = (select(s.decisions, s.tokens.c.symbol, s.tokens.c.address).outerjoin(s.tokens)
             .order_by(desc(s.decisions.c.id)).limit(limit))
        if action:
            q = q.where(s.decisions.c.action == action)
        counts = dict(c.execute(select(s.decisions.c.action, func.count()).group_by(s.decisions.c.action)).all())
        return {**meta(c), "decisions": _rows(c.execute(q)), "counts": counts}

    @app.get("/peak-radar")
    def peak_radar(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        hot = ("EXPANSION", "EXPLOSION", "DISTRIBUTION", "SECOND_WAVE")
        held = _positions(c, s.positions.c.closed_at.is_(None), 5000)
        rows = c.execute(select(s.tokens).where(s.tokens.c.market_state.in_(hot)).order_by(desc(s.tokens.c.last_seen_at))
                         .limit(200)).all()
        market = []
        for r in rows:
            snap = repo.latest_snapshot(c, r.id)
            ath, price = r.ath_price_usd, snap.price_usd if snap else None
            market.append(_ser({"token_id": r.id, "symbol": r.symbol, "address": r.address, "state": r.market_state,
                                "since": r.market_state_since, "scores": (r.meta or {}).get("scores", {}),
                                "ath_price_usd": ath, "price_usd": price,
                                "drawdown_from_ath": None if not ath or price is None else 1 - price / ath,
                                "liquidity_usd": snap.liquidity_usd if snap else None,
                                "price_change_h1": snap.price_change_h1 if snap else None,
                                "explosion_count": r.explosion_count}))
        exits = c.execute(select(s.decisions, s.tokens.c.symbol).outerjoin(s.tokens)
                          .where(s.decisions.c.action.in_(["PARTIAL_SELL", "FULL_SELL"]))
                          .order_by(desc(s.decisions.c.id)).limit(100)).all()
        return {**meta(c), "positions": held, "market": market, "exit_decisions": _rows(exits)}

    @app.get("/second-wave")
    def second_wave(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        rows = c.execute(select(s.tokens).where(or_(s.tokens.c.entries > 0, s.tokens.c.explosion_count > 0))
                         .order_by(desc(s.tokens.c.last_seen_at)).limit(500)).all()
        out = []
        for r in rows:
            snap = repo.latest_snapshot(c, r.id)
            price = snap.price_usd if snap else None
            out.append(_ser({
                "token_id": r.id, "symbol": r.symbol, "address": r.address, "state": r.market_state,
                "entries": r.entries, "explosion_count": r.explosion_count, "last_exit_at": r.last_exit_at,
                "ath_price_usd": r.ath_price_usd, "low_since_ath_usd": r.low_since_ath_usd, "price_usd": price,
                "drawdown_from_ath": None if not r.ath_price_usd or price is None else 1 - price / r.ath_price_usd,
                "rebound_from_low": None if not r.low_since_ath_usd or price is None else price / r.low_since_ath_usd,
                "liquidity_usd": snap.liquidity_usd if snap else None, "liquidity_at_exit_usd": r.liquidity_at_exit_usd,
                "scores": (r.meta or {}).get("scores", {}),
            }))
        events = c.execute(select(s.explosion_events).order_by(desc(s.explosion_events.c.started_at)).limit(200)).all()
        return {**meta(c), "tokens": out, "explosion_events": _rows(events)}

    @app.get("/capital")
    def capital(c=Depends(conn), limit: int = Query(300, le=10000)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        txs = c.execute(select(s.ledger_transactions).order_by(desc(s.ledger_transactions.c.id)).limit(limit)).all()
        ids = [t.id for t in txs]
        posts = c.execute(select(s.ledger_postings).where(s.ledger_postings.c.transaction_id.in_(ids))).all() if ids else []
        by_tx: dict[int, list[dict[str, Any]]] = {}
        for p in posts:
            by_tx.setdefault(p.transaction_id, []).append(_ser({"account": p.account, "amount_eur": p.amount_eur}))
        tx_out = [{**_ser(dict(t._mapping)), "postings": by_tx.get(t.id, [])} for t in txs]
        rec = svc.reconcile(c)
        by_bucket = {k: v for k, v in c.execute(select(s.positions.c.bucket, func.count()).group_by(s.positions.c.bucket))}
        return {**meta(c), "balances": {str(k): v for k, v in ledger.balances(c).items()},
                "contributions": _rows(c.execute(select(s.contributions).order_by(s.contributions.c.period))),
                "transactions": tx_out, "reconciliation": {"ok": rec.ok, "checks": rec.checks},
                "positions_by_bucket": by_bucket, "tax_estimate_by_year": svc.estimated_tax_by_year(c),
                "tax_note": "Estimate for provisioning only; not a tax return. See RISK_MANAGEMENT.md."}

    @app.get("/capital/efficiency")
    def capital_efficiency(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        from aegis.accounting import efficiency
        from aegis.research import temporal

        now = dt.datetime.now(UTC)
        last = c.execute(select(s.portfolio_snapshots.c.as_of).order_by(desc(s.portfolio_snapshots.c.as_of))
                         .limit(1)).first()
        as_of = last.as_of if last is not None else now  # simulated DBs live on their own clock
        pending = svc.unsettled_proceeds(c, as_of)
        eff = efficiency.compute(c, as_of, ledger, svc.spendable(c, at=as_of), pending)
        eps = temporal.episodes(c, as_of)
        estimates = {k: temporal.empirical_duration(eps, k).__dict__ for k in temporal.EPISODE_KINDS}
        counts = {k: {"episodes": sum(1 for e in eps if e.kind == k),
                      "observed": sum(1 for e in eps if e.kind == k and not e.censored)} for k in temporal.EPISODE_KINDS}
        return {**meta(c), "as_of": as_of, "efficiency": eff.as_dict(), "temporal_estimates": estimates,
                "episode_counts": counts,
                "ecv": {"status": "experimental", "enabled_in_selection": strategy.ecv.enabled_in_selection,
                        "note": "ECV is unavailable until a validated outcome/duration model (or enough closed "
                                "positions for the population baseline) exists."}}

    @app.get("/risk")
    def risk(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        flags = _rows(c.execute(select(s.control_flags)))
        events = _rows(c.execute(select(s.risk_events).order_by(desc(s.risk_events.c.occurred_at)).limit(100)))
        sec_events = _rows(c.execute(select(s.security_events, s.tokens.c.symbol).join(s.tokens)
                                     .order_by(desc(s.security_events.c.occurred_at)).limit(200)))
        verdicts = dict(c.execute(select(s.security_assessments.c.verdict, func.count())
                                  .group_by(s.security_assessments.c.verdict)).all())
        dq = _rows(c.execute(select(s.data_quality_events).order_by(desc(s.data_quality_events.c.occurred_at)).limit(100)))
        return {**meta(c), "flags": flags, "risk_events": events, "security_events": sec_events,
                "assessment_verdicts": verdicts, "data_quality_events": dq, "limits": limits.model_dump(mode="json")}

    @app.get("/system")
    def system(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {**meta(c), "sources": _rows(c.execute(select(s.source_health))),
                "workers": _rows(c.execute(select(s.worker_heartbeats))),
                "events": _rows(c.execute(select(s.system_events).order_by(desc(s.system_events.c.id)).limit(100))),
                "table_counts": {t: repo.count(c, getattr(s, t)) for t in (
                    "tokens", "pools", "market_snapshots", "features", "decisions", "orders", "executions",
                    "ledger_transactions", "shadow_positions", "security_assessments")},
                "scheduler": dict(c.execute(select(s.scheduler_queue.c.reason, func.count())
                                            .group_by(s.scheduler_queue.c.reason)).all()),
                "scheduling": _scheduling_summary(c)}

    @app.get("/shadow")
    def shadow_view(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        from aegis.learning import shadow as sh

        rows = c.execute(select(s.shadow_positions, s.tokens.c.symbol).join(s.tokens)
                         .order_by(desc(s.shadow_positions.c.opened_at)).limit(500)).all()
        out = []
        for r in rows:
            d = dict(r._mapping)
            d["horizons"] = (d.pop("outcome") or {}).get("horizons", {})
            d["peak_multiple_to_date"] = d["peak_price_eur"] / d["entry_price_eur"]
            out.append(_ser(d))
        return {**meta(c), "note": "Counterfactual; never part of net worth. Horizon status: observed (fresh data in "
                                   "window), unknown (no fresh data), censored (not reached yet).",
                "horizons": [sh.label(h) for h in strategy.shadow.horizons_hours],
                "summary": sh.summary(c, strategy.shadow), "positions": out}

    @app.get("/research/experiments")
    def research_experiments() -> dict[str, Any]:
        """Phase-2 historical experiments (derived figures only, read from the versioned JSON results)."""
        import json

        cols = ("label", "dataset", "net_worth_liquidable_eur", "net_worth_mark_to_last_eur", "contributions_eur", "buys",
                "sells", "mean_return_per_ticket", "win_rate_closed", "peak_ge_x2", "peak_ge_x2_realised_ge_x2",
                "median_peak_capture", "cash_idle_share", "owner_reported_eur")
        out = []
        root = settings.research_results_dir
        files = sorted(root.glob("E*.json"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0) \
            if root.exists() else []
        for p in files:
            doc = json.loads(p.read_text())
            data = doc.get("data", {})
            rows = []
            for key in ("reference", "rows", "economic", "portfolios"):
                rows += [{c: r.get(c) for c in cols} for r in data.get(key, [])]
            out.append({"id": doc["id"], "title": doc["title"], "generated_at": doc["generated_at"],
                        "aegis_version": doc["aegis_version"], "datasets": doc.get("datasets", {}),
                        "test_window": data.get("test_window") or data.get("period"), "rows": rows})
        return {"note": "Historical research on real point-in-time data (explored periods: not independent "
                        "evidence). Multiples are net of measured fees and costs; nothing here is a forecast.",
                "experiments": out}

    @app.get("/intelligence")
    def intelligence(c=Depends(conn)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        models = _rows(c.execute(select(s.model_versions)))
        return {**meta(c), "models": models, "active_scoring": {"name": "heuristic-v0", "kind": "heuristic",
                                                                 "calibrated_probability": False},
                "feature_catalog": [spec.__dict__ for spec in REGISTRY.values()],
                "strategy_hash": strategy.config_hash, "strategy": strategy.model_dump(mode="json")}

    def require_token(authorization: str = Header(default="")) -> None:
        tok = settings.api_control_token
        if not tok:
            raise HTTPException(403, "control endpoints disabled (AEGIS_API_CONTROL_TOKEN not set)")
        if not secrets.compare_digest(authorization, f"Bearer {tok}"):
            raise HTTPException(401, "invalid token")

    @app.post("/control/kill-switch", dependencies=[Depends(require_token)])
    def kill_switch(body: KillSwitchBody) -> dict[str, Any]:
        with engine.begin() as c:
            repo.set_flag(c, KILL_SWITCH, "on" if body.on else "off", dt.datetime.now(UTC), "api", body.reason)
        return {"kill_switch": body.on}

    @app.post("/control/buys", dependencies=[Depends(require_token)])
    def buys(body: KillSwitchBody) -> dict[str, Any]:
        with engine.begin() as c:
            repo.set_flag(c, BUYS_SUSPENDED, "on" if body.on else "off", dt.datetime.now(UTC), "api", body.reason)
        return {"buys_suspended": body.on}

    return app
