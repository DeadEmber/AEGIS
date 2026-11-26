"""Coverage, resolution and latency reports for historical stores (reproducible, data stays local).

`coverage()` describes what a store can and cannot support (period, universe, cadence, gaps, censoring,
quality counters, market structures). `latency_study()` uses TRADE-LEVEL pool states (Launch Tape) to
measure how much the executable price moves between an observation and a fill L seconds later — the
empirical basis for the simulator's latency/adverse-selection assumptions.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

from aegis.history.store import HistoricalStore, ts


def _q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))
    return xs[k]


def coverage(store: HistoricalStore) -> dict[str, Any]:
    toks = list(store.tokens.values())
    with_obs = [t for t in toks if t.obs]
    by_day: Counter[str] = Counter()
    obs_day: Counter[str] = Counter()
    for t in toks:
        if t.graduated_at is not None:
            by_day[ts(t.graduated_at).date().isoformat()] += 1
        for o in t.obs:
            obs_day[ts(o.t).date().isoformat()] += 1
    track_h = [(t.tracking_end - t.graduated_at) / 3600 for t in with_obs
               if t.tracking_end is not None and t.graduated_at is not None]
    first_s = [t.obs[0].since_grad_s for t in with_obs if t.obs[0].since_grad_s is not None]
    n_obs = sum(len(t.obs) for t in toks)
    missing = Counter()
    for t in with_obs:
        for o in t.obs:
            for name, v in (("price_usd", o.price_usd), ("liquidity_usd", o.liq_usd), ("reserves", o.res_sol),
                            ("volume_m5", o.vol[0]), ("buys_m5", o.buys[0]), ("mcap_usd", o.mcap_usd)):
                if v is None:
                    missing[name] += 1
    return {
        "dataset": store.dataset, "revision": store.revision,
        "period_observations": (ts(store.start).isoformat() if n_obs else None, ts(store.end).isoformat() if n_obs else None),
        "tokens": len(toks), "tokens_with_tradable_observations": len(with_obs),
        "tokens_without_tradable_market": len(toks) - len(with_obs),
        "tradable_observations": n_obs, "non_tradable_observations": sum(t.other_obs for t in toks),
        "outcomes_retrospective": len(store.outcomes),
        "resolution_profile_s": [(None if math.isinf(u) else u, round(g, 1)) for u, g in store.resolution_profile],
        "first_observation_since_graduation_s": {"p10": _q(first_s, .1), "p50": _q(first_s, .5), "p90": _q(first_s, .9)},
        "tracking_hours": {"p10": _q(track_h, .1), "p50": _q(track_h, .5), "p90": _q(track_h, .9)},
        "missing_fields_in_tradable_rows": dict(missing),
        "gaps": [{"from": ts(g.start).isoformat(), "to": ts(g.end).isoformat(), "why": g.why} for g in store.gaps],
        "graduations_by_day": dict(sorted(by_day.items())),
        "observations_by_day": dict(sorted(obs_day.items())),
        "quality": store.quality,
        "notes": store.notes,
    }


def latency_study(store: HistoricalStore, lags: tuple[int, ...] = (1, 2, 5, 10, 30, 60),
                  min_since_grad_s: float = 30, max_since_grad_s: float = 1800) -> dict[str, Any]:
    """Distribution of the executable spot move between an observation and a fill `lag` seconds later.

    Uses only stores with trade-level states (`TokenTrack.ticks`). Returns, per lag, quantiles of the
    log move (signed) and of its absolute value, in basis points."""
    out: dict[str, Any] = {"dataset": store.dataset, "observations": 0, "lags": {}}
    moves: dict[int, list[float]] = {lag: [] for lag in lags}
    n = 0
    for tr in store.tokens.values():
        if not tr.ticks:
            continue
        for o in tr.obs:
            if o.since_grad_s is None or not (min_since_grad_s <= o.since_grad_s <= max_since_grad_s):
                continue
            s0 = tr.state_at(o.t)
            if s0 is None or s0[1] <= 0:
                continue
            p0 = (s0[2] + s0[3]) / s0[1]
            n += 1
            for lag in lags:
                s1 = tr.state_at(o.t + lag)
                if s1 is None or s1[1] <= 0 or (tr.tracking_end is not None and o.t + lag > tr.tracking_end):
                    continue
                p1 = (s1[2] + s1[3]) / s1[1]
                if p0 > 0 and p1 > 0:
                    moves[lag].append(math.log(p1 / p0) * 1e4)
    out["observations"] = n
    for lag, xs in moves.items():
        ab = [abs(x) for x in xs]
        out["lags"][lag] = {
            "n": len(xs), "signed_mean_bps": round(statistics.fmean(xs), 1) if xs else None,
            "signed_p10_bps": _q(xs, .1), "signed_p50_bps": _q(xs, .5), "signed_p90_bps": _q(xs, .9),
            "abs_p50_bps": _q(ab, .5), "abs_p90_bps": _q(ab, .9),
            "share_unchanged": round(sum(1 for x in xs if x == 0) / len(xs), 3) if xs else None,
        }
    return out
