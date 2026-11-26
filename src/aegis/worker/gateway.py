"""Market-data gateway: the worker's single dependency on the outside world.

`LiveGateway` wraps the real adapters; `ScriptedGateway` serves deterministic, clearly-labelled
SIMULATED data for demos and tests (never mixed with live databases).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from aegis.config import Settings, StrategyConfig
from aegis.core.assets import WSOL_MINT
from aegis.execution.costs import TOKEN_ACCOUNT_SIZE_2022_MIN, TOKEN_ACCOUNT_SIZE_SPL
from aegis.execution.types import SwapQuote
from aegis.marketdata.dexscreener import DexScreenerClient
from aegis.marketdata.fx import CoinGeckoClient, FrankfurterClient
from aegis.marketdata.geckoterminal import GeckoTerminalClient
from aegis.marketdata.http import SourceError, SourceStats
from aegis.marketdata.jupiter import JupiterClient
from aegis.marketdata.models import FxRate, MintInfo, PoolObservation, TokenProfile
from aegis.marketdata.solana_rpc import SolanaRpcClient


class MarketDataGateway(Protocol):
    is_simulated: bool

    def discover(self) -> list[PoolObservation]: ...
    def refresh(self, pool_addresses: list[str]) -> list[PoolObservation]: ...
    def eur_usd(self) -> FxRate: ...
    def sol_eur(self) -> FxRate: ...
    def mints(self, addresses: list[str]) -> dict[str, MintInfo | None]: ...
    def profiles(self, addresses: list[str]) -> dict[str, TokenProfile]: ...
    def quote(self, input_mint: str, output_mint: str, amount_raw: int) -> SwapQuote | None: ...
    def rent_lamports(self, program: str | None) -> int | None: ...
    def health(self) -> list[SourceStats]: ...


class LiveGateway:
    is_simulated = False

    def __init__(self, settings: Settings, strategy: StrategyConfig):
        ua = settings.http_user_agent
        self.cfg = strategy
        self.gt = GeckoTerminalClient()
        self.ds = DexScreenerClient()
        self.jup = JupiterClient(api_key=settings.jupiter_api_key)
        self.rpc = SolanaRpcClient(settings.solana_rpc_url)
        self.fx = FrankfurterClient()
        self.cg = CoinGeckoClient(api_key=settings.coingecko_api_key)
        for c in (self.gt.http, self.ds.http, self.jup.http, self.rpc.http, self.fx.http, self.cg.http):
            c._client.headers["User-Agent"] = ua

    def discover(self) -> list[PoolObservation]:
        out: list[PoolObservation] = []
        d = self.cfg.discovery
        for page in range(1, d.new_pools_pages + 1):
            out += self.gt.new_pools(page)
        for page in range(1, d.trending_pages + 1):
            out += self.gt.trending_pools(page)
        return out

    def refresh(self, pool_addresses: list[str]) -> list[PoolObservation]:
        return self.gt.pools_multi(pool_addresses) if pool_addresses else []

    def eur_usd(self) -> FxRate:
        return self.fx.eur_usd()

    def sol_eur(self) -> FxRate:
        prices = self.cg.prices_eur(("solana",))
        if "SOL" not in prices:
            raise SourceError("coingecko", "parse", "SOL price missing")
        return prices["SOL"]

    def mints(self, addresses: list[str]) -> dict[str, MintInfo | None]:
        return self.rpc.get_mints(addresses)

    def profiles(self, addresses: list[str]) -> dict[str, TokenProfile]:
        return self.jup.tokens(addresses)

    def quote(self, input_mint: str, output_mint: str, amount_raw: int) -> SwapQuote | None:
        return self.jup.quote(input_mint, output_mint, amount_raw, self.cfg.execution.slippage_bps)

    def rent_lamports(self, program: str | None) -> int | None:
        size = TOKEN_ACCOUNT_SIZE_2022_MIN if program == "spl-token-2022" else TOKEN_ACCOUNT_SIZE_SPL
        try:
            return self.rpc.rent_exempt_lamports(size)
        except SourceError:
            return None

    def health(self) -> list[SourceStats]:
        return [c.stats for c in (self.gt.http, self.ds.http, self.jup.http, self.rpc.http, self.fx.http, self.cg.http)]


@dataclass
class ScriptedGateway:
    """Deterministic SIMULATED market for demos/tests. `script(t)` returns the pools visible at time t."""

    script: Callable[[dt.datetime], list[PoolObservation]]
    clock: Callable[[], dt.datetime]
    eur_usd_rate: Decimal = Decimal("1.0935")
    sol_eur_rate: Decimal = Decimal("64.17")
    mint_overrides: dict[str, MintInfo | None] = field(default_factory=dict)
    profile_overrides: dict[str, TokenProfile] = field(default_factory=dict)
    unsellable: set[str] = field(default_factory=set)
    fail_sources: set[str] = field(default_factory=set)
    decimals: int = 6
    is_simulated: bool = True

    def _check(self, src: str) -> None:
        if src in self.fail_sources:
            raise SourceError(src, "network", "simulated outage")

    def _visible(self) -> list[PoolObservation]:
        return self.script(self.clock())

    def discover(self) -> list[PoolObservation]:
        self._check("geckoterminal")
        return self._visible()

    def refresh(self, pool_addresses: list[str]) -> list[PoolObservation]:
        self._check("geckoterminal")
        want = set(pool_addresses)
        return [o for o in self._visible() if o.pool_address in want]

    def eur_usd(self) -> FxRate:
        self._check("frankfurter_ecb")
        t = self.clock()
        return FxRate("EUR", "USD", self.eur_usd_rate, "simulated", t, t)

    def sol_eur(self) -> FxRate:
        self._check("coingecko")
        t = self.clock()
        return FxRate("SOL", "EUR", self.sol_eur_rate, "simulated", t, t)

    def mints(self, addresses: list[str]) -> dict[str, MintInfo | None]:
        self._check("solana_rpc")
        t = self.clock()
        return {a: self.mint_overrides.get(a, MintInfo(a, "spl-token", self.decimals, 10**15, None, None, True, {}, t))
                for a in addresses}

    def profiles(self, addresses: list[str]) -> dict[str, TokenProfile]:
        t = self.clock()
        return {a: self.profile_overrides.get(a, TokenProfile("simulated", a, t, holders_count=800,
                                                              top10_pct=Decimal(25), mint_authority_disabled=True,
                                                              freeze_authority_disabled=True,
                                                              organic_score=Decimal(40), decimals=self.decimals))
                for a in addresses}

    def quote(self, input_mint: str, output_mint: str, amount_raw: int) -> SwapQuote | None:
        self._check("jupiter")
        token = output_mint if input_mint == WSOL_MINT else input_mint
        if token in self.unsellable and output_mint == WSOL_MINT:
            return None
        obs = next((o for o in self._visible() if o.token_address == token), None)
        if obs is None or obs.price_usd is None or not obs.liquidity_usd:
            return None
        price_sol = obs.price_usd / self.eur_usd_rate / self.sol_eur_rate
        reserve_sol = obs.liquidity_usd / 2 / self.eur_usd_rate / self.sol_eur_rate
        if input_mint == WSOL_MINT:
            sol_in = Decimal(amount_raw) / Decimal(10**9)
            out = (reserve_sol / price_sol) * sol_in * Decimal("0.9975") / (reserve_sol + sol_in)
            out_raw = int(out * Decimal(10) ** self.decimals)
        else:
            tok_in = Decimal(amount_raw) / Decimal(10) ** self.decimals
            res_tok = reserve_sol / price_sol
            out = reserve_sol * tok_in * Decimal("0.9975") / (res_tok + tok_in)
            out_raw = int(out * Decimal(10**9))
        return SwapQuote("simulated", input_mint, output_mint, amount_raw, out_raw, int(out_raw * 0.97),
                         Decimal("0.1"), ("SimAMM",), self.clock())

    def rent_lamports(self, program: str | None) -> int | None:
        return 1_488_440

    def health(self) -> list[SourceStats]:
        return []
