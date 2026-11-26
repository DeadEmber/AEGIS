"""Jupiter adapter: executable swap quotes (Swap API v1) and token intelligence (Tokens API v2).

Validated on 2026-11-26 against https://lite-api.jup.ag (free, keyless). `api.jup.ag` requires a key.
Interpretation note: Swap v1 `priceImpactPct` is a FRACTION (0.0019 = 0.19%). Evidence: the Ultra
`/order` response for the same pair reports both `priceImpactPct=-0.002786` and `priceImpact=-0.2786`.
We convert to percent. A quote is an indication of executability at fetch time, not a fill.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from aegis.core.clock import parse_ts
from aegis.core.money import parse_optional
from aegis.execution.types import SwapQuote
from aegis.marketdata.http import ResilientHttp, SourceError
from aegis.marketdata.models import TokenProfile

SOURCE = "jupiter"
LITE_URL = "https://lite-api.jup.ag"


def parse_quote(q: dict[str, Any], fetched_at: dt.datetime) -> SwapQuote:
    try:
        impact = parse_optional(q.get("priceImpactPct"))
        pf = q.get("platformFee") or {}
        return SwapQuote(
            source="jupiter_swap_v1", input_mint=q["inputMint"], output_mint=q["outputMint"],
            in_amount_raw=int(q["inAmount"]), out_amount_raw=int(q["outAmount"]),
            min_out_raw=int(q.get("otherAmountThreshold") or q["outAmount"]),
            price_impact_pct=None if impact is None else impact * 100,
            route_labels=tuple((r.get("swapInfo") or {}).get("label", "?") for r in q.get("routePlan") or []),
            fetched_at=fetched_at, context_slot=q.get("contextSlot"),
            platform_fee_bps=int(pf.get("feeBps") or 0) if isinstance(pf, dict) else 0,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(SOURCE, "parse", f"bad quote payload: {exc}") from exc


def parse_token(t: dict[str, Any], observed_at: dt.datetime) -> TokenProfile:
    audit = t.get("audit") or {}
    return TokenProfile(
        source=SOURCE, token_address=t.get("id", ""), observed_at=observed_at,
        holders_count=t.get("holderCount"), top10_pct=parse_optional(audit.get("topHoldersPercentage")),
        dev_holding_pct=parse_optional(audit.get("devBalancePercentage")), developer_address=t.get("dev"),
        mint_authority_disabled=audit.get("mintAuthorityDisabled"),
        freeze_authority_disabled=audit.get("freezeAuthorityDisabled"),
        circulating_supply=parse_optional(t.get("circSupply")), total_supply=parse_optional(t.get("totalSupply")),
        market_cap_usd=parse_optional(t.get("mcap")), fdv_usd=parse_optional(t.get("fdv")),
        liquidity_usd=parse_optional(t.get("liquidity")), organic_score=parse_optional(t.get("organicScore")),
        launchpad=t.get("launchpad"), token_program=t.get("tokenProgram"), decimals=t.get("decimals"),
        symbol=t.get("symbol"), name=t.get("name"),
        created_at=parse_ts((t.get("firstPool") or {}).get("createdAt") or t.get("createdAt")),
        extra={
            "organic_label": t.get("organicScoreLabel"), "tags": t.get("tags"), "dev_mints": audit.get("devMints"),
            "dev_migrations": audit.get("devMigrations"), "graduated_pool": t.get("graduatedPool"),
            "stats1h": t.get("stats1h"), "stats24h": t.get("stats24h"),
        },
    )


class JupiterClient:
    def __init__(self, http: ResilientHttp | None = None, *, base_url: str = LITE_URL, api_key: str = "",
                 min_interval_s: float = 1.1):
        headers = {"x-api-key": api_key} if api_key else {}
        self.http = http or ResilientHttp(SOURCE, base_url, min_interval_s=min_interval_s, headers=headers)

    def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int = 300) -> SwapQuote | None:
        """Returns None when Jupiter reports no route (an informative signal: possibly unsellable)."""
        try:
            payload, meta = self.http.get_json("/swap/v1/quote", {
                "inputMint": input_mint, "outputMint": output_mint, "amount": str(amount_raw),
                "slippageBps": slippage_bps, "restrictIntermediateTokens": "true",
            })
        except SourceError as exc:
            if exc.kind in ("http_4xx", "not_found"):
                return None  # e.g. "Could not find any route" / token not tradable
            raise
        if isinstance(payload, dict) and payload.get("error"):
            return None
        return parse_quote(payload, meta.fetched_at)

    def tokens(self, mints: list[str]) -> dict[str, TokenProfile]:
        out: dict[str, TokenProfile] = {}
        for i in range(0, len(mints), 100):
            payload, meta = self.http.get_json("/tokens/v2/search", {"query": ",".join(mints[i:i + 100])},
                                               cache_ttl_s=120)
            for t in payload if isinstance(payload, list) else []:
                prof = parse_token(t, meta.fetched_at)
                out[prof.token_address] = prof
        return out


def quote_roundtrip_loss_pct(buy: SwapQuote, sell: SwapQuote) -> Decimal | None:
    """Loss of buying X SOL of token and selling the received tokens back, from two quotes.

    `sell` must be quoted for (approximately) `buy.out_amount_raw` tokens."""
    if buy.in_amount_raw <= 0 or sell.in_amount_raw <= 0:
        return None
    scale = Decimal(buy.out_amount_raw) / Decimal(sell.in_amount_raw)
    back = Decimal(sell.out_amount_raw) * scale
    return (Decimal(1) - back / Decimal(buy.in_amount_raw)) * 100
