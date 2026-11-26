"""Reference rates: EUR/USD (ECB via Frankfurter) and SOL/BTC prices (CoinGecko simple/price).

ECB rates are daily (~16:00 CET, business days). SOL/EUR from CoinGecko is used only to convert
network costs (lamports) and SOL-denominated quotes into EUR; both are stored with source and time.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from aegis.core.clock import UTC
from aegis.core.money import parse_optional
from aegis.marketdata.http import ResilientHttp, SourceError
from aegis.marketdata.models import FxRate


class FrankfurterClient:
    SOURCE = "frankfurter_ecb"

    def __init__(self, http: ResilientHttp | None = None):
        self.http = http or ResilientHttp(self.SOURCE, "https://api.frankfurter.dev", min_interval_s=1.0)

    def eur_usd(self) -> FxRate:
        payload, meta = self.http.get_json("/v1/latest", {"base": "EUR", "symbols": "USD"}, cache_ttl_s=3600)
        rate = parse_optional((payload.get("rates") or {}).get("USD"))
        date = payload.get("date")
        if rate is None or rate <= 0 or not date:
            raise SourceError(self.SOURCE, "parse", f"bad FX payload: {payload}")
        # ECB reference fixing is published ~16:00 CET; we timestamp at 15:00 UTC of the stated date.
        rt = dt.datetime.fromisoformat(date).replace(hour=15, tzinfo=UTC)
        return FxRate("EUR", "USD", rate, self.SOURCE, rt, meta.fetched_at)


class CoinGeckoClient:
    SOURCE = "coingecko"

    def __init__(self, http: ResilientHttp | None = None, api_key: str = ""):
        headers = {"x-cg-demo-api-key": api_key} if api_key else {}
        self.http = http or ResilientHttp(self.SOURCE, "https://api.coingecko.com/api/v3", min_interval_s=3.0,
                                          headers=headers, backoff_base_s=10.0)

    def prices_eur(self, ids: tuple[str, ...] = ("solana", "bitcoin")) -> dict[str, FxRate]:
        payload, meta = self.http.get_json("/simple/price", {
            "ids": ",".join(ids), "vs_currencies": "eur,usd", "include_last_updated_at": "true",
        }, cache_ttl_s=60)
        out: dict[str, FxRate] = {}
        symbols = {"solana": "SOL", "bitcoin": "BTC"}
        for cid in ids:
            row = payload.get(cid) or {}
            eur = parse_optional(row.get("eur"))
            ts = row.get("last_updated_at")
            if eur is None or eur <= 0:
                continue
            rt = dt.datetime.fromtimestamp(int(ts), UTC) if ts else meta.fetched_at
            out[symbols.get(cid, cid.upper())] = FxRate(symbols.get(cid, cid.upper()), "EUR", eur, self.SOURCE, rt,
                                                        meta.fetched_at)
        return out


def usd_to_eur(usd: Decimal | None, eur_usd: Decimal) -> Decimal | None:
    """Convert with EUR/USD quoted as USD per 1 EUR."""
    if usd is None:
        return None
    return usd / eur_usd
