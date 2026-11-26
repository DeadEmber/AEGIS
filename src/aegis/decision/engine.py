"""Intelligent Decision Engine (rule layer `decision-0.2`, deterministic and auditable).

Question answered for every token/position each time it is evaluated:
  "Which action reasonably maximises the future liquidable value of the portfolio, given risk,
   costs and alternatives?"

Until a validated model is promoted (Champion-Challenger), the engine combines heuristic scores
with explicit economic rules. It is deterministic given its inputs (seeded randomness for the
exploration sample), and every Decision carries reasons, scores and an inputs hash so any decision
can be reproduced. Generative AI has no authority here.

Exit philosophy: no fixed take-profit ceiling. Recover capital at a net multiple, then let a
residual ride; trim on exhaustion evidence (PRS); protect large winners with a volatility-scaled
trailing floor; exit fully only on critical/security conditions. Stops do not guarantee prices: every
exit is re-priced by the execution engine with current quotes and costs.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aegis.allocation.engine import reassign_unused
from aegis.config import StrategyConfig
from aegis.core.hashing import stable_hash
from aegis.core.money import ONE, ZERO
from aegis.discovery.engine import Eligibility
from aegis.features.library import FeatureVector
from aegis.scoring.heuristics import ScoreSet
from aegis.state.machine import HOT_STATES, MarketState

ENGINE_VERSION = "decision-0.2"


class Action(enum.StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    INCREASE = "INCREASE"
    PARTIAL_SELL = "PARTIAL_SELL"
    FULL_SELL = "FULL_SELL"
    REENTRY = "REENTRY"
    WATCH = "WATCH"
    REJECT = "REJECT"
    WAIT = "WAIT"
    SUSPEND = "SUSPEND"


@dataclass
class Decision:
    action: Action
    token_id: int
    reasons: list[str]
    scores: dict[str, Any] = field(default_factory=dict)
    position_id: int | None = None
    bucket: str | None = None
    fraction: Decimal | None = None
    method: str | None = None  # e.g. 'burn_close'
    priority: float = 0.0
    inputs_hash: str = ""

    def finalize(self, inputs: dict[str, Any]) -> Decision:
        self.inputs_hash = stable_hash({"inputs": inputs, "action": str(self.action), "fraction": self.fraction})
        return self


@dataclass(frozen=True)
class Candidate:
    token_id: int
    address: str
    state: MarketState
    eligibility: Eligibility
    security_verdict: str
    features: FeatureVector
    scores: ScoreSet
    entries: int
    last_exit_at: dt.datetime | None
    liquidity_usd: Decimal | None
    liquidity_at_exit_usd: Decimal | None
    explosion_count: int
    est_roundtrip_cost_pct: Decimal | None = None


@dataclass(frozen=True)
class PositionView:
    position_id: int
    token_id: int
    status: str
    qty: Decimal
    original_qty: Decimal
    cost_basis: Decimal
    invested_total: Decimal
    realized_proceeds: Decimal
    capital_recovered: bool
    peak_price_eur: Decimal | None
    price_eur: Decimal | None
    liquidation_eur: Decimal | None  # full exit, net of costs, excl. deposit refund
    liquidity_eur: Decimal | None
    deposit_eur: Decimal
    state: MarketState
    security_verdict: str
    features: FeatureVector | None
    scores: ScoreSet | None
    last_partial_at: dt.datetime | None
    dead_since: dt.datetime | None
    data_stale: bool


class DecisionEngine:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    # ------------------------------------------------------------------ exits
    def evaluate_position(self, p: PositionView, now: dt.datetime) -> Decision:
        ex = self.cfg.exits
        sc = p.scores.as_dict() if p.scores else {}
        inputs = {"p": p.position_id, "price": p.price_eur, "liq": p.liquidation_eur, "state": str(p.state),
                  "sec": p.security_verdict, "scores": sc, "status": p.status}

        def dec(action: Action, reasons: list[str], fraction: Decimal | None = None, method: str | None = None,
                priority: float = 0.0) -> Decision:
            return Decision(action, p.token_id, reasons, sc, p.position_id, None, fraction, method, priority).finalize(inputs)

        if p.data_stale or p.price_eur is None:
            return dec(Action.HOLD, ["no fresh market data: no action on unknowns"], priority=0.5)
        sellable = p.liquidation_eur is not None and p.liquidation_eur >= ex.min_sale_eur
        current_fraction = p.qty / p.original_qty if p.original_qty > 0 else ONE
        to_residual = max(ZERO, ONE - ex.residual_min_fraction / current_fraction) if current_fraction > 0 else ZERO
        room = to_residual  # max fraction sellable while keeping the residual
        # A residual is a deliberate long-tail ticket: no further trimming, only critical exits.
        if p.status == "RESIDUAL":
            to_residual = room = ZERO
        elif to_residual < ex.min_sell_fraction:
            to_residual = ZERO
        if p.liquidation_eur is not None and p.liquidation_eur * to_residual < ex.min_sale_eur:
            to_residual = ZERO

        # 1) security deterioration (e.g. authority re-enabled, sell route disappearing)
        if p.security_verdict == "BLOCK" and sellable:
            return dec(Action.FULL_SELL, ["security verdict BLOCK on held token: exit while sellable"], ONE, priority=10)
        # 2) critical liquidity / collapse
        critical_liq = p.liquidity_eur is not None and p.liquidity_eur < ex.critical_liquidity_eur
        if critical_liq or p.state in (MarketState.UNTRADABLE,):
            if sellable:
                return dec(Action.FULL_SELL, [f"critical liquidity ({p.liquidity_eur} EUR) or untradable"], ONE,
                           priority=9)
            if p.dead_since and now - p.dead_since >= dt.timedelta(days=ex.dust_close_after_days) and p.deposit_eur > 0:
                return dec(Action.FULL_SELL, ["dead position: burn tokens and close account to recover rent deposit"],
                           ONE, method="burn_close", priority=1)
            return dec(Action.HOLD, ["unsellable at present: waiting (no fabricated exit)"], priority=2)
        if not sellable:
            return dec(Action.HOLD, ["liquidation value below minimum sale; holding"], priority=0.5)
        assert p.liquidation_eur is not None
        total_multiple = (p.liquidation_eur + p.realized_proceeds) / p.invested_total if p.invested_total > 0 else ONE
        prs = p.scores.prs.value if p.scores else None

        # 2b) deterioration: recover what is left of a structurally failing loser while it is still sellable
        if ex.deterioration_exit and total_multiple < ONE and p.features is not None:
            f = p.features
            drain = f.get("liquidity_change_h24")
            bs24 = f.get("buyer_share_h24")
            draining = drain is not None and drain <= -float(ex.deterioration_liquidity_drop_h24) and \
                bs24 is not None and bs24 < float(ex.deterioration_max_buyer_share_h24)
            if p.state == MarketState.COLLAPSE or draining:
                return dec(Action.FULL_SELL, [f"deteriorating position (state {p.state}, liquidity 24h {drain}, "
                                              f"buyer share 24h {bs24}): recover remaining capital"], ONE, priority=8)
        if ex.stop_loss_multiple is not None and total_multiple <= ex.stop_loss_multiple:
            return dec(Action.FULL_SELL, [f"naive stop-loss at net x{total_multiple:.2f}"], ONE, priority=8)
        # 3) capital recovery at a NET multiple: sell just enough to get the invested EUR back
        if not p.capital_recovered and total_multiple >= ex.capital_recovery_multiple:
            need = p.invested_total - p.realized_proceeds
            frac = min(room, max(ZERO, need / p.liquidation_eur * Decimal("1.03")))
            if frac > 0 and frac * p.liquidation_eur >= ex.min_sale_eur:  # value-based: tiny fractions at x40 are fine
                return dec(Action.PARTIAL_SELL, [f"capital recovery at net x{total_multiple:.2f}; keep residual"],
                           frac, priority=6)
        # 4) volatility-scaled trailing floor for large winners
        if p.peak_price_eur and p.peak_price_eur > 0:
            # multiple of the original entry implied by the observed peak (cost per unit incl. fees)
            unit_cost = p.invested_total / p.original_qty if p.original_qty > 0 else ZERO
            peak_mult = p.peak_price_eur / unit_cost if unit_cost > 0 else ZERO
            if total_multiple >= ex.trailing_activate_multiple or peak_mult >= ex.trailing_activate_multiple:
                vol = (p.features.get("realized_vol_h6") if p.features else None) or 0.0
                width = min(float(ex.trailing_max_pct), max(float(ex.trailing_min_pct), float(ex.trailing_vol_k) * vol))
                dd = 1 - float(p.price_eur / p.peak_price_eur)
                if dd >= width and to_residual > 0:
                    return dec(Action.PARTIAL_SELL, [f"trailing floor hit: -{dd:.0%} from peak (width {width:.0%})"],
                               to_residual, priority=7)
        # 5) peak-risk driven trimming (no fixed ceiling)
        if prs is not None and total_multiple > ONE and p.state in HOT_STATES | {MarketState.DISTRIBUTION}:
            recent = p.last_partial_at is not None and now - p.last_partial_at < dt.timedelta(minutes=30)
            if prs >= float(ex.prs_full_threshold) and to_residual > 0:
                return dec(Action.PARTIAL_SELL, [f"PRS {prs:.2f} >= {ex.prs_full_threshold}: sell to residual"],
                           to_residual, priority=8)
            if prs >= float(ex.prs_partial_threshold) and not recent and to_residual > 0:
                frac = min(to_residual, ex.partial_sell_fraction)
                return dec(Action.PARTIAL_SELL, [f"PRS {prs:.2f}: partial monetisation of exhaustion risk"], frac,
                           priority=5)
        if p.state == MarketState.COLLAPSE and to_residual > 0 and total_multiple > ONE:
            return dec(Action.PARTIAL_SELL, ["collapse while still in profit: secure value, keep residual"],
                       to_residual, priority=7)
        return dec(Action.HOLD, [f"hold: net x{total_multiple:.2f}, state {p.state}, PRS {prs}"],
                   priority=3 if p.state in HOT_STATES else 1)

    # ------------------------------------------------------------------ entries
    def _base_rejections(self, c: Candidate) -> list[str]:
        r = []
        if c.security_verdict not in ("PASS", "RISK"):
            r.append(f"security:{c.security_verdict}")
        if c.state in (MarketState.REJECTED, MarketState.UNTRADABLE, MarketState.UNKNOWN, MarketState.COLLAPSE,
                       MarketState.INACTIVE):
            r.append(f"state:{c.state}")
        if c.est_roundtrip_cost_pct is not None and c.est_roundtrip_cost_pct > self.cfg.execution.max_cost_to_ticket_pct:
            r.append(f"cost:{c.est_roundtrip_cost_pct:.1f}%")
        return r

    def is_reentry_candidate(self, c: Candidate, now: dt.datetime) -> tuple[bool, list[str]]:
        rc = self.cfg.reentry
        if c.entries < 1 or c.last_exit_at is None:
            return False, ["never held"]
        why = []
        if c.entries >= rc.max_entries_per_token:
            why.append("max entries reached")
        if (now - c.last_exit_at).total_seconds() / 3600 < float(rc.min_hours_since_exit):
            why.append("too soon after exit")
        f = c.features
        dd, ml, bs = f.get("drawdown_from_ath"), f.get("multiple_from_low_since_ath"), f.get("buyer_share_h1")
        if dd is None or dd < float(rc.min_drawdown_from_ath):
            why.append(f"drawdown {dd} < {rc.min_drawdown_from_ath}")
        if ml is None or ml < float(rc.min_rebound_from_low):
            why.append(f"rebound {ml} < {rc.min_rebound_from_low}")
        if bs is None or bs < float(rc.min_buyer_share_h1):
            why.append(f"buyer share {bs} < {rc.min_buyer_share_h1}")
        if c.liquidity_usd is None or not c.liquidity_at_exit_usd or c.liquidity_at_exit_usd <= 0 or \
                c.liquidity_usd / c.liquidity_at_exit_usd < rc.min_liquidity_retention:
            why.append("liquidity not retained")
        if c.state not in (MarketState.RECOVERY, MarketState.SECOND_WAVE, MarketState.EXPANSION, MarketState.ACCUMULATION):
            why.append(f"state {c.state}")
        return (not why), why

    def plan_entries(self, candidates: list[Candidate], slots: dict[str, int], now: dt.datetime, *,
                     held_tokens: set[int], seed_material: str) -> tuple[list[Decision], list[Decision]]:
        """Returns (buy decisions, non-buy decisions WATCH/REJECT/WAIT) — both auditable."""
        buys: list[Decision] = []
        others: list[Decision] = []
        reentry: list[Candidate] = []
        fresh: list[Candidate] = []
        for c in candidates:
            if c.token_id in held_tokens:
                continue
            sc = c.scores.as_dict()
            inputs = {"t": c.token_id, "state": str(c.state), "sec": c.security_verdict, "scores": sc,
                      "elig": c.eligibility.failed}
            rej = self._base_rejections(c)
            if rej:
                others.append(Decision(Action.REJECT, c.token_id, rej, sc).finalize(inputs))
                continue
            if c.entries >= 1:
                ok, why = self.is_reentry_candidate(c, now)
                if ok:
                    reentry.append(c)
                else:
                    others.append(Decision(Action.WATCH, c.token_id, ["former position; re-entry conditions unmet",
                                                                       *why], sc).finalize(inputs))
                continue
            if not c.eligibility.eligible:
                others.append(Decision(Action.REJECT, c.token_id, [f"filter:{n}" for n in c.eligibility.failed],
                                       sc).finalize(inputs))
                continue
            fresh.append(c)

        def ces(c: Candidate) -> float:  # Capital Efficiency Score (heuristic)
            s = c.scores
            epi = s.epi.value or 0.0
            clean = 1 - (s.mri.value if s.mri.value is not None else 0.5)
            conf = s.mci.value if s.mci.value is not None else 0.5
            cost = float(c.est_roundtrip_cost_pct or Decimal(5))
            return epi * clean * conf / (1 + cost / 10)

        # selection: best CES with a minimum heuristic quality
        ranked = sorted((c for c in fresh if ((c.scores.epi.value or 0) >= 0.40 and c.security_verdict == "PASS")
                         or ((c.scores.epi.value or 0) >= 0.55 and (c.scores.mri.value or 1) < 0.5)),
                        key=ces, reverse=True)
        chosen: set[int] = set()

        def take(c: Candidate, action: Action, bucket: str, why: str) -> None:
            sc = c.scores.as_dict()
            inputs = {"t": c.token_id, "bucket": bucket, "scores": sc, "seed": seed_material}
            buys.append(Decision(action, c.token_id, [why], {**sc, "CES": round(ces(c), 4)}, bucket=bucket,
                                 priority=ces(c)).finalize(inputs))
            chosen.add(c.token_id)

        demand = {"selection": len(ranked), "second_chance": len(reentry), "exploration": len(fresh)}
        use = reassign_unused(slots, demand)
        for c in ranked[: use["selection"]]:
            take(c, Action.BUY, "selection", f"selection: top CES among {len(ranked)} qualified")
        for c in sorted(reentry, key=lambda c: c.scores.swp.value or 0, reverse=True)[: use["second_chance"]]:
            take(c, Action.REENTRY, "second_chance", f"second chance: SWP_h {c.scores.swp.value}")
        pool = [c for c in fresh if c.token_id not in chosen]
        rng = random.Random(int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16))
        rng.shuffle(pool)
        for c in pool[: use["exploration"]]:
            take(c, Action.BUY, "exploration", "exploration: uniform random among eligible & secure (unbiased sample)")
        for c in fresh + reentry:
            if c.token_id not in chosen:
                others.append(Decision(Action.WAIT if sum(slots.values()) == 0 else Action.WATCH, c.token_id,
                                       ["eligible but not selected (budget/pacing/rank)"],
                                       c.scores.as_dict()).finalize({"t": c.token_id, "seed": seed_material}))
        return buys, others
