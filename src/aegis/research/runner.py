"""Glue for historical experiments: load a registered dataset, build policies from config, run, cache stores."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from aegis.config import PostGradEntryConfig, PostGradExitConfig, StrategyConfig
from aegis.history import launchtape, pumpfun
from aegis.history.store import HistoricalStore
from aegis.research import backtest, presets
from aegis.research.policies import AegisExit, EntryPolicy, ExitPolicy, HoldExit, PostGradEntry, RulesExit

DATASETS = ("pumpfun_corpus", "launchtape")


@lru_cache(maxsize=4)
def load_store(dataset: str, base: str, start: str | None = None, end: str | None = None,
               resolution_s: float = 15.0) -> HistoricalStore:
    root = Path(base)
    if dataset == "pumpfun_corpus":
        return pumpfun.load(root, start=start, end=end)
    if dataset == "launchtape":
        return launchtape.load(root, resolution_s=resolution_s)
    raise ValueError(f"unknown dataset {dataset!r}; known: {DATASETS}")


def exit_policy(x: PostGradExitConfig, strategy: StrategyConfig, source: str) -> ExitPolicy:
    if x.policy == "hold":
        return HoldExit()
    if x.policy == "rules":
        return RulesExit(x, min_sale_eur=strategy.exits.min_sale_eur)
    if x.policy == "aegis":
        return AegisExit(strategy, source)
    if x.policy == "agent":
        raise ValueError("policy 'agent' needs a fitted model: pass `exit_override` (see aegis.research.agent)")
    raise ValueError(x.policy)


def run_preset(store: HistoricalStore, preset: str, *, entry: PostGradEntryConfig | None = None,
               exit_cfg: PostGradExitConfig | None = None, entry_policy: EntryPolicy | None = None,
               exit_override: ExitPolicy | None = None, capital: dict[str, Any] | None = None,
               label: str | None = None, **bt: Any) -> backtest.BacktestResult:
    e0, x0 = presets.REFERENCE.get(preset, (presets.STRICT, presets.TRAILING))
    e, x = entry or e0, exit_cfg or x0
    cap = {"investment_pacing": False, "recycling": "full", **(capital or {})}
    for k in ("ticket_eur", "monthly_contribution_eur"):
        if k in cap and not isinstance(cap[k], Decimal):
            cap[k] = Decimal(str(cap[k]))
    strategy = presets.strategy(e, x, **cap)
    cfg = backtest.BacktestConfig(strategy=strategy, label=label or preset, **bt)
    return backtest.run(store, cfg, entry_policy or PostGradEntry(e, preset),
                        exit_override or exit_policy(x, strategy, store.dataset))
