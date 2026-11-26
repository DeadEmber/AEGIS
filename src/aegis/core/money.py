"""Exact decimal arithmetic for money and token quantities.

Rules:
- All monetary amounts are `Decimal`. Floats are rejected at the boundary (`D()` refuses float unless
  explicitly allowed for parsing third-party JSON numbers, where we go through `str()` first).
- EUR amounts are stored with full precision and quantized only for presentation (`eur_str`).
- Lamports are integers (1 SOL = 1_000_000_000 lamports).
"""

from __future__ import annotations

import decimal
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, InvalidOperation

# 40 significant digits: enough for 1e12 token units at 1e-12 EUR prices without loss.
CTX = decimal.Context(prec=40, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, decimal.DivisionByZero])
decimal.setcontext(CTX)

ZERO = Decimal(0)
ONE = Decimal(1)
LAMPORTS_PER_SOL = 1_000_000_000
EUR_CENT = Decimal("0.01")
EUR_DISPLAY = Decimal("0.0001")
# Ledger scale: 1e-12 EUR. All booked amounts are quantized to this scale so that sums are EXACT
# (no 40-digit context rounding when mixing magnitudes) and every transaction balances to zero.
EUR_SCALE = Decimal("1e-12")
QTY_SCALE_DEFAULT = Decimal("1e-12")


class MoneyError(ValueError):
    pass


def D(value: object, *, allow_float: bool = False) -> Decimal:
    """Convert to Decimal. Floats only allowed when explicitly requested (parsing external JSON)."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MoneyError(f"non-finite decimal: {value}")
        return value
    if isinstance(value, bool):
        raise MoneyError("bool is not a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not allow_float:
            raise MoneyError("float passed where exact Decimal required; use str or Decimal")
        out = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            out = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"invalid decimal string: {value!r}") from exc
    else:
        raise MoneyError(f"unsupported type for Decimal: {type(value).__name__}")
    if not out.is_finite():
        raise MoneyError(f"non-finite decimal: {value!r}")
    return out


def parse_optional(value: object) -> Decimal | None:
    """Parse a third-party numeric field. Missing/invalid -> None (never silently 0)."""
    if value is None or value == "":
        return None
    try:
        return D(value, allow_float=True)
    except MoneyError:
        return None


def safe_div(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def lamports_to_sol(lamports: int) -> Decimal:
    return Decimal(lamports) / Decimal(LAMPORTS_PER_SOL)


def sol_to_lamports(sol: Decimal) -> int:
    return int((sol * LAMPORTS_PER_SOL).to_integral_value(rounding=ROUND_DOWN))


def to_raw_units(ui_amount: Decimal, decimals: int) -> int:
    return int((ui_amount * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN))


def from_raw_units(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


def q_eur(x: Decimal) -> Decimal:
    return x.quantize(EUR_SCALE, rounding=ROUND_HALF_EVEN)


def q_qty(x: Decimal, decimals: int | None = None) -> Decimal:
    """Token quantities are integers of raw on-chain units: round DOWN to the token's decimals."""
    scale = QTY_SCALE_DEFAULT if decimals is None else Decimal(1).scaleb(-decimals)
    return x.quantize(scale, rounding=ROUND_DOWN)


def is_eur_quantized(x: Decimal) -> bool:
    return x == q_eur(x)


def eur_str(amount: Decimal, places: Decimal = EUR_DISPLAY) -> str:
    return str(amount.quantize(places, rounding=ROUND_HALF_EVEN))


def clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, x))
