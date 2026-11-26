"""Strategy comparison harness: run StrategyConfig variants through the SAME Worker, market script,
clock schedule, cost model and contribution calendar, each on its own fresh database.

With the simulated market this validates mechanics only. With a HistoricalGateway (pending) it becomes
the Research Lab's comparison of AEGIS vs. simpler alternatives.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from aegis.config import ExecutionMode, RiskLimits, Settings, StrategyConfig
from aegis.core.clock import ManualClock
from aegis.storage import schema as s
from aegis.storage.db import create_all, make_engine
from aegis.worker.gateway import MarketDataGateway
from aegis.worker.runner import Worker


def hold_only(base: StrategyConfig) -> StrategyConfig:
    c = base.model_copy(deep=True)
    c.name = "buy_and_hold"
    c.exits.policy = "hold"
    return c


def naive_stop(base: StrategyConfig, multiple: str = "0.7") -> StrategyConfig:
    """Stop-loss only: no capital recovery, no trailing, no PRS trims, no deterioration rule."""
    c = base.model_copy(deep=True)
    c.name = f"stop_loss_{multiple}"
    c.exits.capital_recovery_multiple = Decimal(10**9)
    c.exits.trailing_activate_multiple = Decimal(10**9)
    c.exits.prs_partial_threshold = Decimal(2)
    c.exits.prs_full_threshold = Decimal(2)
    c.exits.deterioration_exit = False
    c.exits.stop_loss_multiple = Decimal(multiple)
    return c


def with_ecv(base: StrategyConfig) -> StrategyConfig:
    """Same strategy with ECV as a complementary selection factor (experiment E1)."""
    c = base.model_copy(deep=True)
    c.name = "aegis-ecv"
    c.ecv.enabled_in_selection = True
    return c


def run_variant(cfg: StrategyConfig, gateway_factory: Callable[[ManualClock], MarketDataGateway], start: dt.datetime,
                hours: float, step_minutes: int = 30) -> dict[str, Any]:
    clock = ManualClock(start)
    eng = make_engine("sqlite://")
    create_all(eng)
    w = Worker(eng, Settings(database_url="sqlite://", execution_mode=ExecutionMode.HISTORICAL_BACKTEST), cfg,
               RiskLimits(), gateway_factory(clock), clock)
    w.bootstrap()
    end = start + dt.timedelta(hours=hours)
    recon = True
    while clock.now() < end:
        recon &= bool(w.run_cycle().reconciliation_ok)
        clock.advance(minutes=step_minutes)
    with eng.connect() as c:
        last = c.execute(select(s.portfolio_snapshots).order_by(s.portfolio_snapshots.c.id.desc()).limit(1)).first()
        buys = c.execute(select(s.orders).where(s.orders.c.side == "BUY", s.orders.c.status == "FILLED")).all()
        sells = c.execute(select(s.orders).where(s.orders.c.side == "SELL", s.orders.c.status == "FILLED")).all()
    eng.dispose()
    return {"strategy": cfg.name, "strategy_hash": cfg.config_hash, "objective_eur": last.objective_eur,
            "net_worth_eur": last.net_worth_liquidable_eur, "realized_pnl_eur": last.realized_pnl_total_eur,
            "costs_eur": last.costs_total_eur, "buys": len(buys), "sells": len(sells),
            "open_positions": last.open_positions, "always_reconciled": recon}


def compare(variants: list[StrategyConfig], gateway_factory: Callable[[ManualClock], MarketDataGateway],
            start: dt.datetime, hours: float, step_minutes: int = 30) -> list[dict[str, Any]]:
    out = [run_variant(v, gateway_factory, start, hours, step_minutes) for v in variants]
    out.append({"strategy": "cash", "objective_eur": Decimal(0), "note": "keep contributions in cash"})
    return out
