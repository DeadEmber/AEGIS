"""Market-data adapters against REAL recorded responses (tests/fixtures/recorded_2026-11-26) + case 10."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest

from aegis.core.clock import UTC
from aegis.marketdata.dexscreener import parse_pair
from aegis.marketdata.fx import CoinGeckoClient, FrankfurterClient
from aegis.marketdata.geckoterminal import GeckoTerminalClient, parse_ohlcv, parse_pool, parse_token_info
from aegis.marketdata.http import ResilientHttp, SourceError
from aegis.marketdata.jupiter import parse_quote, parse_token, quote_roundtrip_loss_pct
from aegis.marketdata.quality import cross_check
from aegis.marketdata.solana_rpc import SolanaRpcClient, parse_mint
from tests.conftest import FIXTURES, fixture

NOW = dt.datetime(2026, 11, 26, 11, 5, tzinfo=UTC)
PUMPCHAN = "Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpump"


def _http(handler, source="test", **kw) -> ResilientHttp:
    return ResilientHttp(source, "https://example.test", min_interval_s=0, sleep=lambda s: None,
                         transport=httpx.MockTransport(handler), **kw)


def test_gt_new_pools_parse():
    payload = fixture("gt_new.json")
    inc = {i["id"]: i["attributes"] for i in payload["included"] if i["type"] == "token"}
    obs = [o for o in (parse_pool(p, NOW, inc) for p in payload["data"]) if o]
    assert obs, "no observations parsed"
    first = obs[0]
    assert first.token_address == "9PDLcx5cgKGEbgq7BCpjdL6i927GQF3yNSzMYLtzpump"
    assert first.pool_type == "bonding_curve" and first.dex == "pump-fun"
    assert first.market_cap_usd is None and "market_cap_missing" in first.quality_flags
    assert first.token_decimals == 6 and first.token_symbol == "konnichiwa"
    assert first.w("h24").sells == 1 and first.w("h24").buyers == 0
    assert all(o.token_address not in ("So11111111111111111111111111111111111111112",) for o in obs)


def test_gt_quote_side_token_is_inverted():
    item = {"id": "solana_P", "attributes": {
        "address": "P", "base_token_price_usd": "70", "quote_token_price_usd": "0.5",
        "reserve_in_usd": "20000", "price_change_percentage": {"h1": "100"},
        "transactions": {"h1": {"buys": 10, "sells": 3, "buyers": 7, "sellers": 2}}, "volume_usd": {"h1": "5"}},
        "relationships": {"base_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}},
                          "quote_token": {"data": {"id": "solana_Tok"}}, "dex": {"data": {"id": "raydium"}}}}
    o = parse_pool(item, NOW)
    assert o.token_address == "Tok" and o.token_side == "quote" and o.price_usd == Decimal("0.5")
    assert o.w("h1").buys == 3 and o.w("h1").sells == 10  # base buys == quote sells
    assert o.w("h1").price_change_pct == Decimal(-50)  # base x2 => quote x0.5
    assert "inverted_from_quote_side" in o.quality_flags


def test_gt_multi_and_missing_values_stay_none():
    payload = fixture("gt_multi.json")
    obs = [parse_pool(p, NOW) for p in payload["data"]]
    hot = next(o for o in obs if o.token_address == PUMPCHAN)
    assert hot.pool_type == "amm" and hot.w("h1").buyers == 2714
    assert hot.liquidity_usd > Decimal(100000)
    item = {"id": "solana_X", "attributes": {"address": "X", "base_token_price_usd": None, "reserve_in_usd": None},
            "relationships": {"base_token": {"data": {"id": "solana_Tok"}}}}
    o = parse_pool(item, NOW)
    assert o.price_usd is None and o.liquidity_usd is None and "liquidity_missing" in o.quality_flags


def test_gt_token_info_and_ohlcv():
    prof = parse_token_info(fixture("gt_token_info.json"), NOW)
    assert prof.holders_count == 12441 and prof.top10_pct == Decimal("20.005")
    assert prof.mint_authority_disabled is True and prof.freeze_authority_disabled is True
    assert prof.honeypot == "unknown" and prof.launchpad == "pump.fun"
    candles = parse_ohlcv(fixture("gt_ohlcv.json"))
    assert len(candles) == 10 and candles[0].ts < candles[-1].ts
    assert all(c.low <= c.close <= c.high for c in candles)


def test_jupiter_quote_and_token():
    q = parse_quote(fixture("jup_buy.json"), NOW)
    assert q.in_amount_raw == 14_000_000 and q.out_amount_raw == 122_451_083
    assert q.price_impact_pct == Decimal("0.19061848111210329015991694")  # fraction -> percent
    assert q.route_labels == ("Pump.fun Amm",)
    s = parse_quote(fixture("jup_sell.json"), NOW)
    loss = quote_roundtrip_loss_pct(q, s)
    assert Decimal(0) < loss < Decimal(5)
    t = parse_token(fixture("jup_token.json")[0], NOW)
    assert t.mint_authority_disabled and t.freeze_authority_disabled
    assert t.organic_score == Decimal("33.71950896463565")
    assert t.circulating_supply == t.total_supply and t.token_program.startswith("Tokenz")


def test_rpc_mint_parse_token2022_extensions():
    r = fixture("rpc_mint.json")["result"]
    m = parse_mint(PUMPCHAN, r["value"], NOW, r["context"]["slot"])
    assert m.program == "spl-token-2022" and m.decimals == 6
    assert m.mint_authority is None and m.freeze_authority is None
    assert set(m.extensions) == {"metadataPointer", "tokenMetadata"}
    r2 = fixture("rpc_mint_t22.json")["result"]
    m2 = parse_mint("2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo", r2["value"], NOW, None)
    assert {"permanentDelegate", "transferHook", "transferFeeConfig"} <= set(m2.extensions)
    assert m2.freeze_authority is not None


def test_rpc_429_in_body_retried_then_unknown():
    body = fixture("rpc_largest.json")
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=body)

    rpc = SolanaRpcClient(http=_http(handler, "solana_rpc", max_retries=2))
    assert rpc.largest_accounts(PUMPCHAN) is None  # unknown, never "safe"
    assert len(calls) == 3


def test_http_retries_429_then_succeeds_and_caches():  # case 10: API error handling
    seq = [httpx.Response(429, headers={"retry-after": "0"}, json=fixture("gt_ratelimited.json")),
           httpx.Response(200, json=fixture("gt_multi.json"))]

    def handler(req):
        return seq.pop(0) if seq else httpx.Response(500)

    gt = GeckoTerminalClient(http=_http(handler, "geckoterminal"))
    obs = gt._pools("/x", ttl=60)
    assert obs and gt.http.stats.rate_limited == 1
    again = gt._pools("/x", ttl=60)
    assert len(again) == len(obs) and gt.http.stats.cache_hits == 1


def test_http_non_transient_error_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, text="bad")

    h = _http(handler)
    with pytest.raises(SourceError) as e:
        h.get_json("/q")
    assert e.value.kind == "http_4xx" and len(calls) == 1


def test_http_network_failure_raises_after_retries():
    def handler(req):
        raise httpx.ConnectError("dns")

    h = _http(handler, max_retries=2)
    with pytest.raises(SourceError) as e:
        h.get_json("/q")
    assert e.value.kind == "network" and h.stats.consecutive_errors == 3


def test_gt_429_body_with_200_status_is_rate_limited():
    h = _http(lambda req: httpx.Response(200, json=fixture("gt_ratelimited.json")), "geckoterminal", max_retries=0)
    gt = GeckoTerminalClient(http=h)
    with pytest.raises(SourceError) as e:
        gt.new_pools()
    assert e.value.kind == "rate_limited"


def test_fx_and_coingecko_parse():
    fx = FrankfurterClient(http=_http(lambda r: httpx.Response(200, json=fixture("fx.json")))).eur_usd()
    assert fx.rate == Decimal("1.0935") and fx.base == "EUR" and fx.quote == "USD"
    cg = CoinGeckoClient(http=_http(lambda r: httpx.Response(200, json=fixture("cg_price.json")))).prices_eur()
    assert cg["SOL"].rate == Decimal("64.17") and cg["BTC"].quote == "EUR"


def test_dexscreener_and_cross_check():
    ds = [parse_pair(p, NOW) for p in fixture("ds_token_pairs.json")]
    ds_pool = next(o for o in ds if o)
    gt_pool = next(o for o in (parse_pool(p, NOW) for p in fixture("gt_multi.json")["data"]) if o.token_address == PUMPCHAN)
    assert ds_pool.pool_address == gt_pool.pool_address
    issues = cross_check(gt_pool, ds_pool)
    kinds = {i.kind for i in issues}
    # Pool-level liquidity agrees (GT reserve_in_usd 212,160 vs DS liquidity.usd 211,511: both sides).
    # NOTE: token-level metrics differ by definition (GT tokens/{a} total_reserve_in_usd 102,500 and
    # Jupiter `liquidity` 105,003 count ~one side). CPMM estimates use POOL two-sided liquidity.
    assert not kinds & {"price_divergence", "liquidity_divergence"}
    import dataclasses
    bad = dataclasses.replace(ds_pool, price_usd=gt_pool.price_usd * 2)
    assert "price_divergence" in {i.kind for i in cross_check(gt_pool, bad)}


def test_fixture_provenance_documented():
    names = {p.name for p in FIXTURES.glob("*.json")}
    text = (FIXTURES / "PROVENANCE.md").read_text()
    assert all(n in text for n in names), "every fixture must be documented in PROVENANCE.md"
    json.loads((FIXTURES / "gt_new.json").read_text())
