"""Case 29 — return calculations verified against hand-computed values; Spanish tax estimate."""

import datetime as dt
from decimal import Decimal

from aegis.accounting.performance import (
    ValuePoint,
    annualize,
    dca_benchmark,
    max_drawdown,
    net_portfolio_return,
    twr,
    twr_index,
    xirr,
)
from aegis.accounting.tax_es import estimate_by_year, progressive_tax
from aegis.config import TaxConfigES
from aegis.core.clock import UTC

D = Decimal
T = dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_twr_neutralises_contributions():
    # 100 -> grows 10% -> +100 contribution -> falls 10%
    pts = [
        ValuePoint(T, D(100), D(100)),
        ValuePoint(T + dt.timedelta(days=30), D(110), D(0)),
        ValuePoint(T + dt.timedelta(days=60), D("189"), D(100)),  # (110+100)*0.9
    ]
    assert twr(pts) == D("1.1") * D("0.9") - 1


def test_max_drawdown_on_index():
    pts = [ValuePoint(T, D(100), D(100)), ValuePoint(T + dt.timedelta(days=1), D(150), D(0)),
           ValuePoint(T + dt.timedelta(days=2), D(75), D(0)), ValuePoint(T + dt.timedelta(days=3), D(90), D(0))]
    assert max_drawdown(twr_index(pts)) == D("0.5")


def test_xirr_simple_annual():
    r = xirr([(T, D(-100)), (T + dt.timedelta(days=365), D(110))])
    assert abs(r - D("0.10")) < D("0.0001")


def test_xirr_extreme_asymmetric_outcome():
    r = xirr([(T, D(-100)), (T + dt.timedelta(days=365), D(100000))])
    assert abs(r - D(999)) < D("0.01")


def test_xirr_none_without_sign_change():
    assert xirr([(T, D(-1)), (T + dt.timedelta(days=1), D(-1))]) is None


def test_npr_and_annualize():
    assert net_portfolio_return(D(120), D(100), D(0)) == D("0.2")
    assert net_portfolio_return(D(0), D(0), D(0)) is None
    a = annualize(D("0.21"), T, T + dt.timedelta(days=730))
    assert abs(a - D("0.1")) < D("0.0001")


def test_dca_benchmark_no_lookahead():
    prices = [(T, D(100)), (T + dt.timedelta(days=31), D(50))]
    contribs = [(T, D(100)), (T + dt.timedelta(days=31), D(100))]
    v = dca_benchmark(contribs, prices, final_price=D(100), cost_pct=D(0))
    assert v == D(300)  # 1 unit + 2 units at 100
    assert dca_benchmark([(T - dt.timedelta(days=1), D(1))], prices, D(1)) is None


def test_spanish_brackets():
    b = TaxConfigES().brackets
    assert progressive_tax(D(6000), b) == D(1140)
    assert progressive_tax(D(10000), b) == D(1140) + D(4000) * D("0.21")
    assert progressive_tax(D(-5), b) == 0


def test_loss_carryforward_four_years():
    b = TaxConfigES().brackets
    est = estimate_by_year({2024: D(-1000), 2025: D(400), 2026: D(1000), 2030: D(1000)}, b)
    assert est[2025].taxable_base == 0 and est[2025].loss_carryforward_used == D(400)
    assert est[2026].taxable_base == D(400)
    assert est[2030].taxable_base == D(1000)
