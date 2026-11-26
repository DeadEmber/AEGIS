"""HistoricalGateway: serves a HistoricalStore to the UNCHANGED production worker (`Worker.replay`).

Point-in-time by construction: every call answers with what was observable at `clock()` — the latest
observation at or before now (with its true `observed_at`, so staleness is visible to the worker), the
ECB rate already published, SOL/USD from the same observations. Fills are quoted with the exact
constant-product state of CPMM pools; any other structure returns no quote (no fabricated fill).

Security evidence: the datasets do not contain historical mint accounts. By default `mints()` answers
None (unknown) and the production security engine blocks purchases (unknown is never safe). With
`launchpad_invariants=True` the gateway serves the pump.fun program invariants for STANDARD pump.fun
mints (classic SPL token, mint and freeze authority revoked at creation) — an assumption, so the
gateway sets `evidence_ceiling=INDICATOR` and the security engine labels every finding accordingly.
Mayhem-mode mints (different supply, token program not recorded) stay unknown.

The database written by a historical replay is tagged `data_mode=historical`: never mixed with live or
simulated databases.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from aegis.core.assets import WSOL_MINT
from aegis.execution.types import SwapQuote
from aegis.history.fx import PUBLICATION_HOUR_UTC, EcbTable
from aegis.history.markets import FEE_SCHEDULES, PUMPSWAP_2026_10, UnsupportedMarket, swap
from aegis.history.store import HistoricalStore, Obs, TokenTrack, to_pool_observation
from aegis.marketdata.http import SourceError, SourceStats
from aegis.marketdata.models import FxRate, MintInfo, PoolObservation, TokenProfile


class HistoricalGateway:
    is_simulated = True  # not live market access
    data_mode = "historical"

    def __init__(self, store: HistoricalStore, clock: Callable[[], dt.datetime], *, fx: EcbTable | None = None,
                 launchpad_invariants: bool = False, fee_schedule: str = PUMPSWAP_2026_10.name,
                 discover_window_s: int = 6 * 3600, max_discovered: int = 300, rent_lamports: int = 1_488_440):
        self.store = store
        self.clock = clock
        self.fx = fx or EcbTable.bundled()
        self.launchpad_invariants = launchpad_invariants
        self.evidence_ceiling = "INDICATOR" if launchpad_invariants else None
        self.fees = FEE_SCHEDULES[fee_schedule]
        self.discover_window_s = discover_window_s
        self.max_discovered = max_discovered
        self.rent = rent_lamports
        prof = store.resolution_profile
        self.data_resolution_seconds = int(prof[0][1]) if prof else None
        self._by_pool = {tr.pool: tr for tr in store.tokens.values() if tr.pool}
        self._sol_usd: float | None = None

    # ------------------------------------------------------------------ helpers
    def _now(self) -> float:
        return self.clock().timestamp()

    def _latest(self, tr: TokenTrack, now: float) -> Obs | None:
        o = tr.last(now)
        if o is not None and o.sol_usd:
            self._sol_usd = o.sol_usd
        return o

    def _pobs(self, tr: TokenTrack, o: Obs) -> PoolObservation:
        return to_pool_observation(tr, o, f"historical:{self.store.dataset}")

    # ------------------------------------------------------------------ MarketDataGateway
    def discover(self) -> list[PoolObservation]:
        """Graduations of the last `discover_window_s` with a fresh observation (new-pools emulation)."""
        now = self._now()
        out = []
        recent = sorted((tr for tr in self.store.tokens.values()
                         if tr.graduated_at is not None and now - self.discover_window_s <= tr.graduated_at <= now),
                        key=lambda tr: -(tr.graduated_at or 0))[: self.max_discovered]
        for tr in recent:
            o = self._latest(tr, now)
            if o is not None and now - o.t <= 600:
                out.append(self._pobs(tr, o))
        return out

    def refresh(self, pool_addresses: list[str]) -> list[PoolObservation]:
        now = self._now()
        out = []
        for p in pool_addresses:
            tr = self._by_pool.get(p)
            o = self._latest(tr, now) if tr else None
            if tr is not None and o is not None:
                out.append(self._pobs(tr, o))
        return out

    def refresh_tokens(self, token_addresses: list[str]) -> list[PoolObservation]:
        now = self._now()
        out = []
        for a in token_addresses:
            tr = self.store.tokens.get(a)
            o = self._latest(tr, now) if tr else None
            if tr is not None and o is not None:
                out.append(self._pobs(tr, o))
        return out

    def eur_usd(self) -> FxRate:
        now = self._now()
        r = self.fx.eur_usd(now)
        if r is None:
            raise SourceError("frankfurter_ecb", "missing", "no ECB rate published yet for this instant")
        rate, day = r
        published = dt.datetime(day.year, day.month, day.day, PUBLICATION_HOUR_UTC, tzinfo=dt.UTC)
        return FxRate("EUR", "USD", rate, "ecb_historical", published, self.clock())

    def sol_eur(self) -> FxRate:
        fx = self.eur_usd()
        if self._sol_usd is None:
            for tr in self.store.tokens.values():
                o = tr.last(self._now())
                if o is not None and o.sol_usd:
                    self._sol_usd = o.sol_usd
                    break
        if self._sol_usd is None:
            raise SourceError("coingecko", "missing", "no SOL/USD observed yet")
        t = self.clock()
        return FxRate("SOL", "EUR", Decimal(repr(self._sol_usd)) / fx.rate, "dataset_price_native", t, t)

    def mints(self, addresses: list[str]) -> dict[str, MintInfo | None]:
        out: dict[str, MintInfo | None] = {}
        for a in addresses:
            tr = self.store.tokens.get(a)
            standard = tr is not None and tr.supply == 1_000_000_000.0 and not tr.static.get("is_mayhem_mode", {}).get("value")
            if self.launchpad_invariants and standard and tr is not None:
                out[a] = MintInfo(a, "spl-token", tr.decimals, int(tr.supply * 10**tr.decimals), None, None, True, {},
                                  self.clock())
            else:
                out[a] = None
        return out

    def profiles(self, addresses: list[str]) -> dict[str, TokenProfile]:
        now = self._now()
        out = {}
        for a in addresses:
            tr = self.store.tokens.get(a)
            if tr is None:
                continue
            st = tr.static_at(now)
            top10 = st.get("initial_top10_pct_corrected")
            out[a] = TokenProfile(f"historical:{self.store.dataset}", a, self.clock(),
                                  top10_pct=None if top10 is None else Decimal(repr(float(top10))),
                                  dev_holding_pct=None, decimals=tr.decimals, symbol=tr.symbol, name=tr.name,
                                  launchpad="pump.fun", extra={"note": "launch-time concentration (stale at graduation)"})
        return out

    def quote(self, input_mint: str, output_mint: str, amount_raw: int) -> SwapQuote | None:
        now = self._now()
        token = output_mint if input_mint == WSOL_MINT else input_mint
        tr = self.store.tokens.get(token)
        o = self._latest(tr, now) if tr else None
        if tr is None or o is None:
            return None
        sol_in = input_mint == WSOL_MINT
        amt = Decimal(amount_raw) / (Decimal(10**9) if sol_in else Decimal(10) ** tr.decimals)
        try:
            f = swap(o.pool_state(tr.supply), amt, sol_in=sol_in, fees=self.fees)
        except UnsupportedMarket:
            return None
        out_raw = int(f.out_amount * (Decimal(10) ** tr.decimals if sol_in else Decimal(10**9)))
        return SwapQuote(f"historical:{self.store.dataset}", input_mint, output_mint, amount_raw, out_raw, out_raw,
                         f.price_impact_pct, ("historical-cpmm",), self.clock())

    def rent_lamports(self, program: str | None) -> int | None:
        return self.rent

    def health(self) -> list[SourceStats]:
        return []
