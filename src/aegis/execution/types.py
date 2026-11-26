"""Execution domain types shared by the execution and accounting engines."""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aegis.core.money import ZERO, q_eur


class FillMethod(enum.StrEnum):
    JUPITER_QUOTE = "jupiter_quote"  # priced from an executable aggregator quote (not executed)
    CPMM_ESTIMATE = "cpmm_estimate"  # priced from pool reserves with constant-product approximation
    BURN_CLOSE = "burn_close"  # worthless tokens burned and token account closed to recover rent


@dataclass(frozen=True)
class BuyFill:
    executed_at: dt.datetime
    token_qty: Decimal
    swap_input_eur: Decimal  # EUR value of SOL sent into the swap (includes embedded pool/aggregator fees)
    network_fee_eur: Decimal
    priority_fee_eur: Decimal
    deposit_eur: Decimal  # rent deposit for a NEW token account (0 if account already exists)
    deposit_lamports: int
    reference_price_eur: Decimal | None  # indicative price at decision time
    price_impact_pct: Decimal | None
    pool_fee_eur: Decimal = ZERO  # informational: embedded in swap
    aggregator_fee_eur: Decimal = ZERO  # informational: embedded in swap
    sol_eur: Decimal = ZERO
    method: FillMethod = FillMethod.CPMM_ESTIMATE
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("swap_input_eur", "network_fee_eur", "priority_fee_eur", "deposit_eur", "pool_fee_eur",
                     "aggregator_fee_eur"):
            object.__setattr__(self, name, q_eur(getattr(self, name)))
        for name in ("token_qty", "swap_input_eur", "network_fee_eur", "priority_fee_eur", "deposit_eur"):
            v = getattr(self, name)
            if not isinstance(v, Decimal) or v < 0:
                raise ValueError(f"{name} must be a non-negative Decimal")
        if self.token_qty == 0:
            raise ValueError("buy fill with zero quantity")

    @property
    def cost_basis_eur(self) -> Decimal:
        """Acquisition value incl. inherent costs (Spanish IRPF art. 35 style). Deposit excluded."""
        return self.swap_input_eur + self.network_fee_eur + self.priority_fee_eur

    @property
    def cash_out_eur(self) -> Decimal:
        return self.cost_basis_eur + self.deposit_eur

    @property
    def effective_price_eur(self) -> Decimal:
        return self.cost_basis_eur / self.token_qty


@dataclass(frozen=True)
class SellFill:
    executed_at: dt.datetime
    token_qty: Decimal
    swap_output_eur: Decimal  # EUR value of SOL received from swap (after impact/pool/aggregator fees)
    network_fee_eur: Decimal
    priority_fee_eur: Decimal
    deposit_refund_eur: Decimal  # rent recovered when the token account is closed (full exit only)
    reference_price_eur: Decimal | None
    price_impact_pct: Decimal | None
    close_account: bool
    pool_fee_eur: Decimal = ZERO
    aggregator_fee_eur: Decimal = ZERO
    sol_eur: Decimal = ZERO
    method: FillMethod = FillMethod.CPMM_ESTIMATE
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("swap_output_eur", "network_fee_eur", "priority_fee_eur", "deposit_refund_eur",
                     "pool_fee_eur", "aggregator_fee_eur"):
            object.__setattr__(self, name, q_eur(getattr(self, name)))
        for name in ("token_qty", "swap_output_eur", "network_fee_eur", "priority_fee_eur", "deposit_refund_eur"):
            v = getattr(self, name)
            if not isinstance(v, Decimal) or v < 0:
                raise ValueError(f"{name} must be a non-negative Decimal")
        if self.token_qty == 0:
            raise ValueError("sell fill with zero quantity")

    @property
    def net_proceeds_eur(self) -> Decimal:
        """Transmission value net of sale costs. May be negative for burn/close (fees only)."""
        return self.swap_output_eur - self.network_fee_eur - self.priority_fee_eur

    @property
    def cash_in_eur(self) -> Decimal:
        return self.net_proceeds_eur + self.deposit_refund_eur


@dataclass(frozen=True)
class SwapQuote:
    """Provider-agnostic executable quote (e.g. Jupiter /swap/v1/quote). A quote is NOT a fill."""

    source: str
    input_mint: str
    output_mint: str
    in_amount_raw: int
    out_amount_raw: int
    min_out_raw: int  # otherAmountThreshold after slippage tolerance
    price_impact_pct: Decimal | None
    route_labels: tuple[str, ...]
    fetched_at: dt.datetime
    context_slot: int | None = None
    platform_fee_bps: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketContext:
    """Point-in-time market facts available to the execution engine for one token."""

    price_eur: Decimal | None
    liquidity_eur: Decimal | None
    sol_eur: Decimal
    observed_at: dt.datetime
    pool_type: str = "unknown"
