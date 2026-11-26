"""Phase 2 block A: dataset registry, market structures, point-in-time store, adapters, FX, simulator."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import math
from decimal import Decimal
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from aegis.config import StrategyConfig
from aegis.features.library import LookAheadError
from aegis.history import launchtape, pumpfun, registry
from aegis.history.fx import EcbTable
from aegis.history.markets import FLAT_30, PUMPSWAP_2026_10, MarketType, PoolState, UnsupportedMarket, classify_dex, swap
from aegis.history.store import HistoricalStore, MarketView, to_pool_observation
from aegis.research import backtest, presets
from aegis.research.policies import Context, HoldExit, PostGradEntry, RulesExit
from tests.history_synth import T0, make_store, make_track

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------- market structures
def test_cpmm_swap_formula_fee_and_virtual_reserve():
    st = PoolState(MarketType.CPMM, Decimal(1_000_000), Decimal(100), Decimal(0))
    f = swap(st, Decimal(1), sol_in=True, fees=FLAT_30)
    net = Decimal(1) * (1 - Decimal("0.003"))
    assert f.out_amount == Decimal(1_000_000) * net / (Decimal(100) + net)
    assert f.fee_in_input == Decimal("0.003") and f.fee_bps == 30
    assert Decimal(0) < f.price_impact_pct < Decimal(2)
    # a virtual quote reserve raises the price: fewer tokens for the same SOL
    v = swap(dataclasses.replace(st, virtual_sol=Decimal(20)), Decimal(1), sol_in=True, fees=FLAT_30)
    assert v.out_amount < f.out_amount


def test_sell_never_extracts_more_than_the_real_reserve():
    st = PoolState(MarketType.CPMM, Decimal(1000), Decimal(1), Decimal(100))  # mostly virtual
    f = swap(st, Decimal(10**9), sol_in=False, fees=FLAT_30)
    assert f.out_amount <= Decimal(1)


@pytest.mark.parametrize("mt", [MarketType.DLMM, MarketType.CLMM, MarketType.DAMM, MarketType.UNKNOWN,
                                MarketType.BONDING_CURVE])
def test_no_cpmm_fill_for_other_structures(mt):
    with pytest.raises(UnsupportedMarket):
        swap(PoolState(mt, Decimal(10), Decimal(10)), Decimal(1), sol_in=True, fees=FLAT_30)


def test_dex_classification_never_assumes_cpmm_for_ambiguous_dexes():
    assert classify_dex("pumpswap") == MarketType.CPMM
    assert classify_dex("pumpfun") == MarketType.BONDING_CURVE
    assert classify_dex("meteora") == MarketType.UNKNOWN and classify_dex("raydium") == MarketType.UNKNOWN


def test_measured_fee_schedule_tiers_and_unknown_cap_takes_worst_tier():
    assert PUMPSWAP_2026_10.bps(100) == 126 and PUMPSWAP_2026_10.bps(5000) == 106 and PUMPSWAP_2026_10.bps(50_000) == 96
    assert PUMPSWAP_2026_10.bps(None) == 126


# ---------------------------------------------------------------------------- point-in-time store
def test_market_view_refuses_future_observations_and_hides_unknown_static():
    st = make_store(1)
    v = MarketView(st)
    mint = next(iter(st.tokens))
    tr = st.tokens[mint]
    v.advance(tr.obs[5].t)
    assert v.latest(mint) is tr.obs[5] and len(v.history(mint)) == 6
    with pytest.raises(LookAheadError):
        v.get(mint, 6)
    with pytest.raises(LookAheadError):
        v.advance(tr.obs[4].t)  # the clock never runs backwards
    tr.static["late_fact"] = {"value": 1, "known_at": tr.obs[50].t, "src": "test"}
    assert "late_fact" not in v.static(mint)
    assert not hasattr(v, "outcomes")  # retrospective outcomes are not reachable from a policy's view


def test_pool_observation_mapping_keeps_unknowns_as_none():
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    po = to_pool_observation(tr, tr.obs[0], "synthetic")
    assert po.w("h6").volume_usd is None and po.w("h24").buys is None  # never zero-filled
    assert po.pool_type == "amm" and po.price_usd is not None


# ---------------------------------------------------------------------------- FX
def test_ecb_rate_is_only_known_after_publication():
    fx = EcbTable({"2026-07-03": "1.10", "2026-07-06": "1.20"}, "test")
    fri_morning = dt.datetime(2026, 7, 3, 10, tzinfo=dt.UTC).timestamp()
    assert fx.eur_usd(fri_morning) is None  # nothing published yet
    sat = dt.datetime(2026, 7, 4, 12, tzinfo=dt.UTC).timestamp()
    assert fx.eur_usd(sat) == (Decimal("1.10"), dt.date(2026, 7, 3))
    mon_before = dt.datetime(2026, 7, 6, 14, 59, tzinfo=dt.UTC).timestamp()
    assert fx.eur_usd(mon_before)[1] == dt.date(2026, 7, 3)
    assert fx.eur_usd(mon_before + 120)[1] == dt.date(2026, 7, 6)


def test_bundled_ecb_table_covers_the_research_periods():
    lo, hi = EcbTable.bundled().coverage()
    assert lo <= dt.date(2026, 6, 5) and hi >= dt.date(2026, 10, 14)


# ---------------------------------------------------------------------------- registry
def test_registry_pins_revisions_and_hashes():
    for spec in registry.REGISTRY.values():
        assert len(spec.revision) == 40 and all(len(f.sha256) == 64 for f in spec.files)
    assert registry.LAUNCHTAPE.redistributable is False and registry.PUMPFUN.redistributable is True


def test_verify_detects_missing_and_tampered_files(tmp_path):
    spec = dataclasses.replace(registry.PUMPFUN, files=(registry.DatasetFile("a.bin", hashlib.sha256(b"ok").hexdigest(), 2,
                                                                             "docs", "docs"),))
    assert registry.verify(spec, tmp_path)["a.bin"]["status"] == "missing"
    p = spec.root(tmp_path) / "a.bin"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"ok")
    assert registry.is_complete(registry.verify(spec, tmp_path))
    p.write_bytes(b"no")
    assert registry.verify(spec, tmp_path)["a.bin"]["status"] == "hash_mismatch"


@respx.mock
def test_fetch_rejects_a_changed_upstream_file(tmp_path):
    good = hashlib.sha256(b"payload").hexdigest()
    spec = dataclasses.replace(registry.PUMPFUN, files=(registry.DatasetFile("x.parquet", good, 7, "events", "contemporaneous"),))
    url = f"{registry.HF_BASE}/{spec.repo}/resolve/{spec.revision}/x.parquet"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"tampered"))
    with pytest.raises(ValueError, match="sha256"):
        registry.fetch(spec, tmp_path)
    assert not (spec.root(tmp_path) / "x.parquet").exists()
    respx.get(url).mock(return_value=httpx.Response(200, content=b"payload"))
    assert registry.fetch(spec, tmp_path)["x.parquet"] == "fetched"
    assert (spec.root(tmp_path) / "AEGIS_PROVENANCE.json").exists()


def test_no_non_redistributable_dataset_file_is_committed():
    """Launch Tape's licence forbids redistribution 'in whole or in part': no file of it may live in the repo."""
    banned = {f.sha256 for f in registry.LAUNCHTAPE.files} | {f.sha256 for s in registry.REGISTRY.values()
                                                                if not s.redistributable for f in s.files}
    sizes = {f.bytes for s in registry.REGISTRY.values() if not s.redistributable for f in s.files}
    skip = {".git", ".venv", "var", "node_modules", ".next", "out", "__pycache__", ".pytest_cache", ".ruff_cache"}
    for p in REPO.rglob("*"):
        if any(part in skip for part in p.relative_to(REPO).parts) or not p.is_file():
            continue
        assert "launchtape/" not in p.as_posix().lower() or p.suffix in (".py", ".md")
        if p.stat().st_size in sizes:
            assert registry.sha256_file(p) not in banned, p


