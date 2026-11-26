"""Normalised market-data records. Missing values are `None`, never 0. Every record carries provenance."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

WINDOWS = ("m5", "h1", "h6", "h24")


@dataclass(frozen=True)
class WindowStats:
    volume_usd: Decimal | None = None
    price_change_pct: Decimal | None = None  # percent, e.g. Decimal("56.2") = +56.2%
    buys: int | None = None
    sells: int | None = None
    buyers: int | None = None
    sellers: int | None = None


@dataclass(frozen=True)
class PoolObservation:
    source: str
    network: str
    pool_address: str
    token_address: str  # the speculative asset of interest (never SOL/USDC/USDT)
    token_side: str  # 'base' | 'quote' within the pool as reported by the source
    counter_asset: str | None
    dex: str | None
    pool_type: str  # amm | clmm | bonding_curve | unknown
    observed_at: dt.datetime  # when AEGIS received it (known-at time)
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    fdv_usd: Decimal | None
    market_cap_usd: Decimal | None
    windows: dict[str, WindowStats] = field(default_factory=dict)
    pool_created_at: dt.datetime | None = None
    token_symbol: str | None = None
    token_name: str | None = None
    token_decimals: int | None = None
    quality_flags: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None

    def w(self, window: str) -> WindowStats:
        return self.windows.get(window, WindowStats())


@dataclass(frozen=True)
class TokenProfile:
    source: str
    token_address: str
    observed_at: dt.datetime
    holders_count: int | None = None
    top10_pct: Decimal | None = None
    dev_holding_pct: Decimal | None = None
    developer_address: str | None = None
    mint_authority_disabled: bool | None = None
    freeze_authority_disabled: bool | None = None
    honeypot: str | None = None  # 'yes' | 'no' | 'unknown' as reported by the source
    circulating_supply: Decimal | None = None
    total_supply: Decimal | None = None
    market_cap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    liquidity_usd: Decimal | None = None
    organic_score: Decimal | None = None
    launchpad: str | None = None
    token_program: str | None = None
    decimals: int | None = None
    symbol: str | None = None
    name: str | None = None
    created_at: dt.datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class MintInfo:
    address: str
    program: str  # spl-token | spl-token-2022
    decimals: int
    supply_raw: int
    mint_authority: str | None
    freeze_authority: str | None
    is_initialized: bool
    extensions: dict[str, dict[str, Any]]
    observed_at: dt.datetime
    slot: int | None = None


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    source: str
    rate_time: dt.datetime
    fetched_at: dt.datetime


@dataclass(frozen=True)
class Candle:
    ts: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_usd: Decimal
