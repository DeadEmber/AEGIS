"""Solana execution cost model.

Cost components for a swap (all converted to EUR at the SOL/EUR reference rate):
- base signature fee (5000 lamports/signature, protocol constant at time of writing),
- priority fee (configurable; real landing during congestion can cost far more),
- token-account rent deposit (rent-exempt minimum for the account size; RECOVERABLE on close),
- pool/LP fee and aggregator fee (embedded in quotes; estimated when no quote is available),
- price impact (from quote, or constant-product approximation).

No component is assumed negligible: for a 1 EUR ticket the deposit alone is ~10% (2026-11-26:
1,488,440 lamports for 165 bytes at 64.17 EUR/SOL ~= 0.0955 EUR).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aegis.config import ExecutionConfig
from aegis.core.money import ONE, ZERO, lamports_to_sol, q_eur

TOKEN_ACCOUNT_SIZE_SPL = 165
TOKEN_ACCOUNT_SIZE_2022_MIN = 170  # 165 + account type + ImmutableOwner extension (conservative)


@dataclass(frozen=True)
class NetworkCostsEUR:
    base_fee: Decimal
    priority_fee: Decimal
    deposit: Decimal
    deposit_lamports: int

    @property
    def unrecoverable(self) -> Decimal:
        return self.base_fee + self.priority_fee


@dataclass(frozen=True)
class CpmmResult:
    out_amount: Decimal
    price_impact_pct: Decimal  # vs. spot, excluding the pool fee
    pool_fee: Decimal  # in input units


class CostModel:
    def __init__(self, cfg: ExecutionConfig):
        self.cfg = cfg

    def lamports_eur(self, lamports: int, sol_eur: Decimal) -> Decimal:
        return q_eur(lamports_to_sol(lamports) * sol_eur)

    def network_costs(self, sol_eur: Decimal, *, new_account: bool, rent_lamports: int | None) -> NetworkCostsEUR:
        rent = (rent_lamports if rent_lamports is not None else self.cfg.fallback_ata_rent_lamports) if new_account else 0
        return NetworkCostsEUR(
            base_fee=self.lamports_eur(self.cfg.base_fee_lamports, sol_eur),
            priority_fee=self.lamports_eur(self.cfg.priority_fee_lamports, sol_eur),
            deposit=self.lamports_eur(rent, sol_eur),
            deposit_lamports=rent,
        )

    def close_account_cost(self, sol_eur: Decimal) -> Decimal:
        # Burn + CloseAccount in one transaction: one signature, no priority needed (not time critical).
        return self.lamports_eur(self.cfg.base_fee_lamports, sol_eur)

    @staticmethod
    def cpmm_swap(amount_in: Decimal, reserve_in: Decimal, reserve_out: Decimal, fee_bps: int) -> CpmmResult:
        """Constant-product x*y=k swap. Valid ONLY for CPMM pools; an approximation for CLMM/DLMM."""
        if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
            return CpmmResult(ZERO, ONE * 100, ZERO)
        fee = amount_in * Decimal(fee_bps) / Decimal(10_000)
        net_in = amount_in - fee
        out = reserve_out * net_in / (reserve_in + net_in)
        spot_out = net_in * reserve_out / reserve_in
        impact = (ONE - out / spot_out) * 100 if spot_out > 0 else ONE * 100
        return CpmmResult(out, impact, fee)

    def estimate_buy(self, swap_input_eur: Decimal, price_eur: Decimal, liquidity_eur: Decimal) -> tuple[Decimal, CpmmResult]:
        """Returns (token_qty, cpmm) assuming liquidity split 50/50 between token and quote sides."""
        half = liquidity_eur / 2
        fee_bps = self.cfg.estimated_pool_fee_bps + self.cfg.aggregator_fee_bps
        res = self.cpmm_swap(swap_input_eur, half, half / price_eur if price_eur > 0 else ZERO, fee_bps)
        return res.out_amount, res

    def estimate_sell(self, qty: Decimal, price_eur: Decimal, liquidity_eur: Decimal) -> CpmmResult:
        half = liquidity_eur / 2
        fee_bps = self.cfg.estimated_pool_fee_bps + self.cfg.aggregator_fee_bps
        reserve_token = half / price_eur if price_eur > 0 else ZERO
        res = self.cpmm_swap(qty, reserve_token, half, fee_bps)
        # convert pool fee (token units) into EUR at spot for reporting
        return CpmmResult(res.out_amount, res.price_impact_pct, res.pool_fee * price_eur)

    def roundtrip_cost_pct(self, ticket_eur: Decimal, price_eur: Decimal, liquidity_eur: Decimal, sol_eur: Decimal,
                           rent_lamports: int | None) -> Decimal:
        """Unrecoverable cost of buying and immediately selling a ticket, as % of the ticket.

        Excludes the deposit (recoverable) but includes both sides' fees and impact."""
        net = self.network_costs(sol_eur, new_account=True, rent_lamports=rent_lamports)
        swap_in = ticket_eur - net.deposit - net.unrecoverable
        if swap_in <= 0:
            return Decimal(100)
        qty, _ = self.estimate_buy(swap_in, price_eur, liquidity_eur)
        back = self.estimate_sell(qty, price_eur, liquidity_eur).out_amount
        sell_fees = net.unrecoverable + self.close_account_cost(sol_eur)
        recovered = back - sell_fees + net.deposit
        return (ticket_eur - recovered) / ticket_eur * 100
