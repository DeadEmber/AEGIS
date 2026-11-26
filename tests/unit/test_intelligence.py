"""Security (cases 3, 8), features & look-ahead (17, 23), scores, states (25), discovery filters."""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from aegis.config import DiscoveryConfig, SecurityConfig
from aegis.core.clock import UTC
from aegis.discovery.engine import evaluate_eligibility, primary_pool
from aegis.features.library import REGISTRY, FeatureContext, HistoryPoint, compute
from aegis.marketdata.geckoterminal import parse_pool, parse_token_info
from aegis.marketdata.jupiter import parse_quote, parse_token
from aegis.marketdata.solana_rpc import parse_mint
from aegis.scoring.heuristics import score_all
from aegis.security.engine import SecurityEngine, SecurityInputs, Status, Verdict
from aegis.state.machine import MarketState, StateInputs, evaluate
from tests.conftest import fixture

NOW = dt.datetime(2026, 11, 26, 11, 5, tzinfo=UTC)
PUMPCHAN = "Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpump"
EURUSD = Decimal("1.0935")


def _hot_pool():
    return next(o for o in (parse_pool(p, NOW) for p in fixture("gt_multi.json")["data"]) if o.token_address == PUMPCHAN)


def _secure_inputs(**over):
    r = fixture("rpc_mint.json")["result"]
    base = dict(
        token_address=PUMPCHAN, mint=parse_mint(PUMPCHAN, r["value"], NOW, None),
        gt_profile=parse_token_info(fixture("gt_token_info.json"), NOW),
        jup_profile=parse_token(fixture("jup_token.json")[0], NOW), pool=_hot_pool(), liquidity_eur=Decimal(190000),
        buy_quote=parse_quote(fixture("jup_buy.json"), NOW), sell_quote=parse_quote(fixture("jup_sell.json"), NOW),
        quotes_attempted=True,
    )
    base.update(over)
    return SecurityInputs(**base)


def test_real_pump_token_assessment_is_explainable():
    a = SecurityEngine(SecurityConfig()).assess(_secure_inputs())
    by = {f.check: f for f in a.findings}
    assert by["mint_authority"].status == Status.PASS and by["mint_authority"].evidence == "CONFIRMED"
    assert by["freeze_authority"].status == Status.PASS
    assert by["sell_route"].status == Status.PASS and by["roundtrip_loss"].status == Status.PASS
    # real data: 9918 trades/1 h by ~3.8k addresses, volume ~15x liquidity -> manipulation indicators
    assert a.verdict in (Verdict.RISK, Verdict.PASS)
    assert a.verdict != Verdict.BLOCK, a.blocking_reasons


def test_dangerous_token2022_extensions_block():  # case 3
    r = fixture("rpc_mint_t22.json")["result"]
    m = parse_mint("2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo", r["value"], NOW, None)
    a = SecurityEngine(SecurityConfig()).assess(_secure_inputs(mint=m))
    assert a.verdict == Verdict.BLOCK
    reasons = " ".join(a.blocking_reasons)
    assert "freeze_authority" in reasons and "ext:permanentDelegate" in reasons


def test_no_sell_route_blocks_unsellable_token():  # case 8
    a = SecurityEngine(SecurityConfig()).assess(_secure_inputs(sell_quote=None))
    assert a.verdict == Verdict.BLOCK and "sell_route:CRITICAL" in a.blocking_reasons


def test_unknown_is_not_safe():
    a = SecurityEngine(SecurityConfig()).assess(SecurityInputs(token_address=PUMPCHAN))
    assert a.verdict == Verdict.BLOCK
    assert all(s.endswith("UNKNOWN") for s in a.blocking_reasons)
    a2 = SecurityEngine(SecurityConfig()).assess(_secure_inputs(quote_fetch_failed=True, sell_quote=None))
    assert "sell_route:UNKNOWN" in a2.blocking_reasons  # provider error => unknown, not 'unsellable'


