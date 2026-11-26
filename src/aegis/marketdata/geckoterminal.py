"""GeckoTerminal public API v2 adapter (validated against live responses on 2026-11-26).

Docs: https://api.geckoterminal.com/docs/index.html — free tier, no key; observed HTTP 429 after ~7
calls in 17 s, so the default spacing is 6.5 s (~9 calls/min). Paid tier = CoinGecko onchain API.
Known caveats encoded below:
- `market_cap_usd` is frequently null -> kept as None (never replaced by FDV).
- price_change / transactions are reported from the BASE token's perspective. When the speculative
  token is the QUOTE side we invert them and add the quality flag `inverted_from_quote_side`.
- `pump-fun` pools are bonding curves, not AMMs.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from aegis.core.assets import SOLANA_QUOTE_ASSETS
from aegis.core.clock import UTC, parse_ts
from aegis.core.money import ONE, parse_optional
from aegis.marketdata.http import ResilientHttp, SourceError
from aegis.marketdata.models import WINDOWS, Candle, PoolObservation, TokenProfile, WindowStats

SOURCE = "geckoterminal"
BASE_URL = "https://api.geckoterminal.com/api/v2"

POOL_TYPES = {
    "pump-fun": "bonding_curve",
    "raydium-launchlab": "bonding_curve",
    "meteora-dbc": "bonding_curve",
    "moonshot": "bonding_curve",
    "raydium-clmm": "clmm",
    "orca": "clmm",
    "meteora": "clmm",  # DLMM
    "raydium": "amm",
    "raydium-cp": "amm",
    "pumpswap": "amm",
    "meteora-damm-v2": "amm",
    "meteora-damm": "amm",
}


def _int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _invert_pct(p: Decimal | None) -> Decimal | None:
    if p is None:
        return None
    r = ONE + p / 100
    if r <= 0:
        return None
    return (ONE / r - ONE) * 100


def _token_addr(rel: dict[str, Any], key: str) -> str | None:
    tid = ((rel.get(key) or {}).get("data") or {}).get("id") or ""
    _, _, addr = tid.partition("_")
    return addr or None


def parse_pool(item: dict[str, Any], observed_at: dt.datetime, included: dict[str, dict[str, Any]] | None = None,
               keep_raw: bool = False) -> PoolObservation | None:
    a = item.get("attributes") or {}
    rel = item.get("relationships") or {}
    base = _token_addr(rel, "base_token")
    quote = _token_addr(rel, "quote_token")
    dex = ((rel.get("dex") or {}).get("data") or {}).get("id")
    flags: list[str] = []
    if base and base not in SOLANA_QUOTE_ASSETS:
        side, token, counter = "base", base, quote
        if quote and quote not in SOLANA_QUOTE_ASSETS:
            flags.append("non_standard_pair")
    elif quote and quote not in SOLANA_QUOTE_ASSETS:
        side, token, counter = "quote", quote, base
        flags.append("inverted_from_quote_side")
    else:
        return None  # SOL/USDC-type infra pool
    price = parse_optional(a.get(f"{side}_token_price_usd"))
    windows: dict[str, WindowStats] = {}
    tx = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    pc = a.get("price_change_percentage") or {}
    for w in WINDOWS:
        t = tx.get(w) or {}
        change = parse_optional(pc.get(w))
        buys, sells, buyers, sellers = _int(t.get("buys")), _int(t.get("sells")), _int(t.get("buyers")), _int(t.get("sellers"))
        if side == "quote":
            change = _invert_pct(change)
            buys, sells, buyers, sellers = sells, buys, sellers, buyers
        windows[w] = WindowStats(volume_usd=parse_optional(vol.get(w)), price_change_pct=change, buys=buys,
                                 sells=sells, buyers=buyers, sellers=sellers)
    meta = (included or {}).get(f"solana_{token}") or {}
    liq = parse_optional(a.get("reserve_in_usd"))
    if liq is None:
        flags.append("liquidity_missing")
    if a.get("market_cap_usd") is None:
        flags.append("market_cap_missing")
    return PoolObservation(
        source=SOURCE, network="solana", pool_address=a.get("address") or item.get("id", "").partition("_")[2],
        token_address=token, token_side=side, counter_asset=counter, dex=dex, pool_type=POOL_TYPES.get(dex or "", "unknown"),
        observed_at=observed_at, price_usd=price, liquidity_usd=liq, fdv_usd=parse_optional(a.get("fdv_usd")),
        market_cap_usd=parse_optional(a.get("market_cap_usd")) if side == "base" else None, windows=windows,
        pool_created_at=parse_ts(a.get("pool_created_at")), token_symbol=meta.get("symbol"),
        token_name=meta.get("name"), token_decimals=_int(meta.get("decimals")), quality_flags=tuple(flags),
        raw=item if keep_raw else None,
    )


def _included_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i.get("attributes", {}) for i in payload.get("included") or [] if i.get("type") == "token"}


def _validate(payload: Any) -> None:
    if not isinstance(payload, dict) or "data" not in payload:
        status = (payload or {}).get("status", {}) if isinstance(payload, dict) else {}
        if status.get("error_code") == 429:
            raise SourceError(SOURCE, "rate_limited", "429 in body", 429)
        raise SourceError(SOURCE, "parse", f"unexpected payload keys: {list(payload)[:5] if isinstance(payload, dict) else type(payload)}")


class GeckoTerminalClient:
    MULTI_MAX = 30

    def __init__(self, http: ResilientHttp | None = None, *, min_interval_s: float = 6.5, keep_raw: bool = False):
        self.http = http or ResilientHttp(SOURCE, BASE_URL, min_interval_s=min_interval_s, backoff_base_s=10.0)
        self.keep_raw = keep_raw

    def _pools(self, path: str, params: dict[str, Any] | None = None, ttl: float = 0) -> list[PoolObservation]:
        payload, meta = self.http.get_json(path, params, cache_ttl_s=ttl, validate=_validate)
        inc = _included_map(payload)
        out = []
        for item in payload.get("data") or []:
            obs = parse_pool(item, meta.fetched_at, inc, self.keep_raw)
            if obs is not None:
                out.append(obs)
        return out

    def new_pools(self, page: int = 1) -> list[PoolObservation]:
        return self._pools("/networks/solana/new_pools", {"page": page, "include": "base_token,quote_token,dex"}, 30)

    def trending_pools(self, page: int = 1, duration: str = "1h") -> list[PoolObservation]:
        return self._pools("/networks/solana/trending_pools",
                           {"page": page, "duration": duration, "include": "base_token,quote_token"}, 60)

    def pools_multi(self, addresses: list[str]) -> list[PoolObservation]:
        out: list[PoolObservation] = []
        for i in range(0, len(addresses), self.MULTI_MAX):
            chunk = ",".join(addresses[i:i + self.MULTI_MAX])
            out.extend(self._pools(f"/networks/solana/pools/multi/{chunk}", {"include": "base_token,quote_token"}))
        return out

    def token_pools(self, token: str, page: int = 1) -> list[PoolObservation]:
        return self._pools(f"/networks/solana/tokens/{token}/pools", {"page": page, "include": "base_token,quote_token"})

    def token_info(self, token: str) -> TokenProfile:
        payload, meta = self.http.get_json(f"/networks/solana/tokens/{token}/info", validate=_validate, cache_ttl_s=300)
        return parse_token_info(payload, meta.fetched_at)

    def ohlcv(self, pool: str, timeframe: str = "minute", aggregate: int = 5, limit: int = 1000,
              before: dt.datetime | None = None) -> list[Candle]:
        params: dict[str, Any] = {"aggregate": aggregate, "limit": limit, "currency": "usd"}
        if before is not None:
            params["before_timestamp"] = int(before.timestamp())
        payload, _ = self.http.get_json(f"/networks/solana/pools/{pool}/ohlcv/{timeframe}", params, validate=_validate)
        return parse_ohlcv(payload)


def parse_token_info(payload: dict[str, Any], observed_at: dt.datetime) -> TokenProfile:
    a = (payload.get("data") or {}).get("attributes") or {}
    holders = a.get("holders") or {}
    dist = holders.get("distribution_percentage") or {}

    def yn(v: Any) -> bool | None:  # GT reports "no" when the authority is absent
        return {"no": True, "yes": False}.get(str(v).lower()) if v is not None else None

    return TokenProfile(
        source=SOURCE, token_address=a.get("address", ""), observed_at=observed_at,
        holders_count=_int(holders.get("count")), top10_pct=parse_optional(dist.get("top_10")),
        dev_holding_pct=parse_optional(a.get("developer_holding_percentage")),
        developer_address=a.get("developer_address"), mint_authority_disabled=yn(a.get("mint_authority")),
        freeze_authority_disabled=yn(a.get("freeze_authority")), honeypot=a.get("is_honeypot"),
        launchpad=("pump.fun" if "pump-fun" in (a.get("gt_category_ids") or []) else None),
        symbol=a.get("symbol"), name=a.get("name"), decimals=_int(a.get("decimals")),
        extra={"gt_score": a.get("gt_score"), "categories": a.get("categories"), "gt_verified": a.get("gt_verified"),
               "launchpad_details": a.get("launchpad_details")},
    )


def parse_ohlcv(payload: dict[str, Any]) -> list[Candle]:
    rows = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    out = []
    for r in rows:
        try:
            ts, o, h, lo, c, v = r
        except (TypeError, ValueError):
            continue
        vals = [parse_optional(x) for x in (o, h, lo, c, v)]
        if any(x is None for x in vals):
            continue
        out.append(Candle(dt.datetime.fromtimestamp(int(ts), UTC), *vals))  # type: ignore[arg-type]
    out.sort(key=lambda c: c.ts)
    return out
