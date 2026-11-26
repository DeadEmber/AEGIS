"""Adapter: Solana Launch Tape free sample -> HistoricalStore (first 30 min after graduation).

`gradtrades` carries every PumpSwap trade with the pool state AFTER the trade (poolBase, poolQuote and,
when it changes, virtualQuote; verified: quote_after = quote_before + quoteAmount + lpFee on buys). The
adapter keeps two views:

- `ticks`: the trade-level pool state, used only to FILL orders at decision time + latency;
- `obs`: what a poller would have seen on a regular grid (`resolution_s`, default 15 s): last state at or
  before the grid instant, with rolling 5 min / 1 h windows computed only from trades already printed.

Feed gaps (`down`/`up`) are kept as gaps; `end` rows mark the end of tracking (censoring). SOL/USD
comes from the same recorder (`padfeed` `qpx` rows, every 30 s). `pregrad` `record` rows (known at
their own `t`) become graduation-time attributes; `pregrad` `outcome` rows (+10 min) are retrospective.

Licence: research use inside the team only; NO redistribution — nothing from these files is ever
committed to the repository (see registry.LAUNCHTAPE).
"""

from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import duckdb

from aegis.history.markets import MarketType
from aegis.history.registry import LAUNCHTAPE, DatasetSpec
from aegis.history.store import Gap, HistoricalStore, Obs, Outcome, TokenTrack

WSOL = "So11111111111111111111111111111111111111112"
SAMPLE = "2026-10-10-1400-1600utc"


