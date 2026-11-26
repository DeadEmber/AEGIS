"""Solana JSON-RPC adapter (mint accounts, rent, largest holders).

Public endpoint https://api.mainnet-beta.solana.com is heavily rate-limited per method (observed
429 on getTokenLargestAccounts). For production volumes use a provider (Helius etc.) via
AEGIS_SOLANA_RPC_URL — that is a paid decision for the owner.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aegis.core.assets import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from aegis.marketdata.http import ResilientHttp, SourceError
from aegis.marketdata.models import MintInfo

SOURCE = "solana_rpc"


def parse_mint(address: str, value: dict[str, Any] | None, observed_at: dt.datetime, slot: int | None) -> MintInfo | None:
    if not value:
        return None
    data = value.get("data")
    if not isinstance(data, dict) or "parsed" not in data:
        return None
    parsed = data["parsed"]
    if parsed.get("type") != "mint":
        return None
    info = parsed.get("info") or {}
    exts = {e.get("extension"): (e.get("state") or {}) for e in info.get("extensions") or [] if isinstance(e, dict)}
    owner = value.get("owner")
    program = "spl-token-2022" if owner == TOKEN_2022_PROGRAM else ("spl-token" if owner == TOKEN_PROGRAM else str(owner))
    return MintInfo(
        address=address, program=program, decimals=int(info.get("decimals", 0)), supply_raw=int(info.get("supply", 0)),
        mint_authority=info.get("mintAuthority"), freeze_authority=info.get("freezeAuthority"),
        is_initialized=bool(info.get("isInitialized")), extensions=exts, observed_at=observed_at, slot=slot,
    )


class SolanaRpcClient:
    def __init__(self, url: str = "https://api.mainnet-beta.solana.com", http: ResilientHttp | None = None,
                 min_interval_s: float = 0.25):
        self.http = http or ResilientHttp(SOURCE, url, min_interval_s=min_interval_s, backoff_base_s=2.0)
        self._id = 0
        self._rent: dict[int, int] = {}

    def call(self, method: str, params: list[Any], cache_ttl_s: float = 0) -> tuple[Any, dt.datetime]:
        self._id += 1

        def validate(p: Any) -> None:
            if isinstance(p, dict) and p.get("error"):
                err = p["error"]
                code = err.get("code") if isinstance(err, dict) else None
                kind = "rpc_rate_limited" if code == 429 else "rpc_error"
                raise SourceError(SOURCE, kind, f"{method}: {err}")

        payload, meta = self.http.post_json("", {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
                                            validate=validate, cache_ttl_s=cache_ttl_s)
        return payload.get("result"), meta.fetched_at

    def get_mints(self, addresses: list[str]) -> dict[str, MintInfo | None]:
        out: dict[str, MintInfo | None] = {}
        for i in range(0, len(addresses), 100):
            chunk = addresses[i:i + 100]
            res, at = self.call("getMultipleAccounts", [chunk, {"encoding": "jsonParsed"}])
            slot = (res or {}).get("context", {}).get("slot")
            for addr, val in zip(chunk, (res or {}).get("value") or [None] * len(chunk), strict=False):
                out[addr] = parse_mint(addr, val, at, slot)
        return out

    def rent_exempt_lamports(self, size: int) -> int:
        if size not in self._rent:
            res, _ = self.call("getMinimumBalanceForRentExemption", [size], cache_ttl_s=3600)
            self._rent[size] = int(res)
        return self._rent[size]

    def largest_accounts(self, mint: str) -> list[dict[str, Any]] | None:
        try:
            res, _ = self.call("getTokenLargestAccounts", [mint], cache_ttl_s=600)
        except SourceError as exc:
            if exc.kind in ("rpc_rate_limited", "rpc_error"):
                return None  # unknown, NOT safe
            raise
        return (res or {}).get("value")
