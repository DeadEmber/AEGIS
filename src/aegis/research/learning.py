"""Point-in-time features and supervised samples for blocks C/D.

Features are computed from the observation at `i` and earlier ones only. Labels look FORWARD and are
built here for training/evaluation; they never enter a decision. Single-ticket outcomes replicate the
simulator's cost model (fees by tier, adverse haircut, network fees, recoverable deposit) without the
ledger, so labels are cheap; economic evaluation always goes through `research.backtest`.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from aegis.history.markets import FEE_SCHEDULES, PUMPSWAP_2026_10, UnsupportedMarket, swap
from aegis.history.store import HistoricalStore, Obs, TokenTrack

ENTRY_FEATURES = (
    "since_grad_min", "log_liq_usd", "log_mcap_usd", "vol_m5_to_liq", "vol_h1_to_liq", "buys_m5", "sells_m5",
    "log_bs_ratio_m5", "log_bs_ratio_h1", "chg_m5", "chg_h1", "mult_since_first_obs", "drawdown_from_high",
    "realized_vol_10", "liq_change_since_first", "hour_utc", "seconds_to_graduation", "dev_buy_pct",
    "creator_past_tokens", "creator_past_rugs", "initial_top10_pct", "initial_holder_count", "is_mayhem",
    "opportunity_rate_24h", "observed_24h",
)
POSITION_FEATURES = (
    "multiple_now", "peak_multiple", "drawdown_from_peak", "minutes_held", "since_grad_min", "chg_m5", "chg_h1",
    "log_bs_ratio_m5", "log_bs_ratio_h1", "vol_m5_to_liq", "liq_change_since_entry", "realized_vol_10",
    "mult_since_first_obs",
)


def _num(x: object) -> float:
    if x is None or isinstance(x, str):
        return math.nan
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def _log(x: float | None) -> float:
    return math.log(x) if x is not None and x > 0 else math.nan


def _ratio(b: int | None, s: int | None) -> float:
    if b is None or s is None:
        return math.nan
    return math.log((b + 1) / (s + 1))


def market_features(tr: TokenTrack, i: int) -> dict[str, float]:
    o = tr.obs[i]
    first = tr.obs[0]
    window = tr.obs[max(0, i - 10): i + 1]
    prices = [x.price_usd for x in tr.obs[: i + 1] if x.price_usd]
    rets = [math.log(b.price_usd / a.price_usd) for a, b in itertools.pairwise(window)
            if a.price_usd and b.price_usd]
    hi = max(prices) if prices else None
    return {
        "since_grad_min": (o.since_grad_s or math.nan) / 60,
        "log_liq_usd": _log(o.liq_usd), "log_mcap_usd": _log(o.mcap_usd),
        "vol_m5_to_liq": (o.vol[0] / o.liq_usd) if o.vol[0] is not None and o.liq_usd else math.nan,
        "vol_h1_to_liq": (o.vol[1] / o.liq_usd) if o.vol[1] is not None and o.liq_usd else math.nan,
        "buys_m5": _num(o.buys[0]), "sells_m5": _num(o.sells[0]),
        "log_bs_ratio_m5": _ratio(o.buys[0], o.sells[0]), "log_bs_ratio_h1": _ratio(o.buys[1], o.sells[1]),
        "chg_m5": _num(o.chg[0]), "chg_h1": _num(o.chg[1]),
        "mult_since_first_obs": (o.price_usd / first.price_usd) if o.price_usd and first.price_usd else math.nan,
        "drawdown_from_high": (1 - o.price_usd / hi) if o.price_usd and hi else math.nan,
        "realized_vol_10": float(np.std(rets)) if len(rets) >= 3 else math.nan,
        "liq_change_since_first": (o.liq_usd / first.liq_usd - 1) if o.liq_usd and first.liq_usd else math.nan,
    }


def entry_features(tr: TokenTrack, i: int, ctx_rate: float | None, ctx_observed: int | None) -> dict[str, float]:
    o = tr.obs[i]
    st = tr.static_at(o.t)
    f = market_features(tr, i)
    hour = math.floor((o.t % 86400) / 3600)
    f.update({
        "hour_utc": float(hour),
        "seconds_to_graduation": _num(st.get("seconds_to_graduation")),
        "dev_buy_pct": _num(st.get("dev_buy_pct_corrected", st.get("dev_buy_pct"))),
        "creator_past_tokens": _num(st.get("creator_past_tokens")),
        "creator_past_rugs": _num(st.get("creator_past_rugs")),
        "initial_top10_pct": _num(st.get("initial_top10_pct_corrected")),
        "initial_holder_count": _num(st.get("initial_holder_count")),
        "is_mayhem": 1.0 if st.get("is_mayhem_mode") else (0.0 if "is_mayhem_mode" in st else math.nan),
        "opportunity_rate_24h": ctx_rate if ctx_rate is not None else math.nan,
        "observed_24h": float(ctx_observed) if ctx_observed is not None else math.nan,
    })
    return f


@dataclass
class TicketCosts:
    """Cost model shared with the simulator's defaults (fees per tier, adverse haircut, network, deposit)."""

    fee_schedule: str = PUMPSWAP_2026_10.name
    adverse_bps: int = 100
    tx_lamports: int = 105_000  # base 5,000 + priority 100,000
    rent_lamports: int = 1_488_440
    ticket_eur: float = 1.0
    eur_usd: float = 1.16


