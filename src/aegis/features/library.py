"""Feature Engineering Engine — point-in-time feature library (feature set `fs-0.2`).

Every feature has a registered spec (definition, unit, horizon, source, validity). Computation only
uses information known at `as_of` (observations with observed_at <= as_of), which is asserted.
Missing inputs produce an explicit missing reason, never a zero.

Horizons: provider windows give m5/h1/h6/h24 directly; our own snapshot history adds liquidity
change, realised volatility, drawdowns and ATH-relative measures (limited to the period AEGIS has
observed the token — flagged by `history_hours`).
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from aegis.marketdata.models import PoolObservation, TokenProfile

FEATURE_SET_VERSION = "fs-0.2"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    unit: str
    horizon: str
    source: str
    definition: str
    validity: str
    group: str


@dataclass(frozen=True)
class HistoryPoint:
    observed_at: dt.datetime
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    pool_address: str


@dataclass
class FeatureContext:
    as_of: dt.datetime
    obs: PoolObservation
    history: Sequence[HistoryPoint]  # same token, sorted ascending, observed_at <= as_of
    profile: TokenProfile | None = None
    token_created_at: dt.datetime | None = None
    ath_price_usd: Decimal | None = None
    ath_at: dt.datetime | None = None
    low_since_ath_usd: Decimal | None = None
    first_seen_at: dt.datetime | None = None
    market_state: str | None = None  # state known BEFORE this evaluation (point-in-time)
    market_state_since: dt.datetime | None = None
    active_explosion_started_at: dt.datetime | None = None
    explosion_count: int | None = None
    last_exit_at: dt.datetime | None = None


@dataclass
class FeatureVector:
    as_of: dt.datetime
    version: str
    values: dict[str, float] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> float | None:
        return self.values.get(name)

    def coverage(self) -> float:
        total = len(self.values) + len(self.missing)
        return len(self.values) / total if total else 0.0


REGISTRY: dict[str, FeatureSpec] = {}
_COMPUTE: dict[str, Callable[[FeatureContext], float | tuple[None, str] | None]] = {}


def feature(name: str, unit: str, horizon: str, source: str, definition: str, validity: str, group: str):
    def deco(fn):  # type: ignore[no-untyped-def]
        REGISTRY[name] = FeatureSpec(name, unit, horizon, source, definition, validity, group)
        _COMPUTE[name] = fn
        return fn

    return deco


class Missing(Exception):
    pass


class LookAheadError(ValueError):
    """Raised when any input is newer than `as_of`. Never swallowed."""


def _f(x: Decimal | int | None, why: str) -> float:
    if x is None:
        raise Missing(why)
    v = float(x)
    if not math.isfinite(v):
        raise Missing(f"{why}:non_finite")
    return v


def _ret(ctx: FeatureContext, w: str) -> float:
    pc = ctx.obs.w(w).price_change_pct
    return _f(pc, f"price_change_{w}_missing") / 100.0


def _log_rate_per_hour(ret: float, hours: float) -> float:
    if ret <= -1:
        raise Missing("total_loss")
    return math.log1p(ret) / hours


_HOURS = {"m5": 5 / 60, "h1": 1.0, "h6": 6.0, "h24": 24.0}

# ---------------------------------------------------------------- A. price & time behaviour
for _w in ("m5", "h1", "h6", "h24"):
    def _mk(w: str):  # type: ignore[no-untyped-def]
        @feature(f"ret_{w}", "fraction", w, "provider window", f"Simple return over {w}: P_t/P_(t-{w}) - 1",
                 "provider price_change present", "price")
        def _r(ctx: FeatureContext) -> float:
            return _ret(ctx, w)

        @feature(f"velocity_{w}", "log-return/hour", w, "provider window",
                 f"ln(1+ret_{w}) / hours({w}) — average continuous price speed", "ret_w > -1", "price")
        def _v(ctx: FeatureContext) -> float:
            return _log_rate_per_hour(_ret(ctx, w), _HOURS[w])
    _mk(_w)


@feature("acceleration_short", "log-return/hour", "m5 vs h1", "derived",
         "velocity_m5 - velocity_h1 (>0: price speeding up)", "both velocities", "price")
def _acc_s(ctx: FeatureContext) -> float:
    return _log_rate_per_hour(_ret(ctx, "m5"), _HOURS["m5"]) - _log_rate_per_hour(_ret(ctx, "h1"), 1.0)


@feature("acceleration_long", "log-return/hour", "h1 vs h6", "derived", "velocity_h1 - velocity_h6",
         "both velocities", "price")
def _acc_l(ctx: FeatureContext) -> float:
    return _log_rate_per_hour(_ret(ctx, "h1"), 1.0) - _log_rate_per_hour(_ret(ctx, "h6"), 6.0)


@feature("jerk", "log-return/hour", "m5/h1/h6", "derived", "acceleration_short - acceleration_long",
         "both accelerations", "price")
def _jerk(ctx: FeatureContext) -> float:
    return _acc_s(ctx) - _acc_l(ctx)


def _points(ctx: FeatureContext, hours: float) -> list[HistoryPoint]:
    start = ctx.as_of - dt.timedelta(hours=hours)
    pts = [p for p in ctx.history if p.observed_at >= start and p.price_usd is not None and p.price_usd > 0]
    if ctx.obs.price_usd is not None and ctx.obs.price_usd > 0:
        pts.append(HistoryPoint(ctx.as_of, ctx.obs.price_usd, ctx.obs.liquidity_usd, ctx.obs.pool_address))
    return pts


@feature("realized_vol_h6", "log-return/sqrt(hour)", "h6", "own snapshots",
         "sqrt(mean(r_i^2/dt_i)) over consecutive snapshots in 6h (irregular sampling normalised)",
         ">=4 snapshots in window", "price")
def _rv(ctx: FeatureContext) -> float:
    pts = _points(ctx, 6)
    if len(pts) < 4:
        raise Missing("insufficient_history")
    acc = []
    for a, b in itertools.pairwise(pts):
        dt_h = (b.observed_at - a.observed_at).total_seconds() / 3600
        if dt_h <= 0:
            continue
        r = math.log(float(b.price_usd) / float(a.price_usd))
        acc.append(r * r / dt_h)
    if len(acc) < 3:
        raise Missing("insufficient_history")
    return math.sqrt(sum(acc) / len(acc))


@feature("drawdown_from_peak_h24", "fraction", "h24", "own snapshots",
         "1 - P_now / max(P) over the last 24h of observations", ">=2 snapshots", "price")
def _dd24(ctx: FeatureContext) -> float:
    pts = _points(ctx, 24)
    if len(pts) < 2:
        raise Missing("insufficient_history")
    peak = max(float(p.price_usd) for p in pts)
    return 1 - float(pts[-1].price_usd) / peak


@feature("drawdown_from_ath", "fraction", "since first seen", "token state",
         "1 - P_now / ATH observed by AEGIS (not the true ATH if the token predates observation)",
         "ATH known", "survival")
def _ddath(ctx: FeatureContext) -> float:
    ath = _f(ctx.ath_price_usd, "ath_unknown")
    p = _f(ctx.obs.price_usd, "price_missing")
    return max(0.0, 1 - p / ath) if ath > 0 else 0.0


@feature("multiple_from_low_since_ath", "ratio", "since ATH", "token state",
         "P_now / min(P) observed after the ATH", "low known", "survival")
def _mlow(ctx: FeatureContext) -> float:
    low = _f(ctx.low_since_ath_usd, "low_unknown")
    if low <= 0:
        raise Missing("low_nonpositive")
    return _f(ctx.obs.price_usd, "price_missing") / low


@feature("hours_since_ath", "hours", "since ATH", "token state", "(as_of - ath_at) in hours", "ATH time known",
         "survival")
def _hath(ctx: FeatureContext) -> float:
    if ctx.ath_at is None:
        raise Missing("ath_unknown")
    return (ctx.as_of - ctx.ath_at).total_seconds() / 3600


@feature("token_age_hours", "hours", "-", "chain/provider", "as_of - token creation (pool creation if unknown)",
         "creation time known", "context")
def _age(ctx: FeatureContext) -> float:
    created = ctx.token_created_at or ctx.obs.pool_created_at
    if created is None:
        raise Missing("creation_unknown")
    return (ctx.as_of - created).total_seconds() / 3600


@feature("history_hours", "hours", "-", "own snapshots", "hours since AEGIS first observed the token",
         "first_seen known", "context")
def _hist(ctx: FeatureContext) -> float:
    if ctx.first_seen_at is None:
        raise Missing("first_seen_unknown")
    return (ctx.as_of - ctx.first_seen_at).total_seconds() / 3600


# ---------------------------------------------------------------- A2. temporal / lifecycle (observed durations only)
def _hours_since(ctx: FeatureContext, t: dt.datetime | None, why: str) -> float:
    if t is None:
        raise Missing(why)
    if t > ctx.as_of:
        raise LookAheadError("look-ahead: timestamp after as_of")
    return (ctx.as_of - t).total_seconds() / 3600


@feature("hours_in_state", "hours", "since state change", "token state",
         "time spent in the market state held before this evaluation", "state timestamp known", "temporal")
def _his(ctx: FeatureContext) -> float:
    return _hours_since(ctx, ctx.market_state_since, "state_since_unknown")


@feature("hours_since_explosion_start", "hours", "current episode", "explosion_events",
         "duration so far of the ACTIVE explosion episode (observed, not predicted)", "an episode is open",
         "temporal")
def _hse(ctx: FeatureContext) -> float:
    return _hours_since(ctx, ctx.active_explosion_started_at, "no_active_explosion")


@feature("hours_since_last_exit", "hours", "since exit", "positions", "time since AEGIS fully exited this token",
         "token previously held", "temporal")
def _hsx(ctx: FeatureContext) -> float:
    return _hours_since(ctx, ctx.last_exit_at, "never_exited")


@feature("explosion_count", "episodes", "since first seen", "explosion_events",
         "number of explosion episodes observed before as_of (waves)", "token state", "temporal")
def _exc(ctx: FeatureContext) -> float:
    if ctx.explosion_count is None:
        raise Missing("unknown")
    return float(ctx.explosion_count)


# ---------------------------------------------------------------- B. flows & demand quality
for _w in ("m5", "h1", "h6", "h24"):
    def _mk2(w: str):  # type: ignore[no-untyped-def]
        @feature(f"trade_imbalance_{w}", "fraction [-1,1]", w, "provider window",
                 f"(buys - sells)/(buys + sells) by COUNT over {w}. AMM swaps: not proof of external capital",
                 "buys+sells > 0", "flows")
        def _imb(ctx: FeatureContext) -> float:
            s = ctx.obs.w(w)
            b, se = _f(s.buys, "buys_missing"), _f(s.sells, "sells_missing")
            if b + se == 0:
                raise Missing("no_trades")
            return (b - se) / (b + se)

        @feature(f"buyer_share_{w}", "fraction", w, "provider window",
                 f"buyers/(buyers+sellers) — unique ADDRESSES, not people, over {w}", "buyers+sellers>0", "flows")
        def _bs(ctx: FeatureContext) -> float:
            s = ctx.obs.w(w)
            b, se = _f(s.buyers, "buyers_missing"), _f(s.sellers, "sellers_missing")
            if b + se == 0:
                raise Missing("no_traders")
            return b / (b + se)

        @feature(f"buyer_rate_{w}", "addresses/hour", w, "provider window", f"buyers_{w} / hours({w})",
                 "buyers present", "flows")
        def _br(ctx: FeatureContext) -> float:
            return _f(ctx.obs.w(w).buyers, "buyers_missing") / _HOURS[w]

        @feature(f"avg_trade_usd_{w}", "USD", w, "provider window", f"volume_{w}/(buys+sells)", "trades>0", "flows")
        def _ats(ctx: FeatureContext) -> float:
            s = ctx.obs.w(w)
            n = _f(s.buys, "buys_missing") + _f(s.sells, "sells_missing")
            if n == 0:
                raise Missing("no_trades")
            return _f(s.volume_usd, "volume_missing") / n
    _mk2(_w)


@feature("buyer_growth_short", "ratio-1", "m5 vs h1", "derived",
         "buyer_rate_m5 / buyer_rate_h1 - 1 (>0: new buyers arriving faster than the hourly average)",
         "buyer_rate_h1 > 0", "flows")
def _bg_s(ctx: FeatureContext) -> float:
    m5 = _f(ctx.obs.w("m5").buyers, "buyers_missing") * 12
    h1 = _f(ctx.obs.w("h1").buyers, "buyers_missing")
    if h1 == 0:
        raise Missing("no_buyers_h1")
    return m5 / h1 - 1


@feature("buyer_growth_long", "ratio-1", "h1 vs h6", "derived", "buyer_rate_h1 / buyer_rate_h6 - 1",
         "buyer_rate_h6 > 0", "flows")
def _bg_l(ctx: FeatureContext) -> float:
    h1 = _f(ctx.obs.w("h1").buyers, "buyers_missing")
    h6 = _f(ctx.obs.w("h6").buyers, "buyers_missing") / 6
    if h6 == 0:
        raise Missing("no_buyers_h6")
    return h1 / h6 - 1


@feature("trades_per_address_h24", "trades/address", "h24", "provider window",
         "(buys+sells)/(buyers+sellers) — high values indicate bot concentration", "traders>0", "flows")
def _tpa(ctx: FeatureContext) -> float:
    s = ctx.obs.w("h24")
    n = _f(s.buys, "buys_missing") + _f(s.sells, "sells_missing")
    a = _f(s.buyers, "buyers_missing") + _f(s.sellers, "sellers_missing")
    if a == 0:
        raise Missing("no_traders")
    return n / a


# ---------------------------------------------------------------- D. liquidity & execution
@feature("liquidity_usd", "USD", "now", "provider", "pool two-sided reserve value", "present", "liquidity")
def _liq(ctx: FeatureContext) -> float:
    return _f(ctx.obs.liquidity_usd, "liquidity_missing")


@feature("volume_to_liquidity_h24", "ratio", "h24", "provider", "volume_h24 / liquidity", "liquidity>0", "liquidity")
def _vl(ctx: FeatureContext) -> float:
    liq = _f(ctx.obs.liquidity_usd, "liquidity_missing")
    if liq <= 0:
        raise Missing("liquidity_nonpositive")
    return _f(ctx.obs.w("h24").volume_usd, "volume_missing") / liq


@feature("liquidity_to_fdv", "ratio", "now", "provider", "liquidity / FDV", "fdv>0", "liquidity")
def _lf(ctx: FeatureContext) -> float:
    fdv = _f(ctx.obs.fdv_usd, "fdv_missing")
    if fdv <= 0:
        raise Missing("fdv_nonpositive")
    return _f(ctx.obs.liquidity_usd, "liquidity_missing") / fdv


def _liq_change(ctx: FeatureContext, hours: float) -> float:
    now = _f(ctx.obs.liquidity_usd, "liquidity_missing")
    target = ctx.as_of - dt.timedelta(hours=hours)
    tol = dt.timedelta(hours=max(0.25, hours * 0.5))
    cands = [p for p in ctx.history if p.pool_address == ctx.obs.pool_address and p.liquidity_usd is not None
             and abs(p.observed_at - target) <= tol]
    if not cands:
        raise Missing("no_reference_snapshot")
    ref = min(cands, key=lambda p: abs(p.observed_at - target))
    if ref.liquidity_usd <= 0:
        raise Missing("ref_liquidity_nonpositive")
    return now / float(ref.liquidity_usd) - 1


@feature("liquidity_change_h1", "fraction", "h1", "own snapshots (same pool)", "L_now / L_(t-1h) - 1",
         "same-pool snapshot within ±30min of t-1h", "liquidity")
def _lc1(ctx: FeatureContext) -> float:
    return _liq_change(ctx, 1)


@feature("liquidity_change_h24", "fraction", "h24", "own snapshots (same pool)", "L_now / L_(t-24h) - 1",
         "same-pool snapshot within ±12h of t-24h", "liquidity")
def _lc24(ctx: FeatureContext) -> float:
    return _liq_change(ctx, 24)


# ---------------------------------------------------------------- C. holders / wallets (provider aggregates)
@feature("holders_count", "addresses", "now", "GeckoTerminal/Jupiter", "number of holder addresses", "profile",
         "wallets")
def _hc(ctx: FeatureContext) -> float:
    return _f(ctx.profile.holders_count if ctx.profile else None, "profile_missing")


@feature("top10_pct", "percent", "now", "GeckoTerminal/Jupiter", "supply % held by top 10 addresses (may include pools)",
         "profile", "wallets")
def _t10(ctx: FeatureContext) -> float:
    return _f(ctx.profile.top10_pct if ctx.profile else None, "profile_missing")


@feature("organic_score", "0-100", "now", "Jupiter", "Jupiter organic activity score (proprietary, unvalidated)",
         "profile", "wallets")
def _org(ctx: FeatureContext) -> float:
    return _f(ctx.profile.organic_score if ctx.profile else None, "profile_missing")


@feature("organic_volume_share_h24", "fraction", "h24", "Jupiter stats24h",
         "buyOrganicVolume / buyVolume (Jupiter proprietary organic classifier)", "profile with stats24h", "wallets")
def _ovs(ctx: FeatureContext) -> float:
    from aegis.security.engine import organic_volume_share

    if ctx.profile is None:
        raise Missing("profile_missing")
    v = organic_volume_share(ctx.profile)
    if v is None:
        raise Missing("stats_missing")
    return float(v)


def compute(ctx: FeatureContext) -> FeatureVector:
    if ctx.obs.observed_at > ctx.as_of:
        raise LookAheadError("look-ahead: observation newer than as_of")
    if any(p.observed_at > ctx.as_of for p in ctx.history):
        raise LookAheadError("look-ahead: history contains points after as_of")
    fv = FeatureVector(ctx.as_of, FEATURE_SET_VERSION)
    for name, fn in _COMPUTE.items():
        try:
            v = fn(ctx)
            if v is None or not math.isfinite(v):
                fv.missing[name] = "undefined"
            else:
                fv.values[name] = float(v)
        except Missing as m:
            fv.missing[name] = str(m)
        except LookAheadError:
            raise
        except (ZeroDivisionError, ValueError, OverflowError) as exc:
            fv.missing[name] = f"error:{type(exc).__name__}"
    return fv
