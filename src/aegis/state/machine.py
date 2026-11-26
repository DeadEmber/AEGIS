"""Token lifecycle / market-phase state machine.

Market states (token) are separate from position states (accounting.portfolio.PositionStatus).
Transitions are rule-based and justified: every evaluation returns (state, reason, evidence).
UNTRADABLE requires CONFIRMED evidence repeated over time; an API error or missing data yields
UNKNOWN, never UNTRADABLE. Bidirectional transitions are allowed (e.g. COLLAPSE -> RECOVERY).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from aegis.features.library import FeatureVector
from aegis.scoring.heuristics import ScoreSet

ENGINE_VERSION = "state-0.2"


class MarketState(enum.StrEnum):
    DISCOVERED = "DISCOVERED"
    WATCHLIST = "WATCHLIST"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    ACCUMULATION = "ACCUMULATION"
    EXPANSION = "EXPANSION"
    EXPLOSION = "EXPLOSION"
    DISTRIBUTION = "DISTRIBUTION"
    COLLAPSE = "COLLAPSE"
    RESIDUAL = "RESIDUAL"  # kept for compatibility with the spec; used only via position overlay
    RECOVERY = "RECOVERY"
    SECOND_WAVE = "SECOND_WAVE"
    INACTIVE = "INACTIVE"
    UNTRADABLE = "UNTRADABLE"
    UNKNOWN = "UNKNOWN"


HOT_STATES = frozenset({MarketState.EXPANSION, MarketState.EXPLOSION, MarketState.DISTRIBUTION,
                        MarketState.SECOND_WAVE})


@dataclass(frozen=True)
class StateInputs:
    features: FeatureVector | None
    scores: ScoreSet | None
    previous: MarketState
    data_available: bool
    security_verdict: str  # PASS|RISK|BLOCK|UNKNOWN
    security_confirmed_block: bool  # BLOCK based on CONFIRMED facts (not mere unknowns)
    consecutive_no_sell_route: int
    hours_without_sell_route: float
    eligible: bool
    explosion_count: int


@dataclass(frozen=True)
class StateDecision:
    state: MarketState
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    changed: bool = False


def evaluate(x: StateInputs) -> StateDecision:
    def d(state: MarketState, reason: str, **ev: Any) -> StateDecision:
        return StateDecision(state, reason, ev, changed=state != x.previous)

    if x.consecutive_no_sell_route >= 3 and x.hours_without_sell_route >= 2:
        return d(MarketState.UNTRADABLE, "no executable sell route in >=3 checks over >=2h",
                 checks=x.consecutive_no_sell_route, hours=round(x.hours_without_sell_route, 2))
    if x.security_verdict == "BLOCK" and x.security_confirmed_block:
        return d(MarketState.REJECTED, "security engine: confirmed critical finding")
    if not x.data_available or x.features is None:
        return d(MarketState.UNKNOWN, "market data unavailable or stale (not evidence of failure)")
    f, sc = x.features, x.scores
    g = f.get
    ret_h6, ret_h24 = g("ret_h6"), g("ret_h24")
    dd24, ddath, mlow = g("drawdown_from_peak_h24"), g("drawdown_from_ath"), g("multiple_from_low_since_ath")
    bs1, acc = g("buyer_share_h1"), g("acceleration_short")
    prs = sc.prs.value if sc else None
    trades24 = None
    if g("buyer_rate_h24") is not None:
        trades24 = g("buyer_rate_h24") * 24

    if trades24 is not None and trades24 < 5 and (ret_h24 is None or abs(ret_h24) < 0.05):
        return d(MarketState.INACTIVE, "fewer than 5 buyers in 24h and flat price", buyers_24h=trades24)
    if (dd24 is not None and dd24 >= 0.6) or (g("liquidity_change_h1") is not None and g("liquidity_change_h1") <= -0.5):
        return d(MarketState.COLLAPSE, "drawdown >=60% within 24h or liquidity -50% in 1h", dd24=dd24,
                 liq_h1=g("liquidity_change_h1"))
    rebound = ddath is not None and ddath >= 0.6 and mlow is not None and mlow >= 1.5
    exploding = (ret_h24 is not None and ret_h24 >= 1.0) or (ret_h6 is not None and ret_h6 >= 0.5 and (acc or 0) > 0)
    if rebound and exploding and x.explosion_count >= 1:
        return d(MarketState.SECOND_WAVE, "renewed explosion after deep drawdown", dd_ath=ddath, mult_low=mlow)
    if exploding and prs is not None and prs >= 0.55:
        return d(MarketState.DISTRIBUTION, "explosive move with high peak-risk score", prs=prs, ret_h24=ret_h24)
    if exploding:
        return d(MarketState.EXPLOSION, "x2 in 24h or +50% in 6h with positive acceleration", ret_h24=ret_h24,
                 ret_h6=ret_h6, acc=acc)
    if rebound and bs1 is not None and bs1 > 0.5:
        return d(MarketState.RECOVERY, "rebound >=1.5x from post-ATH low with buyer majority", mult_low=mlow)
    if x.previous in (MarketState.EXPLOSION, MarketState.DISTRIBUTION) and prs is not None and prs >= 0.55:
        return d(MarketState.DISTRIBUTION, "post-explosion exhaustion signals", prs=prs)
    if ret_h6 is not None and ret_h6 > 0.15 and bs1 is not None and bs1 > 0.5:
        return d(MarketState.EXPANSION, "+15% in 6h with buyer majority", ret_h6=ret_h6, buyer_share_h1=bs1)
    if ret_h6 is not None and abs(ret_h6) < 0.10 and g("buyer_share_h6") is not None and g("buyer_share_h6") > 0.55:
        return d(MarketState.ACCUMULATION, "flat price with persistent buyer majority", ret_h6=ret_h6)
    if x.security_verdict == "BLOCK":
        return d(MarketState.WATCHLIST, "not buyable: security unverifiable or failed (non-confirmed)")
    if x.eligible:
        return d(MarketState.ELIGIBLE, "passes discovery filters and security")
    return d(MarketState.WATCHLIST, "observed; does not meet eligibility filters")