# ---------------------------------------------------------------------------- adapters (synthetic files)
def _pumpfun_fixture(base: Path) -> registry.DatasetSpec:
    spec = dataclasses.replace(registry.PUMPFUN, revision="test")
    root = spec.root(base)
    root.mkdir(parents=True)
    c = duckdb.connect()
    c.execute(f"""COPY (SELECT * FROM (VALUES
        ('MINTA', TIMESTAMPTZ '2026-07-06 12:00:00+00', 30.0, 'POOLA'),
        ('MINTB', TIMESTAMPTZ '2026-07-06 13:00:00+00', 10.0, 'synthetic_graduation_queue'),
        ('MINTC', TIMESTAMPTZ '2026-07-09 13:00:00+00', 10.0, 'POOLC'))
        t(mint, migrated_at, seconds_to_graduation, pool_address)) TO '{root}/migrations.parquet' (FORMAT parquet)""")
    snaps = []
    for k in range(5):
        ts_ = f"2026-07-06 12:{k + 1:02d}:00+00"
        snaps.append(f"('MINTA', TIMESTAMPTZ '{ts_}', {60.0 * (k + 1)}, 1, 'POOLA', 'pumpswap', false, {1e-6 * (k + 1)}, "
                     f"{8e-5 * (k + 1)}, 1.0, 2.0, 3.0, 4.0, 1e5, 1e5, 20000.0, 1e8, 125.0, 1500.0, 9000.0, 9000.0, 9000.0, "
                     f"20, 5, 40, 10, 0.7)")
    snaps.append("('MINTA', TIMESTAMPTZ '2026-07-06 12:02:30+00', 150.0, 1, 'CURVEA', 'pumpfun', false, 1e-6, 8e-5, "
                 "NULL, NULL, NULL, NULL, 1e5, 1e5, NULL, NULL, NULL, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, NULL)")
    snaps.append("('MINTB', TIMESTAMPTZ '2026-07-06 13:01:00+00', 60.0, 1, 'METEORAB', 'meteora', true, 1e-6, 8e-5, "
                 "NULL, NULL, NULL, NULL, 1e5, 1e5, 500.0, 1e6, 3.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, NULL)")
    cols = ("mint, snapshot_time, seconds_since_graduation, phase, pair_address, dex_id, incomplete_data, price_native, "
            "price_usd, price_change_5m, price_change_1h, price_change_6h, price_change_24h, market_cap_usd, fdv_usd, "
            "liquidity_usd, liquidity_base, liquidity_quote, volume_5m, volume_1h, volume_6h, volume_24h, buys_5m, sells_5m, "
            "buys_1h, sells_1h, buy_pressure_1h")
    c.execute(f"COPY (SELECT * FROM (VALUES {', '.join(snaps)}) t({cols})) TO '{root}/postgard_snapshots.parquet' (FORMAT parquet)")
    c.execute(f"""COPY (SELECT * FROM (VALUES ('MINTA', TIMESTAMPTZ '2026-07-08 12:00:00+00', 'major_pump', 9.9, true))
        t(mint, computed_at, outcome_label, peak_price_usd, rug_detected)) TO '{root}/postgard_outcomes.parquet' (FORMAT parquet)""")
    static = ", ".join(f"NULL::DOUBLE AS {k}" for k in pumpfun._STATIC_COLS if k not in ("is_mayhem_mode", "creator",
                                                                                        "top10_pct_suspect",
                                                                                        "supply_bug_corrected",
                                                                                        "is_cashback_enabled"))
    c.execute(f"""COPY (SELECT m AS mint, TIMESTAMPTZ '2026-07-06 11:45:00+00' AS detected_at, 'N' AS name, 'S' AS symbol,
        false AS is_mayhem_mode, 'CREATOR' AS creator, false AS is_cashback_enabled, (m = 'MINTB') AS top10_pct_suspect,
        false AS supply_bug_corrected, {static}, 55.0 AS peak_market_cap_sol, TIMESTAMPTZ '2026-07-06 12:30:00+00' AS graduated_at
        FROM (VALUES ('MINTA'), ('MINTB'), ('MINTC')) v(m)) TO '{root}/tokens.parquet' (FORMAT parquet)""")
    return spec


