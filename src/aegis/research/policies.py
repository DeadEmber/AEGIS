"""Entry and exit policies for historical research (block A reference strategies, block C/D variants).

Policies see only point-in-time data: the current observation, the token's past observations and
attributes already known (`MarketView`). Retrospective outcomes are never passed in. Thresholds live in
`StrategyConfig.postgrad_entry` / `postgrad_exit` (no economic constant is hard-coded here).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from aegis.config import PostGradEntryConfig, PostGradExitConfig, StrategyConfig
from aegis.core.money import ONE, ZERO
from aegis.decision.engine import Action, DecisionEngine, PositionView
from aegis.features.library import FeatureContext, compute
from aegis.history.store import MarketView, Obs, TokenTrack, history_points, to_pool_observation, ts
from aegis.scoring.heuristics import score_all
from aegis.state.machine import MarketState, StateInputs, evaluate


@dataclass
class Context:
    """Point-in-time market context maintained by the simulator."""

    opportunities_24h: int = 0  # distinct tokens that passed the base filters in the last 24 h
    observed_24h: int = 0  # distinct tokens that graduated and were first observed in the last 24 h (coverage)
    last_exit: dict[str, float] = field(default_factory=dict)  # mint -> time of the last full exit

    @property
    def opportunity_rate_24h(self) -> float | None:
        return self.opportunities_24h / self.observed_24h if self.observed_24h else None


@dataclass
class Held:
    """In-memory mirror of an open position (the ledger/DB remains the source of truth)."""

    position_id: int
    token_id: int
    mint: str
    opened_at: float
    invested: Decimal
    realized: Decimal
    qty: Decimal
    original_qty: Decimal
    capital_recovered: bool
    status: str
    deposit: Decimal
    entry_since_grad: float | None
    peak_multiple: float = 0.0  # running max of the net liquidable multiple observed while held
    peak_price_usd: float = 0.0
    last_obs_t: float = 0.0
    last_multiple: float | None = None
    partials: int = 0
    last_partial_t: float | None = None
    state: MarketState = MarketState.DISCOVERED
    trail: list[tuple[float, float]] = field(default_factory=list)  # (t, multiple) path while held


@dataclass(frozen=True)
class ExitAction:
    fraction: Decimal
    reason: str
    critical: bool = False


class EntryPolicy(Protocol):
    name: str

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]: ...
    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None: ...


class ExitPolicy(Protocol):
    name: str

    def decide(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float,
               now: float) -> ExitAction | None: ...


def _ge(x: float | None, threshold: Decimal | None) -> bool | None:
    if threshold is None:
        return True
    if x is None:
        return None  # unknown: never a pass
    return x >= float(threshold)


class PostGradEntry:
    """Rule-based post-graduation entry (reference strategies 'basic' / 'strict' / 'strict+context')."""

    def __init__(self, cfg: PostGradEntryConfig, name: str = "postgrad"):
        self.cfg = cfg
        self.name = name

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        c = self.cfg
        fail: list[str] = []
        sg = o.since_grad_s
        if sg is None:
            return False, ["since_graduation_unknown"]
        if sg < c.min_seconds_since_graduation or sg > c.max_seconds_since_graduation:
            return False, ["outside_entry_window"]
        if not o.tradable:
            return False, ["not_tradable"]
        liq = o.liq_usd / eur_usd if o.liq_usd is not None else None
        mc = o.mcap_usd / eur_usd if o.mcap_usd is not None else None
        checks = {
            "liquidity": _ge(liq, c.min_liquidity_eur),
            "mcap_min": _ge(mc, c.min_market_cap_eur),
            "mcap_max": True if c.max_market_cap_eur is None else (None if mc is None else mc <= float(c.max_market_cap_eur)),
            "volume_m5": _ge(o.vol[0], c.min_volume_m5_usd),
            "buy_sell_m5": _ge(o.buy_sell_ratio(0), c.min_buy_sell_ratio_m5),
            "price_change_m5": _ge(o.chg[0], c.min_price_change_m5_pct),
        }
        for k, v in checks.items():
            if v is None:
                fail.append(f"{k}:unknown")
            elif not v:
                fail.append(k)
        return not fail, fail

    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None:
        if entries >= self.cfg.max_entries_per_token:
            return None
        ok, _ = self.base_pass(tr, o, eur_usd)
        if not ok:
            return None
        c = self.cfg
        if c.context_min_opportunities_24h is not None and ctx.opportunities_24h < c.context_min_opportunities_24h:
            return None
        if c.context_min_opportunity_rate_24h is not None:
            r = ctx.opportunity_rate_24h
            if r is None or r < float(c.context_min_opportunity_rate_24h):
                return None
        return [f"{self.name}: filters passed at +{o.since_grad_s:.0f}s"]


class HoldExit:
    name = "hold"

    def decide(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float,
               now: float) -> ExitAction | None:
        return None


class RulesExit:
    """Stops / take-profit / capital recovery / trailing on the NET LIQUIDABLE multiple (impact and fees
    included), so a 'trailing stop' can only trigger on values that were actually realisable."""

    def __init__(self, cfg: PostGradExitConfig, name: str = "rules", min_sale_eur: Decimal = Decimal("0.10")):
        self.cfg = cfg
        self.name = name
        self.min_sale = min_sale_eur

    def decide(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float,
               now: float) -> ExitAction | None:
        c = self.cfg
        if liq_eur < self.min_sale:  # unsellable above costs: hold (a dead ticket's deposit stays recoverable)
            return None
        if c.max_hold_seconds is not None and now - h.opened_at >= c.max_hold_seconds:
            return ExitAction(ONE, f"max hold {c.max_hold_seconds}s")
        if c.stop_loss_multiple is not None and multiple <= float(c.stop_loss_multiple):
            return ExitAction(ONE, f"stop at net x{multiple:.2f}")
        if h.status == "RESIDUAL":
            return None
        if c.take_profit_multiple is not None and multiple >= float(c.take_profit_multiple):
            return ExitAction(self._to_residual(h), f"take profit at net x{multiple:.2f}")
        if c.recovery_multiple is not None and not h.capital_recovered and multiple >= float(c.recovery_multiple) \
                and liq_eur > 0:
            need = h.invested - h.realized
            frac = min(Decimal("0.95"), max(ZERO, need / liq_eur * Decimal("1.03")))
            if frac > 0:
                return ExitAction(frac, f"capital recovery at net x{multiple:.2f}")
        if c.trailing_pct is not None and c.trailing_activate_multiple is not None and \
                h.peak_multiple >= float(c.trailing_activate_multiple) and \
                multiple <= h.peak_multiple * (1 - float(c.trailing_pct)):
            return ExitAction(self._to_residual(h), f"trailing: x{multiple:.2f} vs peak x{h.peak_multiple:.2f}")
        return None

    def _to_residual(self, h: Held) -> Decimal:
        r = self.cfg.residual_fraction
        if r <= 0 or h.original_qty <= 0:
            return ONE
        current = h.qty / h.original_qty
        return ONE if current <= r else max(ZERO, ONE - r / current)


class AegisExit:
    """The production DecisionEngine (decision-0.2) driven by point-in-time features of the history."""

    name = "aegis"

    def __init__(self, cfg: StrategyConfig, source: str):
        self.engine = DecisionEngine(cfg)
        self.source = source

    def decide(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float,
               now: float) -> ExitAction | None:
        tr = view.track(h.mint)
        hist = view.history(h.mint)
        obs = to_pool_observation(tr, o, self.source)
        prior = [x for x in hist if x.t < o.t]
        peak = max((x.price_usd for x in hist if x.price_usd is not None), default=None)
        low_since = None
        if peak is not None:
            i_peak = max(i for i, x in enumerate(hist) if x.price_usd == peak)
            low_since = min((x.price_usd for x in hist[i_peak:] if x.price_usd is not None), default=None)
        fv = compute(FeatureContext(
            as_of=ts(now), obs=obs, history=history_points(prior),
            token_created_at=ts(tr.created_at) if tr.created_at else None,
            ath_price_usd=_dd(peak), low_since_ath_usd=_dd(low_since),
            first_seen_at=ts(hist[0].t) if hist else None, market_state=str(h.state),
        ))
        sc = score_all(fv, data_age_minutes=max(0.0, (now - o.t) / 60))
        st = evaluate(StateInputs(features=fv, scores=sc, previous=h.state, data_available=True,
                                  security_verdict="PASS", security_confirmed_block=False,
                                  consecutive_no_sell_route=0, hours_without_sell_route=0.0, eligible=True,
                                  explosion_count=0)).state
        h.state = st
        price_eur = _dd(o.price_usd / eur_usd) if o.price_usd else None
        unit_peak = _dd(h.peak_price_usd / eur_usd) if h.peak_price_usd else None
        pv = PositionView(
            position_id=h.position_id, token_id=h.token_id, status=h.status, qty=h.qty, original_qty=h.original_qty,
            cost_basis=h.invested, invested_total=h.invested, realized_proceeds=h.realized,
            capital_recovered=h.capital_recovered, peak_price_eur=unit_peak, price_eur=price_eur, liquidation_eur=liq_eur,
            liquidity_eur=_dd(o.liq_usd / eur_usd) if o.liq_usd else None, deposit_eur=h.deposit, state=st,
            security_verdict="PASS", features=fv, scores=sc, last_partial_at=ts(h.last_partial_t) if h.last_partial_t else None,
            dead_since=None, data_stale=False, opened_at=ts(h.opened_at))
        d = self.engine.evaluate_position(pv, ts(now))
        if d.action in (Action.PARTIAL_SELL, Action.FULL_SELL) and d.fraction is not None and d.method != "burn_close":
            return ExitAction(d.fraction, "; ".join(d.reasons)[:200], d.critical)
        return None


def _dd(x: float | None) -> Decimal | None:
    if x is None or not math.isfinite(x):
        return None
    return Decimal(repr(x))
