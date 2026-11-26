"""Spanish IRPF *estimate* for crypto disposals (base imponible del ahorro).

THIS IS AN ESTIMATE FOR PROVISIONING, NOT A TAX RETURN. Simplifications (documented in
RISK_MANAGEMENT.md / STRATEGY_SPECIFICATION.md):
- Gains/losses per disposal = net transmission value - acquisition value (FIFO lots, costs included).
- Net losses of a year offset future net gains for up to 4 years (art. 49 LIRPF). The 25% cross-offset
  against the general base (rendimientos) is ignored (conservative).
- In real execution every token<->SOL swap is a taxable permuta, including the SOL leg. The
  simulation keeps EUR as unit of account and does not model SOL FX gains (limitation).
- The two-month homogeneous-asset rule (art. 33.5) is NOT applied (its application to crypto is
  uncertain); flagged for professional review.
- Savings base aggregates all of the taxpayer's other savings income, unknown here; we assume AEGIS
  gains are the only savings income (may UNDER-estimate marginal rate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from aegis.core.money import ZERO

Bracket = tuple[Decimal | None, Decimal]


def progressive_tax(base: Decimal, brackets: Sequence[Bracket]) -> Decimal:
    if base <= ZERO:
        return ZERO
    tax = ZERO
    lower = ZERO
    for upper, rate in brackets:
        if upper is None or base <= upper:
            tax += (base - lower) * rate
            return tax
        tax += (upper - lower) * rate
        lower = upper
    return tax


@dataclass(frozen=True)
class YearEstimate:
    year: int
    net_gain: Decimal
    loss_carryforward_used: Decimal
    taxable_base: Decimal
    estimated_tax: Decimal
    carryforward_remaining: Decimal


def estimate_by_year(net_by_year: Mapping[int, Decimal], brackets: Sequence[Bracket]) -> dict[int, YearEstimate]:
    """Apply 4-year loss carryforward chronologically."""
    pending: list[tuple[int, Decimal]] = []  # (origin year, remaining loss)
    out: dict[int, YearEstimate] = {}
    for year in sorted(net_by_year):
        pending = [(y, loss) for (y, loss) in pending if year - y <= 4 and loss > ZERO]
        net = net_by_year[year]
        used = ZERO
        if net > ZERO:
            remaining_gain = net
            new_pending = []
            for y, loss in pending:
                take = min(loss, remaining_gain)
                remaining_gain -= take
                used += take
                new_pending.append((y, loss - take))
            pending = new_pending
            base = net - used
        else:
            base = ZERO
            if net < ZERO:
                pending.append((year, -net))
        out[year] = YearEstimate(
            year=year,
            net_gain=net,
            loss_carryforward_used=used,
            taxable_base=base,
            estimated_tax=progressive_tax(base, brackets),
            carryforward_remaining=sum((loss for _, loss in pending if loss > ZERO), ZERO),
        )
    return out
