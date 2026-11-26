"""Adapter: PumpFun Launch-to-Graduation Corpus -> HistoricalStore (post-graduation market).

Contemporaneous inputs:
- `postgard_snapshots`: pair polls after graduation (price, reserves, liquidity, rolling volume, buys and
  sells windows). Only PumpSwap pairs are tradable CPMM markets; 'pumpfun' rows after graduation are
  the completed curve (no liquidity) and Meteora/Raydium rows are of unknown structure: coverage only.
- `migrations`: graduation instant per mint (pool address sentinels are never used as addresses).
- `tokens` (graduated mints only): launch-time attributes, each with the instant it became known.
  Retrospective columns (graduated_at, peak_*, trade_count, is_zombie, data_quality_score,
  is_training_ready, entry_price_*) are NOT loaded as attributes (KNOWN_ISSUES §7: label proxies).
Retrospective: `postgard_outcomes` -> `HistoricalStore.outcomes` (evaluation only).
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from pathlib import Path
from statistics import median
from typing import Any

import duckdb

from aegis.history.markets import MarketType, classify_dex
from aegis.history.registry import PUMPFUN, DatasetSpec
from aegis.history.store import Gap, HistoricalStore, Obs, Outcome, TokenTrack

POOL_SENTINELS = ("synthetic_graduation_queue", "backfilled_from_pumpswap_trade")
STANDARD_SUPPLY = 1_000_000_000.0
MAYHEM_SUPPLY = 2_000_000_000.0
PUMP_DECIMALS = 6  # pump.fun mints (amounts in Launch Tape and the corpus carry 6 decimals)

# Launch-time attributes kept from tokens.parquet (KNOWN_ISSUES: corrected concentration only).
_STATIC_COLS = ("is_mayhem_mode", "creator", "creator_past_tokens", "creator_past_rugs", "dev_buy_pct_corrected",
                "initial_top1_pct_corrected", "initial_top5_pct_corrected", "initial_top10_pct_corrected",
                "initial_holder_count", "initial_gini", "initial_market_cap_sol", "initial_buy_sol",
                "is_cashback_enabled", "first_trade_price_sol", "top10_pct_suspect", "supply_bug_corrected")
_CONCENTRATION = ("initial_top1_pct_corrected", "initial_top5_pct_corrected", "initial_top10_pct_corrected",
                  "initial_gini")


def _f(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def load(base: Path, spec: DatasetSpec = PUMPFUN, *, start: str | None = None, end: str | None = None,
         mints: list[str] | None = None) -> HistoricalStore:
    """Load the post-graduation universe. `start`/`end` (ISO dates, by graduation instant) and `mints`
    restrict the universe for experiments and tests."""
    root = spec.root(base)
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    for name in ("postgard_snapshots", "migrations", "postgard_outcomes", "tokens"):
        c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{(root / (name + '.parquet')).as_posix()}')")
    where = ["1=1"]
    params: list[Any] = []
    if start:
        where.append("migrated_at >= ?::TIMESTAMPTZ")
        params.append(start)
    if end:
        where.append("migrated_at < ?::TIMESTAMPTZ")
        params.append(end)
    if mints:
        where.append("mint IN (SELECT unnest(?::VARCHAR[]))")
        params.append(mints)
    c.execute(f"CREATE TEMP TABLE universe AS SELECT mint, epoch(migrated_at) AS grad_t, seconds_to_graduation, "
              f"pool_address FROM migrations WHERE {' AND '.join(where)}", params)

    tokens: dict[str, TokenTrack] = {}
    quality: dict[str, int] = {"pool_sentinel": 0, "incomplete_data_rows": 0, "non_tradable_rows": 0,
                               "concentration_suspect_tokens": 0, "missing_reserves_rows": 0}
    for mint, grad_t, stg, pool in c.execute("SELECT * FROM universe").fetchall():
        tr = TokenTrack(mint=mint, graduated_at=float(grad_t), decimals=PUMP_DECIMALS)
        if pool in POOL_SENTINELS:
            quality["pool_sentinel"] += 1
        else:
            tr.pool = pool
        tr.static["seconds_to_graduation"] = {"value": _f(stg), "known_at": float(grad_t), "src": "migrations"}
        tokens[mint] = tr

    cols = ", ".join(f"t.{k}" for k in _STATIC_COLS)
    for row in c.execute(f"SELECT t.mint, epoch(t.detected_at), t.name, t.symbol, {cols} FROM tokens t "
                         "JOIN universe u USING (mint)").fetchall():
        tr = tokens[row[0]]
        known = _f(row[1])
        tr.created_at, tr.name, tr.symbol = known, row[2], row[3]
        vals = dict(zip(_STATIC_COLS, row[4:], strict=True))
        suspect = bool(vals.get("top10_pct_suspect"))
        quality["concentration_suspect_tokens"] += suspect
        for k, v in vals.items():
            value = None if k in _CONCENTRATION and suspect else v  # KNOWN_ISSUES §2.1: unknown, never imputed
            tr.static[k] = {"value": value, "known_at": known, "src": "tokens.parquet (as collected at detection)"}
        tr.supply = MAYHEM_SUPPLY if vals.get("is_mayhem_mode") else STANDARD_SUPPLY

    snap = c.execute("""
        SELECT s.mint, epoch(s.snapshot_time), s.pair_address, s.dex_id, s.incomplete_data, s.price_usd, s.price_native,
               s.liquidity_usd, s.liquidity_base, s.liquidity_quote, s.market_cap_usd, s.fdv_usd,
               s.volume_5m, s.volume_1h, s.volume_6h, s.volume_24h,
               s.price_change_5m, s.price_change_1h, s.price_change_6h, s.price_change_24h,
               s.buys_5m, s.buys_1h, s.sells_5m, s.sells_1h, s.seconds_since_graduation
        FROM postgard_snapshots s JOIN universe u USING (mint)
        ORDER BY s.mint, s.snapshot_time""").fetchall()
    gaps_by_bucket: dict[float, list[float]] = {7200.0: [], 21600.0: [], 86400.0: [], math.inf: []}
    last_t: dict[str, float] = {}
    for r in snap:
        mint, t, pair, dex = r[0], float(r[1]), r[2], r[3]
        mt = classify_dex(dex)
        flags: tuple[str, ...] = ("incomplete_data",) if r[4] else ()
        quality["incomplete_data_rows"] += bool(r[4])
        tr = tokens[mint]
        if mt != MarketType.CPMM or r[7] is None:
            tr.other_obs += 1
            quality["non_tradable_rows"] += 1
            continue
        if tr.pool is None:
            tr.pool = pair
        elif pair != tr.pool:  # a second CPMM pair for the same mint: keep the graduation pool only
            tr.other_obs += 1
            quality["non_tradable_rows"] += 1
            continue
        pu, pn = _f(r[5]), _f(r[6])
        sol_usd = pu / pn if pu and pn else None
        if r[8] is None or r[9] is None:
            quality["missing_reserves_rows"] += 1
        o = Obs(t=t, pool=pair, market_type=mt, price_usd=pu, price_sol=pn, sol_usd=sol_usd, liq_usd=_f(r[7]),
                res_token=_f(r[8]), res_sol=_f(r[9]), mcap_usd=_f(r[10]), fdv_usd=_f(r[11]),
                vol=(_f(r[12]), _f(r[13]), _f(r[14]), _f(r[15])), chg=(_f(r[16]), _f(r[17]), _f(r[18]), _f(r[19])),
                buys=(r[20], r[21]), sells=(r[22], r[23]), since_grad_s=_f(r[24]), flags=flags)
        tr.obs.append(o)
        if mint in last_t and o.since_grad_s is not None:
            for upper, lst in gaps_by_bucket.items():
                if o.since_grad_s < upper:
                    lst.append(t - last_t[mint])
                    break
        last_t[mint] = t
    for tr in tokens.values():
        if tr.obs:
            tr.tracking_end = tr.obs[-1].t

    outcomes: dict[str, Outcome] = {}
    oc = c.execute("SELECT o.*, epoch(o.computed_at) AS computed_t FROM postgard_outcomes o JOIN universe u USING (mint)")
    names = [d[0] for d in oc.description]
    for row in oc.fetchall():
        rec = dict(zip(names, row, strict=True))
        outcomes[rec["mint"]] = Outcome(rec["mint"], rec.get("outcome_label"),
                                        {k: v for k, v in rec.items() if k not in ("mint", "computed_at", "computed_t")},
                                        _f(rec["computed_t"]))

    # collection gaps: whole UTC days without any graduation in a period with daily graduations
    days = [r[0] for r in c.execute("SELECT DISTINCT CAST(to_timestamp(grad_t) AS DATE) FROM universe ORDER BY 1").fetchall()]
    gaps = []
    for a, b in itertools.pairwise(days):
        if (b - a).days > 1:
            s = dt.datetime.combine(a + dt.timedelta(days=1), dt.time(), dt.UTC).timestamp()
            e = dt.datetime.combine(b, dt.time(), dt.UTC).timestamp()
            gaps.append(Gap(s, e, "no graduations recorded (collection outage or gap; KNOWN_ISSUES §5.5 for Jul 3)"))
    profile = [(u, median(g)) for u, g in gaps_by_bucket.items() if g]
    return HistoricalStore(spec.id, spec.revision, tokens, outcomes, gaps=gaps, notes=list(spec.notes),
                           resolution_profile=profile, quality=quality).finalize()