def test_roundtrip_tax_detected():
    sell = parse_quote(fixture("jup_sell.json"), NOW)
    taxed = dataclasses.replace(sell, out_amount_raw=sell.out_amount_raw // 2)
    a = SecurityEngine(SecurityConfig()).assess(_secure_inputs(sell_quote=taxed))
    assert "roundtrip_loss:CRITICAL" in a.blocking_reasons


def _ctx(as_of=NOW, history=(), **kw):
    return FeatureContext(as_of=as_of, obs=_hot_pool(), history=list(history), **kw)


def test_features_from_real_snapshot_and_explicit_missing():  # case 23
    fv = compute(_ctx())
    assert fv.values["ret_h1"] == pytest.approx(0.56226)
    assert fv.values["buyer_share_h1"] == pytest.approx(2714 / (2714 + 1062))
    assert "realized_vol_h6" in fv.missing and fv.missing["realized_vol_h6"] == "insufficient_history"
    assert "liquidity_change_h24" in fv.missing
    assert set(fv.values) | set(fv.missing) == set(REGISTRY)
    assert all(spec.definition and spec.unit for spec in REGISTRY.values())


def test_no_lookahead_allowed():  # case 17
    future = HistoryPoint(NOW + dt.timedelta(minutes=1), Decimal(1), Decimal(1), "p")
    with pytest.raises(ValueError, match="look-ahead"):
        compute(_ctx(history=[future]))
    with pytest.raises(ValueError, match="look-ahead"):
        compute(_ctx(as_of=NOW - dt.timedelta(seconds=1)))


def test_history_features_and_scores():
    obs = _hot_pool()
    hist = [HistoryPoint(NOW - dt.timedelta(hours=h), obs.price_usd * Decimal(str(1 + 0.05 * (h % 3))),
                         obs.liquidity_usd * Decimal("0.8"), obs.pool_address) for h in (5, 4, 3, 2, 1)]
    fv = compute(_ctx(history=hist, ath_price_usd=obs.price_usd * 2, low_since_ath_usd=obs.price_usd / 2,
                      ath_at=NOW - dt.timedelta(hours=10)))
    assert "realized_vol_h6" in fv.values and fv.values["drawdown_from_ath"] == pytest.approx(0.5)
    assert fv.values["liquidity_change_h1"] == pytest.approx(0.25)
    sc = score_all(fv, data_age_minutes=1)
    for s in (sc.prs, sc.dqi, sc.mri, sc.epi, sc.ssi, sc.swp, sc.mci):
        assert s.kind == "heuristic" and (s.value is None or 0 <= s.value <= 1)


def _state(fv, prev=MarketState.WATCHLIST, **kw):
    base = dict(features=fv, scores=score_all(fv) if fv else None, previous=prev, data_available=True,
                security_verdict="PASS", security_confirmed_block=False, consecutive_no_sell_route=0,
                hours_without_sell_route=0, eligible=True, explosion_count=0)
    base.update(kw)
    return evaluate(StateInputs(**base))


def test_state_machine_explosion_unknown_untradable():
    fv = compute(_ctx())
    assert _state(fv).state in (MarketState.EXPLOSION, MarketState.DISTRIBUTION)  # +26000% in 24h
    assert _state(None, data_available=False).state == MarketState.UNKNOWN  # API failure != untradable
    s = _state(fv, consecutive_no_sell_route=3, hours_without_sell_route=2.5)
    assert s.state == MarketState.UNTRADABLE
    assert _state(fv, consecutive_no_sell_route=5, hours_without_sell_route=0.5).state != MarketState.UNTRADABLE


def test_second_wave_detected():  # case 25
    fv = compute(_ctx(ath_price_usd=_hot_pool().price_usd * 20, low_since_ath_usd=_hot_pool().price_usd / 10))
    s = _state(fv, explosion_count=1, prev=MarketState.RECOVERY)
    assert s.state == MarketState.SECOND_WAVE and s.changed


def test_eligibility_filters_and_fdv_labelled():
    obs = _hot_pool()
    e = evaluate_eligibility(obs, EURUSD, NOW, DiscoveryConfig())
    names = {r.name: r for r in e.results}
    assert "cap_unknown_used_fdv" in e.tags  # GT market_cap null -> explicit FDV filter
    assert names["price_eur"].outcome == "fail"  # 0.0075 EUR > 0.001 experimental max
    assert names["token_age_hours"].outcome == "fail"  # pool created ~9.5h earlier
    assert not e.eligible and "price_eur" in e.failed
    cfg = DiscoveryConfig(max_price_eur=None, min_token_age_hours=Decimal(1), max_fdv_eur=Decimal(10**8))
    assert evaluate_eligibility(obs, EURUSD, NOW, cfg).eligible


def test_primary_pool_excludes_bonding_curve():
    payload = fixture("gt_new.json")
    obs = [o for o in (parse_pool(p, NOW) for p in payload["data"]) if o]
    curve = [o for o in obs if o.pool_type == "bonding_curve"]
    assert curve
    assert primary_pool(curve, allow_bonding_curve=False) is not None  # falls back but stays flagged by filters
    amm = dataclasses.replace(curve[0], pool_type="amm", liquidity_usd=Decimal(1), pool_address="Z")
    assert primary_pool([curve[0], amm], allow_bonding_curve=False).pool_address == "Z"