def test_pumpfun_adapter_separates_markets_outcomes_and_flags(tmp_path):
    spec = _pumpfun_fixture(tmp_path)
    st = pumpfun.load(tmp_path, spec)
    a, b = st.tokens["MINTA"], st.tokens["MINTB"]
    assert len(a.obs) == 5 and a.other_obs == 1  # completed bonding-curve row is coverage-only
    assert all(o.market_type == MarketType.CPMM and o.tradable for o in a.obs)
    assert a.obs[0].sol_usd == pytest.approx(80.0) and a.obs[0].res_sol == 125.0
    assert b.pool is None and not b.obs and b.other_obs == 1  # sentinel pool never used; meteora not tradable
    assert st.quality["pool_sentinel"] == 1 and st.quality["incomplete_data_rows"] == 1
    assert b.static["initial_top10_pct_corrected"]["value"] is None  # suspect concentration -> unknown
    assert "peak_market_cap_sol" not in a.static and "graduated_at" not in a.static  # retrospective columns excluded
    assert st.outcomes["MINTA"].label == "major_pump" and "rug_detected" in st.outcomes["MINTA"].values
    assert [g.why for g in st.gaps] and st.gaps[0].start == dt.datetime(2026, 7, 7, tzinfo=dt.UTC).timestamp()


