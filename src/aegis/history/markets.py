"""Market structures and their execution models.

A constant-product formula is only valid for constant-product pools. Historical data carries the
market type of every observation and the simulator only fills orders where the structure and its
state (reserves) are known:

- CPMM (PumpSwap, Raydium CPMM): x*y=k on the reported reserves (PumpSwap may add a virtual quote
  reserve to the price curve; when the source reports it, it is used).
- BONDING_CURVE (pump.fun curve before graduation): constant product on virtual reserves; once the
  curve has completed it is NOT tradable and such rows are coverage-only.
- CLMM / DLMM / DAMM / UNKNOWN: liquidity is concentrated in ranges or bins whose state the datasets
  do not provide, so no fill is simulated (`UnsupportedMarket`) instead of an invented CPMM fill.

PumpSwap fees were measured on Launch Tape (2026-10-10 14:00-16:00 UTC, 417,347 trades): lp ~20 bps,
protocol ~5 bps and a creator fee that falls with market cap (~96 bps below 3k SOL cap, ~80 bps at
3k-10k SOL, ~71 bps above), i.e. ~1.0-1.26% per side. The previous AEGIS assumption (30 bps) was 3-4x
too low. For the Jun-Jul 2026 corpus the schedule is not observed: it is an explicit, documented
assumption with a sensitivity grid (`FeeSchedule`).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from aegis.core.money import ONE, ZERO


class MarketType(enum.StrEnum):
    BONDING_CURVE = "bonding_curve"
    CPMM = "cpmm"
    CLMM = "clmm"
    DLMM = "dlmm"
    DAMM = "damm"
    UNKNOWN = "unknown"


# dex identifiers as reported by DexScreener-style sources -> structure (only where unambiguous)
_DEX_TYPES = {
    "pumpswap": MarketType.CPMM,
    "pumpfun": MarketType.BONDING_CURVE,
    "pump-amm": MarketType.CPMM,
}


def classify_dex(dex_id: str | None) -> MarketType:
    """'meteora' and 'raydium' are ambiguous (DLMM/DAMM/DBC, AMMv4/CPMM/CLMM): UNKNOWN, never assumed CPMM."""
    return _DEX_TYPES.get((dex_id or "").lower(), MarketType.UNKNOWN)


class UnsupportedMarket(ValueError):
    """The market structure or its state is unknown: no simulated fill (never a fabricated CPMM fill)."""


@dataclass(frozen=True)
class FeeSchedule:
    """Total swap fee (bps, per side) as a function of market cap in SOL. Steps: [(cap_upper, bps)]."""

    name: str
    steps: tuple[tuple[float, int], ...]
    default_bps: int

    def bps(self, cap_sol: float | None) -> int:
        if cap_sol is None:
            return max(b for _, b in self.steps) if self.steps else self.default_bps  # unknown cap: worst tier
        for upper, b in self.steps:
            if cap_sol < upper:
                return b
        return self.default_bps


# Measured medians of (lp + protocol + creator) / quote, sells (the costlier side), Launch Tape 2026-10-10.
PUMPSWAP_2026_10 = FeeSchedule("pumpswap_measured_2026_10", ((300.0, 126), (1200.0, 121), (3000.0, 116),
                                                             (10000.0, 106)), 96)
FLAT_30 = FeeSchedule("flat_30bps_legacy_assumption", (), 30)
FLAT_125 = FeeSchedule("flat_125bps_conservative", (), 125)
FEE_SCHEDULES = {s.name: s for s in (PUMPSWAP_2026_10, FLAT_30, FLAT_125)}


@dataclass(frozen=True)
class PoolState:
    """Executable state of a pool at a known time (all amounts in human units: tokens, SOL)."""

    market_type: MarketType
    reserve_token: Decimal | None
    reserve_sol: Decimal | None  # real quote reserve
    virtual_sol: Decimal = ZERO  # extra virtual quote reserve on the price curve (PumpSwap, when reported)
    cap_sol: float | None = None

    def spot_price_sol(self) -> Decimal | None:
        if self.reserve_token is None or self.reserve_sol is None or self.reserve_token <= 0:
            return None
        return (self.reserve_sol + self.virtual_sol) / self.reserve_token


@dataclass(frozen=True)
class Fill:
    out_amount: Decimal
    fee_in_input: Decimal
    price_impact_pct: Decimal  # vs spot, excluding the fee
    fee_bps: int


def swap(state: PoolState, amount_in: Decimal, *, sol_in: bool, fees: FeeSchedule) -> Fill:
    """Exact constant-product swap on (token, sol + virtual) reserves; the fee is taken from the input.

    Raises UnsupportedMarket unless the pool is a CPMM with known reserves. A sell can never extract more
    SOL than the real reserve holds."""
    if state.market_type != MarketType.CPMM:
        raise UnsupportedMarket(f"no execution model for {state.market_type}")
    if state.reserve_token is None or state.reserve_sol is None or state.reserve_token <= 0 or state.reserve_sol <= 0:
        raise UnsupportedMarket("reserves unknown")
    if amount_in <= 0:
        return Fill(ZERO, ZERO, ZERO, 0)
    bps = fees.bps(state.cap_sol)
    fee = amount_in * Decimal(bps) / Decimal(10_000)
    net = amount_in - fee
    x, y = state.reserve_token, state.reserve_sol + state.virtual_sol
    if sol_in:
        out = x * net / (y + net)
        spot = net * x / y
    else:
        out = min(y * net / (x + net), state.reserve_sol)
        spot = net * y / x
    impact = (ONE - out / spot) * 100 if spot > 0 else Decimal(100)
    return Fill(out, fee, impact, bps)
