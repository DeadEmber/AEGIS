"""Normalised point-in-time historical store.

Contemporaneous observations (what an observer could have known at `t`) and retrospective outcomes
(peaks, labels, "price at +24 h") are kept in different containers. Policies only ever receive a
`MarketView`, which serves observations with `t <= now` and raises `LookAheadError` otherwise;
outcomes are reachable only through `HistoricalStore.outcomes`, used by evaluation code.

Floats are used for market observations (prices, volumes) for speed; every MONEY amount booked by
the simulator is converted to Decimal at the execution boundary and goes through the Ledger.
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aegis.features.library import HistoryPoint, LookAheadError
from aegis.history.markets import MarketType, PoolState
from aegis.marketdata.models import PoolObservation, WindowStats

WIN = ("m5", "h1", "h6", "h24")


def ts(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.UTC)


def _d(x: float | None) -> Decimal | None:
    if x is None or not math.isfinite(x):
        return None
    return Decimal(repr(x))


@dataclass(slots=True)
class Obs:
    """One observation of a market at time `t` (epoch seconds, when the observer had it)."""

    t: float
    pool: str
    market_type: MarketType
    price_usd: float | None
    price_sol: float | None
    sol_usd: float | None
    liq_usd: float | None
    res_token: float | None
    res_sol: float | None
    vres_sol: float = 0.0
    mcap_usd: float | None = None
    fdv_usd: float | None = None
    vol: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None)
    chg: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None)  # percent
    buys: tuple[int | None, int | None] = (None, None)  # m5, h1
    sells: tuple[int | None, int | None] = (None, None)
    # 24 h counts (only when derivable exactly, e.g. a pool younger than 24 h with every trade recorded)
    buys_h24: int | None = None
    sells_h24: int | None = None
    buyers_h24: int | None = None
    sellers_h24: int | None = None
    since_grad_s: float | None = None
    flags: tuple[str, ...] = ()

    @property
    def tradable(self) -> bool:
        return self.market_type == MarketType.CPMM and bool(self.res_token) and bool(self.res_sol)

    def pool_state(self, supply: float | None = None) -> PoolState:
        cap = None
        if self.price_sol is not None and supply:
            cap = self.price_sol * supply
        return PoolState(self.market_type, _d(self.res_token), _d(self.res_sol), _d(self.vres_sol) or Decimal(0), cap)

    def buy_sell_ratio(self, window: int = 0) -> float | None:
        b, s = self.buys[window], self.sells[window]
        if b is None or s is None:
            return None
        return b / s if s > 0 else (math.inf if b > 0 else None)


@dataclass
class TokenTrack:
    mint: str
    symbol: str | None = None
    name: str | None = None
    created_at: float | None = None  # launch (PIT from creation)
    graduated_at: float | None = None  # PIT only from this instant on
    pool: str | None = None  # tradable (primary) pool
    supply: float | None = None  # total supply in human units when known (1e9 standard, 2e9 mayhem)
    decimals: int = 6
    token_program: str | None = None
    static: dict[str, Any] = field(default_factory=dict)  # launch/graduation-time attributes with `known_at`
    obs: list[Obs] = field(default_factory=list)  # tradable market, sorted by t
    other_obs: int = 0  # non-tradable observations (completed curve, unknown structures): coverage only
    tracking_end: float | None = None  # source stopped following the token (censoring)
    # Optional trade-level pool states (t, reserve_token, reserve_sol, virtual_sol), sorted: lets the simulator
    # fill at the TRUE state at decision time + latency instead of the last (coarser) observation.
    ticks: list[tuple[float, float, float, float]] = field(default_factory=list, repr=False)
    _times: list[float] = field(default_factory=list, repr=False)
    _tick_times: list[float] = field(default_factory=list, repr=False)

    def finalize(self) -> None:
        self.obs.sort(key=lambda o: o.t)
        self._times = [o.t for o in self.obs]
        self.ticks.sort()
        self._tick_times = [x[0] for x in self.ticks]

    def state_at(self, t: float) -> tuple[float, float, float, float] | None:
        """Trade-level pool state in force at `t` (None when the source has no trade-level states)."""
        if not self.ticks:
            return None
        i = bisect.bisect_right(self._tick_times, t) - 1
        return self.ticks[i] if i >= 0 else None

    def index_at(self, now: float) -> int:
        """Index of the last observation with t <= now (-1 if none)."""
        return bisect.bisect_right(self._times, now) - 1

    def last(self, now: float) -> Obs | None:
        i = self.index_at(now)
        return self.obs[i] if i >= 0 else None

    def static_at(self, now: float) -> dict[str, Any]:
        """Static attributes already known at `now` (each entry carries its own known_at)."""
        return {k: v["value"] for k, v in self.static.items() if v["known_at"] is not None and v["known_at"] <= now}


@dataclass
class Outcome:
    """RETROSPECTIVE facts about a token (computed after the fact). Never a decision input."""

    mint: str
    label: str | None
    values: dict[str, Any]
    computed_at: float | None


@dataclass
class Gap:
    start: float
    end: float
    why: str


@dataclass
class HistoricalStore:
    dataset: str
    revision: str
    tokens: dict[str, TokenTrack]
    outcomes: dict[str, Outcome]
    gaps: list[Gap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    resolution_profile: list[tuple[float, float]] = field(default_factory=list)  # (since_grad upper s, typical gap s)
    quality: dict[str, int] = field(default_factory=dict)

    def finalize(self) -> HistoricalStore:
        for t in self.tokens.values():
            t.finalize()
        return self

    @property
    def start(self) -> float:
        return min((t.obs[0].t for t in self.tokens.values() if t.obs), default=0.0)

    @property
    def end(self) -> float:
        return max((t.obs[-1].t for t in self.tokens.values() if t.obs), default=0.0)

    def events(self, start: float | None = None, end: float | None = None) -> Iterator[tuple[float, str, int]]:
        """All observations in time order as (t, mint, index). Ties broken by mint (deterministic)."""
        ev = [(o.t, m, i) for m, tr in self.tokens.items() for i, o in enumerate(tr.obs)
              if (start is None or o.t >= start) and (end is None or o.t <= end)]
        ev.sort()
        yield from ev

    def resolution_at(self, since_grad_s: float | None) -> float | None:
        if since_grad_s is None:
            return None
        for upper, gap in self.resolution_profile:
            if since_grad_s < upper:
                return gap
        return self.resolution_profile[-1][1] if self.resolution_profile else None

    def in_gap(self, t: float) -> Gap | None:
        return next((g for g in self.gaps if g.start <= t <= g.end), None)


class MarketView:
    """Point-in-time window on a store for policies: observations newer than `now` are unreachable."""

    def __init__(self, store: HistoricalStore):
        self._s = store
        self.now = 0.0

    def advance(self, now: float) -> None:
        if now < self.now:
            raise LookAheadError("clock moved backwards")
        self.now = now

    def track(self, mint: str) -> TokenTrack:
        return self._s.tokens[mint]

    def latest(self, mint: str) -> Obs | None:
        return self._s.tokens[mint].last(self.now)

    def history(self, mint: str, *, since: float | None = None) -> list[Obs]:
        tr = self._s.tokens[mint]
        i = tr.index_at(self.now)
        if i < 0:
            return []
        lo = 0 if since is None else bisect.bisect_left(tr._times, since)
        return tr.obs[lo:i + 1]

    def get(self, mint: str, index: int) -> Obs:
        o = self._s.tokens[mint].obs[index]
        if o.t > self.now:
            raise LookAheadError(f"{mint}[{index}] observed at {o.t} > now {self.now}")
        return o

    def static(self, mint: str) -> dict[str, Any]:
        return self._s.tokens[mint].static_at(self.now)

    def graduated(self, mint: str) -> float | None:
        g = self._s.tokens[mint].graduated_at
        return g if g is not None and g <= self.now else None


def to_pool_observation(tr: TokenTrack, o: Obs, source: str) -> PoolObservation:
    """Map a historical observation onto the production `PoolObservation` (feature library / worker)."""
    windows = {}
    for k, w in enumerate(WIN):
        b = o.buys[k] if k < 2 else (o.buys_h24 if w == "h24" else None)
        s = o.sells[k] if k < 2 else (o.sells_h24 if w == "h24" else None)
        extra = {"buyers": o.buyers_h24, "sellers": o.sellers_h24} if w == "h24" else {}
        windows[w] = WindowStats(volume_usd=_d(o.vol[k]), price_change_pct=_d(o.chg[k]), buys=b, sells=s, **extra)
    ptype = {"cpmm": "amm", "bonding_curve": "bonding_curve", "clmm": "clmm"}.get(str(o.market_type), "unknown")
    return PoolObservation(
        source=source, network="solana", pool_address=o.pool, token_address=tr.mint, token_side="base",
        counter_asset="SOL", dex="pumpswap" if o.market_type == MarketType.CPMM else str(o.market_type),
        pool_type=ptype, observed_at=ts(o.t), price_usd=_d(o.price_usd), liquidity_usd=_d(o.liq_usd),
        fdv_usd=_d(o.fdv_usd), market_cap_usd=_d(o.mcap_usd), windows=windows,
        pool_created_at=ts(tr.graduated_at) if tr.graduated_at is not None else None, token_symbol=tr.symbol,
        token_name=tr.name, token_decimals=tr.decimals, quality_flags=o.flags,
    )


def history_points(obs: list[Obs]) -> list[HistoryPoint]:
    return [HistoryPoint(ts(o.t), _d(o.price_usd), _d(o.liq_usd), o.pool) for o in obs]