@dataclass
class TicketPath:
    entry_index: int
    qty: float
    deposit_eur: float
    invested_eur: float
    values: list[tuple[int, float, float]] = field(default_factory=list)  # (obs index, t, net multiple excl. deposit)


def ticket_path(tr: TokenTrack, i: int, c: TicketCosts, max_index: int | None = None) -> TicketPath | None:
    """Buy a ticket at observation i, then the net liquidable multiple at every later observation."""
    fees = FEE_SCHEDULES[c.fee_schedule]
    o = tr.obs[i]
    if not o.sol_usd:
        return None
    sol_eur = o.sol_usd / c.eur_usd
    tx = c.tx_lamports / 1e9 * sol_eur
    dep = c.rent_lamports / 1e9 * sol_eur
    swap_in = c.ticket_eur - dep - tx
    if swap_in <= 0:
        return None
    adv = 1 - c.adverse_bps / 1e4
    try:
        fill = swap(o.pool_state(tr.supply), Decimal(repr(swap_in / sol_eur)), sol_in=True, fees=fees)
    except UnsupportedMarket:
        return None
    qty = float(fill.out_amount) * adv
    path = TicketPath(i, qty, dep, c.ticket_eur)
    end = len(tr.obs) if max_index is None else min(len(tr.obs), max_index + 1)
    for j in range(i, end):
        v = liquidation_multiple(tr.obs[j], qty, tr, c)
        if v is not None:
            path.values.append((j, tr.obs[j].t, v))
    return path


def liquidation_multiple(o: Obs, qty: float, tr: TokenTrack, c: TicketCosts) -> float | None:
    if not o.sol_usd or qty <= 0:
        return None
    sol_eur = o.sol_usd / c.eur_usd
    try:
        f = swap(o.pool_state(tr.supply), Decimal(repr(qty)), sol_in=False, fees=FEE_SCHEDULES[c.fee_schedule])
    except UnsupportedMarket:
        return None
    net = float(f.out_amount) * sol_eur * (1 - c.adverse_bps / 1e4) - c.tx_lamports / 1e9 * sol_eur
    return max(0.0, net) / c.ticket_eur


def trailing_outcome(path: TicketPath, activate: float, trail: float) -> tuple[float, float, int]:
    """(cash multiple incl. deposit refund, peak multiple, exit position in path) under a trailing rule;
    without a trigger, the last observed value (censored at tracking end)."""
    peak = 0.0
    for k, (_, _, v) in enumerate(path.values):
        peak = max(peak, v)
        if peak >= activate and v <= peak * (1 - trail):
            return v + path.deposit_eur / path.invested_eur, peak, k
    last = path.values[-1][2] if path.values else 0.0
    return last + path.deposit_eur / path.invested_eur, peak, len(path.values) - 1


@dataclass
class Samples:
    X: np.ndarray
    y: np.ndarray
    value: np.ndarray  # economic outcome (cash multiple) behind the label
    t: np.ndarray  # decision time
    t_label_end: np.ndarray  # when the label became known (purging / embargo)
    group: list[str]  # token (and creator, for dependence checks)
    creator: list[str | None]
    names: tuple[str, ...]

    def mask(self, m: np.ndarray) -> Samples:
        idx = np.flatnonzero(m)
        return Samples(self.X[idx], self.y[idx], self.value[idx], self.t[idx], self.t_label_end[idx],
                       [self.group[i] for i in idx], [self.creator[i] for i in idx], self.names)