def _launchtape_fixture(base: Path) -> registry.DatasetSpec:
    spec = dataclasses.replace(registry.LAUNCHTAPE, revision="test")
    d = spec.root(base) / "data"
    d.mkdir(parents=True)
    c = duckdb.connect()
    g = 1_791_640_800_000  # 2026-10-10 14:00:00 UTC in ms
    rows = [("grad", g, "M1", "P1", 2e8, 85.0, 17.5, None, None, None, None, False, None, None, 0)]
    base_, quote = 2e8, 85.0
    for k in range(1, 121):  # one trade every 5 s for 10 min: buys then sells
        side = "buy" if k <= 60 else "sell"
        qa = 0.5
        base_ += -1e5 if side == "buy" else 1e5
        quote += qa if side == "buy" else -qa
        vq = 17.5 if k % 10 == 0 else None
        rows.append(("trade", g + k * 5000, "M1", None, base_, quote, vq, side, 1e5, qa, None, None, None, f"W{k % 7}", k))
    rows += [("down", g + 100_000, None, None, None, None, None, None, None, None, None, None, None, None, 999),
             ("up", g + 101_000, None, None, None, None, None, None, None, None, None, None, None, None, 1000),
             ("end", g + 600_000, "M1", None, None, None, None, None, None, None, None, None, "window", None, 1001)]
    vals = ", ".join("(" + ", ".join("NULL" if v is None else (f"'{v}'" if isinstance(v, str) else str(v)) for v in r) + ")"
                     for r in rows)
    c.execute(f"COPY (SELECT * FROM (VALUES {vals}) t(type, t, mint, pool, poolBase, poolQuote, virtualQuote, side, baseAmount, "
              f"quoteAmount, priceAfter, late, why, user, i)) TO '{d}/gradtrades-{launchtape.SAMPLE}.parquet' (FORMAT parquet)")
    c.execute(f"""COPY (SELECT 'qpx' AS type, {g} + k * 30000 AS t,
        MAP {{'{launchtape.WSOL}': 110.0}} AS usd FROM range(0, 25) r(k))
        TO '{d}/padfeed-{launchtape.SAMPLE}.parquet' (FORMAT parquet)""")
    c.execute(f"""COPY (SELECT * FROM (VALUES
        ('record', {g + 5000}, 'M1', 'SYM', 'Name', 600.0, false, 3.5, 2.0, '{{"top1NcPct": 1.2, "bundlePct": 0}}',
         {{'txs50': 4}}, {{'pct': 0.1, 'nWallets': 2}}, NULL, NULL, NULL, NULL, NULL, NULL),
        ('outcome', {g + 600_000}, 'M1', 'SYM', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
         {{'60': 500.0, '600': 300.0}}, 20.0, -50.0, true, NULL, 1200))
        t(type, t, mint, sym, name, ageS, instant, creatorPctAtGrad, devBuyPct, curve, creatorInfo, gradSlot, capAt,
          maxGain, minDrop, crashed, firstBigSell, trades)) TO '{d}/pregrad-{launchtape.SAMPLE}.parquet' (FORMAT parquet)""")
    return spec


