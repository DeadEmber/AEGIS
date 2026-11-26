"""DexScreener adapter — secondary source for cross-provider consistency checks and discovery.

Validated 2026-11-26: /token-pairs/v1/{chain}/{token}, /tokens/v1/{chain}/{a,b,..} (<=30),
/token-profiles/latest/v1. Rate limits per docs: 300 req/min (pairs), 60 req/min (profiles).
Caveat: `marketCap` may equal FDV; never treated as verified circulating cap.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aegis.core.assets import SOLANA_QUOTE_ASSETS
from aegis.core.clock import parse_ts
from aegis.core.money import parse_optional
from aegis.marketdata.http import ResilientHttp
from aegis.marketdata.models import WINDOWS, PoolObservation, WindowStats

SOURCE = "dexscreener"
DEX_TYPES = {"pumpswap": "amm", "raydium": "amm", "orca": "clmm", "meteora": "clmm", "pumpfun": "bonding_curve"}


def parse_pair(p: dict[str, Any], observed_at: dt.datetime) -> PoolObservation | None:
    if p.get("chainId") != "solana":
        return None
    base = (p.get("baseToken") or {}).get("address")
    quote = (p.get("quoteToken") or {}).get("address")
    if base and base not in SOLANA_QUOTE_ASSETS:
        side, token, counter = "base", base, quote
    elif quote and quote not in SOLANA_QUOTE_ASSETS:
        return None  # DexScreener prices refer to the base token; skip quote-side pairs (no inversion data)
    else:
        return None
    txns = p.get("txns") or {}
    vol = p.get("volume") or {}
    pc = p.get("priceChange") or {}
    windows = {
        w: WindowStats(volume_usd=parse_optional(vol.get(w)), price_change_pct=parse_optional(pc.get(w)),
                       buys=(txns.get(w) or {}).get("buys"), sells=(txns.get(w) or {}).get("sells"))
        for w in WINDOWS
    }
    flags = ["market_cap_unverified"]
    return PoolObservation(
        source=SOURCE, network="solana", pool_address=p.get("pairAddress", ""), token_address=token, token_side=side,
        counter_asset=counter, dex=p.get("dexId"), pool_type=DEX_TYPES.get(p.get("dexId") or "", "unknown"),
        observed_at=observed_at, price_usd=parse_optional(p.get("priceUsd")),
        liquidity_usd=parse_optional((p.get("liquidity") or {}).get("usd")), fdv_usd=parse_optional(p.get("fdv")),
        market_cap_usd=None, windows=windows, pool_created_at=parse_ts(p.get("pairCreatedAt")),
        token_symbol=(p.get("baseToken") or {}).get("symbol"), token_name=(p.get("baseToken") or {}).get("name"),
        quality_flags=tuple(flags),
    )


class DexScreenerClient:
    def __init__(self, http: ResilientHttp | None = None, min_interval_s: float = 1.0):
        self.http = http or ResilientHttp(SOURCE, "https://api.dexscreener.com", min_interval_s=min_interval_s)

    def tokens(self, addresses: list[str]) -> list[PoolObservation]:
        out: list[PoolObservation] = []
        for i in range(0, len(addresses), 30):
            payload, meta = self.http.get_json(f"/tokens/v1/solana/{','.join(addresses[i:i + 30])}", cache_ttl_s=30)
            for p in payload if isinstance(payload, list) else []:
                obs = parse_pair(p, meta.fetched_at)
                if obs:
                    out.append(obs)
        return out

    def latest_profiles(self) -> list[str]:
        payload, _ = self.http.get_json("/token-profiles/latest/v1", cache_ttl_s=60)
        return [p["tokenAddress"] for p in payload if isinstance(p, dict) and p.get("chainId") == "solana"
                and p.get("tokenAddress")]
