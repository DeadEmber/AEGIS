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


def recovery_at(base: StrategyConfig, multiple: str | None) -> StrategyConfig:
    """E2: recover the initial capital at a given NET multiple (None = no early capital recovery)."""
    c = base.model_copy(deep=True)
    c.name = f"recovery_{multiple or 'none'}"
    c.exits.capital_recovery_multiple = Decimal(10**9) if multiple is None else Decimal(multiple)
    return c


def residual_variant(base: StrategyConfig, fraction: str) -> StrategyConfig:
    """E2: minimum residual kept after monetisation (fraction of the original quantity)."""
    c = base.model_copy(deep=True)
    c.name = f"residual_{fraction}"
    c.exits.residual_min_fraction = Decimal(fraction)
    return c


def trailing_variant(base: StrategyConfig, activate: str | None, k: str = "3.0", min_pct: str = "0.25",
                     max_pct: str = "0.60") -> StrategyConfig:
    """E2: trailing floor rules (activate=None disables the trailing floor)."""
    c = base.model_copy(deep=True)
    c.name = f"trailing_{activate or 'off'}_{k}_{min_pct}_{max_pct}"
    c.exits.trailing_activate_multiple = Decimal(10**9) if activate is None else Decimal(activate)
    c.exits.trailing_vol_k, c.exits.trailing_min_pct, c.exits.trailing_max_pct = Decimal(k), Decimal(min_pct), Decimal(max_pct)
    return c


def monetisation_grid(base: StrategyConfig) -> list[StrategyConfig]:
    """E2 grid. The operational default is NOT changed by running it."""
    out = [recovery_at(base, m) for m in ("2", "5", "10", "20", None)]
    out += [residual_variant(base, f) for f in ("0.05", "0.10", "0.25")]
    out += [trailing_variant(base, a) for a in ("3", "10", None)]
    return out


def run_variant(cfg: StrategyConfig, gateway_factory: Callable[[ManualClock], MarketDataGateway], start: dt.datetime,
                hours: float, step_minutes: int = 30, *, cadence: str = "adaptive", full_interval_s: int = 300) -> dict[str, Any]:
    """Replay one variant. cadence='adaptive' uses the operational scheduler (Worker.replay/next_wake) at the
    gateway's data resolution; cadence='fixed' steps every `step_minutes` (mechanical tests only)."""
    clock = ManualClock(start)
    eng = make_engine("sqlite://")
    create_all(eng)
    gw = gateway_factory(clock)
    w = Worker(eng, Settings(database_url="sqlite://", execution_mode=ExecutionMode.HISTORICAL_BACKTEST), cfg,
               RiskLimits(), gw, clock)
    w.bootstrap()
    rr = w.replay(start + dt.timedelta(hours=hours), full_interval_s=full_interval_s, cadence=cadence,
                  fixed_step_s=step_minutes * 60, data_resolution_s=getattr(gw, "data_resolution_seconds", None))
    recon = rr.always_reconciled
    with eng.connect() as c:
        from aegis.accounting import efficiency

        last = c.execute(select(s.portfolio_snapshots).order_by(s.portfolio_snapshots.c.id.desc()).limit(1)).first()
        eff = efficiency.compute(c, clock.now())
        buys = c.execute(select(s.orders).where(s.orders.c.side == "BUY", s.orders.c.status == "FILLED")).all()
        sells = c.execute(select(s.orders).where(s.orders.c.side == "SELL", s.orders.c.status == "FILLED")).all()
    eng.dispose()
    return {"strategy": cfg.name, "strategy_hash": cfg.config_hash, "objective_eur": last.objective_eur,
            "net_worth_eur": last.net_worth_liquidable_eur, "realized_pnl_eur": last.realized_pnl_total_eur,
            "costs_eur": last.costs_total_eur, "buys": len(buys), "sells": len(sells),
            "open_positions": last.open_positions, "always_reconciled": recon,
            "positions_liquidation_eur": last.positions_liquidation_eur,
            "recycled_inflows_eur": eff.recycled_inflows_total_eur,
            "purchases_from_recycled_eur": eff.purchases_from_recycled_eur,
            "investments_funded_by_recycled": eff.investments_funded_by_recycled,
            "capital_turnover": eff.capital_turnover, "replay": rr.__dict__}


def compare(variants: list[StrategyConfig], gateway_factory: Callable[[ManualClock], MarketDataGateway],
            start: dt.datetime, hours: float, step_minutes: int = 30, *, cadence: str = "adaptive") -> list[dict[str, Any]]:
    out = [run_variant(v, gateway_factory, start, hours, step_minutes, cadence=cadence) for v in variants]
    out.append({"strategy": "cash", "objective_eur": Decimal(0), "note": "keep contributions in cash"})
    return out