def _f(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _sol_usd_series(c: duckdb.DuckDBPyConnection, path: Path) -> tuple[list[float], list[float]]:
    rows = c.execute(f"SELECT t, usd['{WSOL}'] FROM read_parquet('{path.as_posix()}') WHERE type = 'qpx' "
                     "ORDER BY t").fetchall()
    times = [r[0] / 1000 for r in rows if r[1] is not None]
    vals = [float(r[1]) for r in rows if r[1] is not None]
    return times, vals


def load(base: Path, spec: DatasetSpec = LAUNCHTAPE, *, resolution_s: float = 15.0) -> HistoricalStore:
    root = spec.root(base) / "data"
    c = duckdb.connect()
    gt = (root / f"gradtrades-{SAMPLE}.parquet").as_posix()
    sol_t, sol_v = _sol_usd_series(c, root / f"padfeed-{SAMPLE}.parquet")

    def sol_usd(t: float) -> float | None:
        i = bisect.bisect_right(sol_t, t) - 1
        if i < 0:
            return sol_v[0] if sol_v and sol_t[0] - t < 60 else None
        return sol_v[i] if t - sol_t[i] <= 120 else None  # stale SOL quote -> unknown

    tokens: dict[str, TokenTrack] = {}
    gaps: list[Gap] = []
    down_at: float | None = None
    trades: dict[str, list[tuple[float, str, float, float, float, float, float, str]]] = defaultdict(list)
    vq_last: dict[str, float] = {}
    quality = {"trades": 0, "virtual_quote_carried": 0, "late_grad": 0}
    for row in c.execute(f"SELECT type, t, mint, pool, poolBase, poolQuote, virtualQuote, side, baseAmount, quoteAmount, "
                         f"priceAfter, late, why, user FROM read_parquet('{gt}') ORDER BY t, i").fetchall():
        typ, t = row[0], row[1] / 1000
        if typ == "down":
            down_at = t
        elif typ == "up":
            gaps.append(Gap(down_at if down_at is not None else t, t, "launchtape feed down/up"))
            down_at = None
        elif typ == "grad":
            tr = tokens.setdefault(row[2], TokenTrack(mint=row[2], decimals=6, supply=1_000_000_000.0))
            tr.graduated_at, tr.pool = t, row[3]
            vq_last[row[2]] = _f(row[6]) or 0.0
            quality["late_grad"] += bool(row[11])
            tr.static["grad_state"] = {"value": {"poolBase": row[4], "poolQuote": row[5], "virtualQuote": row[6]},
                                       "known_at": t, "src": "gradtrades grad row"}
        elif typ == "trade":
            mint = row[2]
            if row[6] is not None:
                vq_last[mint] = float(row[6])
            else:
                quality["virtual_quote_carried"] += 1
            trades[mint].append((t, row[7], float(row[8] or 0), float(row[9] or 0), float(row[4]), float(row[5]),
                                 vq_last.get(mint, 0.0), row[13] or ""))
            quality["trades"] += 1
        elif typ == "end" and row[2] in tokens:
            tokens[row[2]].tracking_end = t
            tokens[row[2]].static["end_reason"] = {"value": row[12], "known_at": t, "src": "gradtrades end row"}

    for mint, tl in trades.items():
        tr = tokens.setdefault(mint, TokenTrack(mint=mint, decimals=6, supply=1_000_000_000.0))
        tr.ticks = [(x[0], x[4], x[5], x[6]) for x in tl]
        buyers: set[str] = set()
        sellers: set[str] = set()
        n_buy = n_sell = 0
        vol_all = 0.0
        pre_window = tr.graduated_at is None  # graduated before the sample window: graduation instant unknown
        t0 = tr.graduated_at if tr.graduated_at is not None else tl[0][0]
        t_end = tr.tracking_end or tl[-1][0]
        win5: deque[tuple[float, str, float]] = deque()
        win60: deque[tuple[float, str, float]] = deque()
        price_hist: list[tuple[float, float]] = []
        price_times: list[float] = []
        j = 0
        g = t0 + resolution_s
        while g <= t_end + 1e-9:
            while j < len(tl) and tl[j][0] <= g:
                t, side, _, qa, rb, rq, vq, user = tl[j]
                if side == "buy":
                    n_buy += 1
                    buyers.add(user)
                else:
                    n_sell += 1
                    sellers.add(user)
                vol_all += qa
                win5.append((t, side, qa))
                win60.append((t, side, qa))
                price_hist.append((t, (rq + vq) / rb if rb > 0 else math.nan))
                price_times.append(t)
                j += 1
            while win5 and win5[0][0] <= g - 300:
                win5.popleft()
            while win60 and win60[0][0] <= g - 3600:
                win60.popleft()
            if j == 0:
                g += resolution_s
                continue
            _, _, _, _, rb, rq, vq, _ = tl[j - 1]
            su = sol_usd(g)
            p_sol = (rq + vq) / rb if rb > 0 else None
            k = bisect.bisect_right(price_times, g - 300) - 1
            p5 = price_hist[k][1] if k >= 0 else price_hist[0][1]
            chg5 = (p_sol / p5 - 1) * 100 if p_sol and p5 and p5 > 0 else None
            p0 = price_hist[0][1]
            chg_h1 = (p_sol / p0 - 1) * 100 if p_sol and p0 and p0 > 0 else None  # tracking < 1 h: since first trade
            v5 = sum(x[2] for x in win5) * su if su else None
            v60 = sum(x[2] for x in win60) * su if su else None
            b5 = sum(1 for x in win5 if x[1] == "buy")
            b60 = sum(1 for x in win60 if x[1] == "buy")
            liq_sol = rq + (rb * p_sol if p_sol else 0.0)
            tr.obs.append(Obs(
                t=g, pool=tr.pool or "", market_type=MarketType.CPMM,
                price_usd=p_sol * su if p_sol and su else None, price_sol=p_sol, sol_usd=su,
                liq_usd=liq_sol * su if su else None, res_token=rb, res_sol=rq, vres_sol=vq,
                mcap_usd=p_sol * tr.supply * su if p_sol and su and tr.supply else None,
                fdv_usd=p_sol * tr.supply * su if p_sol and su and tr.supply else None,
                vol=(v5, v60, None, vol_all * su if su and not pre_window else None), chg=(chg5, chg_h1, None, None),
                buys=(b5, b60), sells=(len(win5) - b5, len(win60) - b60),
                # the pool exists only since graduation and every trade is recorded: 24 h counts are exact
                buys_h24=None if pre_window else n_buy, sells_h24=None if pre_window else n_sell,
                buyers_h24=None if pre_window else len(buyers), sellers_h24=None if pre_window else len(sellers),
                since_grad_s=None if pre_window else g - t0,
                flags=("graduated_before_window",) if pre_window else ("h1_window_is_since_graduation",)))
            g += resolution_s

    pre = (root / f"pregrad-{SAMPLE}.parquet").as_posix()
    outcomes: dict[str, Outcome] = {}
    rows = c.execute(f"SELECT type, t, mint, sym, name, ageS, instant, creatorPctAtGrad, devBuyPct, curve, "
                     f"to_json(creatorInfo), to_json(gradSlot), capAt, maxGain, minDrop, crashed, to_json(firstBigSell), "
                     f"trades FROM read_parquet('{pre}') WHERE type IN ('record', 'outcome')").fetchall()
    for r in rows:
        typ, t, mint = r[0], r[1] / 1000, r[2]
        if typ == "record" and mint in tokens:
            tr = tokens[mint]
            tr.symbol, tr.name = r[3], r[4]
            curve = json.loads(r[9]) if isinstance(r[9], str) and r[9].startswith("{") else {}
            ci = json.loads(r[10]) if r[10] else {}
            gs = json.loads(r[11]) if r[11] else {}
            vals = {"curve_age_s": r[5], "instant_fill": r[6], "creator_pct_at_grad": r[7], "dev_buy_pct": r[8],
                    "curve_top1_nc_pct": curve.get("top1NcPct"), "curve_top5_nc_pct": curve.get("top5NcPct"),
                    "curve_bundle_pct": curve.get("bundlePct"), "curve_coverage": curve.get("coverage"),
                    "creator_txs50": ci.get("txs50"), "creator_prior_coins_24h": ci.get("priorCoins24h"),
                    "creator_prior_coins_72h": ci.get("priorCoins72h"), "grad_slot_pct": gs.get("pct"),
                    "grad_slot_wallets": gs.get("nWallets")}
            for k, v in vals.items():
                tr.static[k] = {"value": v, "known_at": t, "src": "pregrad record"}
        elif typ == "outcome":
            outcomes[mint] = Outcome(mint, "crashed" if r[15] else "not_crashed",
                                     {"capAt": r[12], "maxGain_pct": r[13], "minDrop_pct": r[14], "crashed": r[15],
                                      "firstBigSell": json.loads(r[16]) if r[16] else None, "trades_10m": r[17]}, t)
    for tr in tokens.values():
        if tr.tracking_end is None and tr.obs:
            tr.tracking_end = tr.obs[-1].t
    return HistoricalStore(spec.id, spec.revision, tokens, outcomes, gaps=gaps, notes=list(spec.notes),
                           resolution_profile=[(math.inf, resolution_s)], quality=quality).finalize()