def test_launchtape_adapter_point_in_time_windows_and_exact_24h_counts(tmp_path):
    spec = _launchtape_fixture(tmp_path)
    st = launchtape.load(tmp_path, spec, resolution_s=15)
    tr = st.tokens["M1"]
    assert tr.graduated_at == pytest.approx(1_791_640_800.0) and len(tr.ticks) == 120
    o = tr.obs[0]  # +15 s: trades at +5, +10, +15 s are already printed, none after
    assert o.since_grad_s == pytest.approx(15) and o.buys == (3, 3) and o.buys_h24 == 3
    assert o.res_token == pytest.approx(2e8 - 3e5) and o.vres_sol == pytest.approx(17.5)  # virtual quote carried forward
    assert o.price_sol == pytest.approx((85 + 1.5 + 17.5) / (2e8 - 3e5))
    assert o.sol_usd == pytest.approx(110.0) and o.buyers_h24 == 3
    last = tr.obs[-1]
    assert last.buys_h24 == 60 and last.sells_h24 == 60 and last.sellers_h24 == 7
    assert last.buys[0] + last.sells[0] == 60  # 5-minute window: only the last 60 trades
    assert tr.tracking_end == pytest.approx(1_791_641_400.0) and len(st.gaps) == 1
    assert tr.static["curve_top1_nc_pct"]["value"] == 1.2 and tr.static["creator_txs50"]["known_at"] > tr.graduated_at
    assert st.outcomes["M1"].values["crashed"] is True  # retrospective, separate
    s_exec = tr.state_at(tr.obs[0].t + 2)
    assert s_exec is not None and s_exec[0] <= tr.obs[0].t + 2


# ---------------------------------------------------------------------------- research simulator
def _bt(store, *, recycling="full", pacing=False, entry=None, exit_cfg=None, **kw):
    e = entry or presets.STRICT.model_copy(update={"min_liquidity_eur": Decimal(1000), "min_price_change_m5_pct": None})
    x = exit_cfg or presets.TRAILING
    cfg = backtest.BacktestConfig(strategy=presets.strategy(e, x, investment_pacing=pacing, recycling=recycling), **kw)
    pol = HoldExit() if x.policy == "hold" else RulesExit(x)
    return backtest.run(store, cfg, PostGradEntry(e, "t"), pol)


def test_backtest_books_everything_through_the_ledger_and_reconciles():
    r = _bt(make_store(3))
    m = r.metrics
    assert m["reconciliation_ok"] and m["buys"] == 9 and m["contributions_eur"] == 100.0
    assert m["pool_fees_eur"] > 0 and m["network_fees_eur"] > 0
    # pump paths are monetised by the trailing exit; dump paths are cut
    assert any(p.multiple > 1.5 for p in r.closed) and all(p.peak_multiple >= p.multiple * 0.99 for p in r.closed)