def entry_samples(store: HistoricalStore, passes: Callable[[TokenTrack, Obs], bool], c: TicketCosts,
                  context: Callable[[float], tuple[float | None, int | None]], *, activate: float = 1.0,
                  trail: float = 0.30) -> Samples:
    """One sample per token: the FIRST observation passing `passes` (point-in-time), label = the trailing
    Champion's ticket outcome after costs (> 1 = cash returned exceeds the ticket)."""
    rows, ys, vals, ts_, tend, grp, cre = [], [], [], [], [], [], []
    for tr in store.tokens.values():
        i = next((k for k, o in enumerate(tr.obs) if passes(tr, o)), None)
        if i is None:
            continue
        path = ticket_path(tr, i, c)
        if path is None or not path.values:
            continue
        mult, _, k = trailing_outcome(path, activate, trail)
        rate, observed = context(tr.obs[i].t)
        f = entry_features(tr, i, rate, observed)
        rows.append([f[n] for n in ENTRY_FEATURES])
        ys.append(1.0 if mult > 1.0 else 0.0)
        vals.append(mult)
        ts_.append(tr.obs[i].t)
        tend.append(path.values[k][1])
        grp.append(tr.mint)
        cre.append(tr.static.get("creator", {}).get("value"))
    return Samples(np.array(rows, dtype=float).reshape(-1, len(ENTRY_FEATURES)), np.array(ys), np.array(vals),
                   np.array(ts_), np.array(tend), grp, cre, ENTRY_FEATURES)


def position_features(tr: TokenTrack, j: int, entry_i: int, mult_now: float, peak: float) -> dict[str, float]:
    o, e = tr.obs[j], tr.obs[entry_i]
    f = market_features(tr, j)
    return {
        "multiple_now": mult_now, "peak_multiple": peak,
        "drawdown_from_peak": 1 - mult_now / peak if peak > 0 else math.nan,
        "minutes_held": (o.t - e.t) / 60, "since_grad_min": f["since_grad_min"], "chg_m5": f["chg_m5"],
        "chg_h1": f["chg_h1"], "log_bs_ratio_m5": f["log_bs_ratio_m5"], "log_bs_ratio_h1": f["log_bs_ratio_h1"],
        "vol_m5_to_liq": f["vol_m5_to_liq"],
        "liq_change_since_entry": (o.liq_usd / e.liq_usd - 1) if o.liq_usd and e.liq_usd else math.nan,
        "realized_vol_10": f["realized_vol_10"], "mult_since_first_obs": f["mult_since_first_obs"],
    }


def continuation_samples(store: HistoricalStore, passes: Callable[[TokenTrack, Obs], bool], c: TicketCosts, *,
                         horizon_s: float, margin: float = 0.0, max_per_token: int = 60) -> Samples:
    """Optimal-stopping training set: for a ticket held from its first qualifying observation, every later
    state j (subsampled per token) labelled 1 if the liquidable value at j + horizon (first observation at or
    after it, within tracking) exceeds the value now by `margin`. Horizons below the data resolution are not
    allowed (the caller checks `store.resolution_at`). Samples are weighted per token (1/n)."""
    rows, ys, vals, ts_, tend, grp, cre = [], [], [], [], [], [], []
    for tr in store.tokens.values():
        i = next((k for k, o in enumerate(tr.obs) if passes(tr, o)), None)
        if i is None:
            continue
        path = ticket_path(tr, i, c)
        if path is None or len(path.values) < 3:
            continue
        times = [t for _, t, _ in path.values]
        step = max(1, len(path.values) // max_per_token)
        peak = 0.0
        for k, (j, t, v) in enumerate(path.values):
            peak = max(peak, v)
            if k % step or v <= 0:
                continue
            target = t + horizon_s
            kk = next((q for q in range(k + 1, len(times)) if times[q] >= target), None)
            if kk is None:
                break  # label unknown (censored): never imputed
            v_h = path.values[kk][2]
            f = position_features(tr, j, i, v, peak)
            rows.append([f[n] for n in POSITION_FEATURES])
            ys.append(1.0 if v_h > v * (1 + margin) else 0.0)
            vals.append(v_h / v)
            ts_.append(t)
            tend.append(times[kk])
            grp.append(tr.mint)
            cre.append(tr.static.get("creator", {}).get("value"))
    return Samples(np.array(rows, dtype=float).reshape(-1, len(POSITION_FEATURES)), np.array(ys), np.array(vals),
                   np.array(ts_), np.array(tend), grp, cre, POSITION_FEATURES)


def token_weights(groups: list[str]) -> np.ndarray:
    counts: dict[str, int] = {}
    for g in groups:
        counts[g] = counts.get(g, 0) + 1
    return np.array([1.0 / counts[g] for g in groups])