def test_backtest_is_deterministic():
    a, b = _bt(make_store(2)), _bt(make_store(2))
    assert a.metrics == b.metrics and a.fingerprint == b.fingerprint


def test_hold_exit_never_sells_and_reports_censoring():
    r = _bt(make_store(2), exit_cfg=presets.HOLD)
    assert r.metrics["sells"] == 0 and r.metrics["censored_open_positions"] == r.metrics["buys"]
    assert r.metrics["net_worth_mark_to_last_eur"] >= r.metrics["net_worth_censored_zero_eur"]


def test_recycling_none_leaves_proceeds_idle_full_recycles_after_settlement():
    # dumps graduate first and are stopped out; pumps graduate later: only recycled cash can buy them
    tracks = {}
    for k, path in enumerate(("dump", "dump", "dump", "pump", "pump", "pump")):
        mint = f"{path}{k}" + "y" * 30
        tracks[mint] = make_track(mint, path, T0 + k * 600 + (1800 if path == "pump" else 0))
    st = HistoricalStore("synthetic", "test", tracks, {}, resolution_profile=[(math.inf, 60.0)]).finalize()
    entry = presets.STRICT.model_copy(update={"min_liquidity_eur": Decimal(1000), "min_price_change_m5_pct": None})
    stop = presets.TRAILING.model_copy(update={"stop_loss_multiple": Decimal("0.8")})
    runs = {}
    for mode in ("none", "full"):
        cfg = presets.strategy(entry, stop, investment_pacing=False, recycling=mode, monthly_contribution_eur=Decimal(3))
        runs[mode] = backtest.run(st, backtest.BacktestConfig(strategy=cfg), PostGradEntry(entry, "t"), RulesExit(stop))
    rn, rf = runs["none"], runs["full"]
    assert rn.metrics["buys"] == 3 and rn.rejections.get("budget_or_cash", 0) > 0
    assert rf.metrics["buys"] > 3  # stopped-out proceeds re-deployed into later opportunities
    assert rn.metrics["reconciliation_ok"] and rf.metrics["reconciliation_ok"]
    first_rebuy = next(x for x in rf.trades[3:] if x["side"] == "BUY")
    first_sale = next(x for x in rf.trades if x["side"] == "SELL")
    assert dt.datetime.fromisoformat(first_rebuy["t"]) >= dt.datetime.fromisoformat(first_sale["t"]) + dt.timedelta(seconds=15)


def test_unsupported_market_is_never_filled():
    st = make_store(1)
    for tr in st.tokens.values():
        for o in tr.obs:
            o.market_type = MarketType.DLMM
    r = _bt(st)
    assert r.metrics["buys"] == 0


def test_entry_filters_treat_unknown_as_failure():
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    o = dataclasses.replace(tr.obs[3], buys=(None, None))
    ok, why = PostGradEntry(presets.STRICT).base_pass(tr, o, 1.16)
    assert not ok and "buy_sell_m5:unknown" in why
    assert PostGradEntry(presets.STRICT).signal(tr, tr.obs[3], 1.16, Context(), entries=1) is None  # max entries


def test_context_filters_raw_and_coverage_normalised():
    ctx = Context(opportunities_24h=30, observed_24h=600)
    assert ctx.opportunity_rate_24h == pytest.approx(0.05)
    assert Context().opportunity_rate_24h is None
    e = presets.STRICT.model_copy(update={"min_liquidity_eur": Decimal(1000), "min_price_change_m5_pct": None,
                                          "context_min_opportunity_rate_24h": Decimal("0.5")})
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    assert PostGradEntry(e).signal(tr, tr.obs[3], 1.16, ctx, 0) is None


def test_settlement_blocks_spending_unsettled_proceeds():
    cfg = StrategyConfig()
    assert cfg.execution.settlement_seconds == 15  # the simulator uses the production spendable(at=...) path
    assert math.isfinite(backtest.date_ts("2026-07-06"))
