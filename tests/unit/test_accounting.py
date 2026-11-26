"""Portfolio accounting: double entry, FIFO, reserves, reconciliation, valuation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aegis.accounting.ledger import Account, InsufficientFunds, Ledger, Posting, UnbalancedTransaction
from aegis.accounting.portfolio import PortfolioService, PositionStatus, PositionValuation
from aegis.config import StrategyConfig
from aegis.storage.db import create_all, make_engine
from tests.conftest import T0, buy_fill, new_execution, new_token, sell_fill

D = Decimal


def _buy(svc, conn, token, t=T0, key="b1", **kw):
    ex = new_execution(conn, token, "BUY", t, key)
    fill = buy_fill(t=t, **kw)
    funding = [(Account.CASH_INVESTMENT, fill.cash_out_eur)]
    return svc.record_buy(conn, token_id=token, bucket="exploration", execution_id=ex, fill=fill,
                          funding=funding, idempotency_key=key)


def _sell(svc, conn, pos_id, token, t, key, **kw):
    ex = new_execution(conn, token, "SELL", t, key)
    return svc.record_sell(conn, position_id=pos_id, execution_id=ex, fill=sell_fill(t, **kw), idempotency_key=key)


# Case 1 — monthly contribution without duplication
def test_monthly_contribution_idempotent(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        assert svc.contribute_month(c, T0)
        assert not svc.contribute_month(c, T0 + dt.timedelta(days=3))
        assert svc.ledger.balance(c, Account.CASH_INVESTMENT) == D(100)
        assert svc.contribute_month(c, T0 + dt.timedelta(days=35))
        assert svc.ledger.balance(c, Account.EQUITY_CONTRIBUTIONS) == D(-200)
        assert svc.reconcile(c).ok


def test_ledger_rejects_unbalanced_and_overdraft(engine):
    led = Ledger()
    with engine.begin() as c:
        with pytest.raises(UnbalancedTransaction):
            led.post(c, occurred_at=T0, recorded_at=T0, kind="x", description="x", idempotency_key="k1",
                     postings=[Posting(Account.CASH_INVESTMENT, D(1)), Posting(Account.EQUITY_CONTRIBUTIONS, D(-2))])
        with pytest.raises(InsufficientFunds):
            led.post(c, occurred_at=T0, recorded_at=T0, kind="x", description="x", idempotency_key="k2",
                     postings=[Posting(Account.POSITIONS, D(1)), Posting(Account.CASH_INVESTMENT, D(-1))])


def test_ledger_idempotency_key_prevents_double_posting(engine):
    led = Ledger()
    with engine.begin() as c:
        p = [Posting(Account.CASH_INVESTMENT, D(5)), Posting(Account.EQUITY_CONTRIBUTIONS, D(-5))]
        a = led.post(c, occurred_at=T0, recorded_at=T0, kind="c", description="c", postings=p, idempotency_key="same")
        b = led.post(c, occurred_at=T0, recorded_at=T0, kind="c", description="c", postings=p, idempotency_key="same")
        assert a.created and not b.created and a.id == b.id
        assert led.balance(c, Account.CASH_INVESTMENT) == D(5)


def test_buy_capitalises_costs_and_books_deposit(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tok = new_token(c)
        pos = _buy(svc, c, tok)
        bal = svc.ledger.balances(c)
        assert bal[Account.CASH_INVESTMENT] == D(100) - D("0.9823")
        assert bal[Account.POSITIONS] == D("0.8868")  # 0.88 swap + 0.0004 base + 0.0064 priority
        assert bal[Account.DEPOSITS] == D("0.0955")
        row = svc.open_position(c, tok)
        assert row.id == pos and row.status == PositionStatus.OPEN
        assert svc.reconcile(c).ok


# Cases 4 & 5 — partial and full sale; FIFO across two lots; deposit recovered on close
def test_partial_then_full_sale_fifo(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tok = new_token(c)
        pos = _buy(svc, c, tok, key="b1", qty="1000", swap="0.88")
        _buy(svc, c, tok, t=T0 + dt.timedelta(hours=1), key="b2", qty="500", swap="0.88", dep="0")  # INCREASE
        r1 = _sell(svc, c, pos, tok, T0 + dt.timedelta(hours=2), "s1", qty="1200", out="3.00")
        # FIFO: 1000 from lot1 (cost .8868) + 200 of 500 from lot2 (cost .8868*0.4=.35472)
        assert r1.cost_basis_sold == D("0.8868") + D("0.35472")
        assert r1.net_proceeds == D("3.00") - D("0.0068")
        assert r1.realized_gain == r1.net_proceeds - r1.cost_basis_sold
        assert not r1.closed
        assert svc.reconcile(c).ok
        r2 = _sell(svc, c, pos, tok, T0 + dt.timedelta(hours=3), "s2", qty="300", out="0.50",
                   refund="0.0955", close=True)
        assert r2.closed and r2.deposit_refund == D("0.0955")
        bal = svc.ledger.balances(c)
        assert bal[Account.POSITIONS] == 0 and bal[Account.DEPOSITS] == 0
        assert svc.reconcile(c).ok
        assert svc.open_position(c, tok) is None


# Case 6 — reinvestment of recovered capital, without double counting contributions
def test_recycled_capital_funds_new_buys_no_double_count(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tok = new_token(c)
        pos = _buy(svc, c, tok)
        _sell(svc, c, pos, tok, T0 + dt.timedelta(hours=1), "s1", qty="1000", out="5.0", refund="0.0955", close=True)
        recycled = svc.ledger.balance(c, Account.CASH_RECYCLED)
        gain = D("4.9932") - D("0.8868")  # net proceeds - cost basis
        assert recycled == D("5.0887") - gain * D("0.10") - gain * D("0.19")  # minus profit & tax reserves
        assert svc.ledger.balance(c, Account.CASH_RESERVE_PROFIT) == gain * D("0.10")
        tok2 = new_token(c)
        ex = new_execution(c, tok2, "BUY", T0 + dt.timedelta(hours=2), "b2")
        fill = buy_fill(t=T0 + dt.timedelta(hours=2))
        svc.record_buy(c, token_id=tok2, bucket="selection", execution_id=ex, fill=fill,
                       funding=[(Account.CASH_RECYCLED, fill.cash_out_eur)], idempotency_key="b2")
        st_ = svc.state(c, T0 + dt.timedelta(hours=2), {})
        assert st_.contributions_total == D(100)  # recycling never inflates contributions
        assert st_.reconciliation.ok


# Case 28 — tax reserve protected from reinvestment and withdrawals
def test_tax_reserve_not_spendable(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tok = new_token(c)
        pos = _buy(svc, c, tok)
        _sell(svc, c, pos, tok, T0 + dt.timedelta(hours=1), "s1", qty="1000", out="50", refund="0.0955", close=True)
        tax_res = svc.ledger.balance(c, Account.CASH_RESERVE_TAX)
        assert tax_res > 0
        gain = svc.realized_net_by_year(c)[2026]
        assert tax_res == gain * D("0.19")
        assert Account.CASH_RESERVE_TAX not in svc.spendable(c)
        with pytest.raises(InsufficientFunds):
            svc.withdraw(c, T0, D(1), "w1", source=Account.CASH_RESERVE_TAX)


def test_tax_reserve_released_after_later_losses(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        a, b = new_token(c), new_token(c)
        pa = _buy(svc, c, a, key="ba")
        pb = _buy(svc, c, b, key="bb")
        _sell(svc, c, pa, a, T0 + dt.timedelta(hours=1), "sa", qty="1000", out="10", refund="0.0955", close=True)
        high = svc.ledger.balance(c, Account.CASH_RESERVE_TAX)
        _sell(svc, c, pb, b, T0 + dt.timedelta(hours=2), "sb", qty="1000", out="0.01", refund="0.0955", close=True)
        low = svc.ledger.balance(c, Account.CASH_RESERVE_TAX)
        assert low < high
        assert low == svc.realized_net_by_year(c)[2026] * D("0.19")
        assert svc.reconcile(c).ok


# Case 16 — illiquid positions valued at liquidation value, flagged, not at mark
def test_state_uses_liquidation_value_and_counts_unvalued(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        t1, t2 = new_token(c), new_token(c)
        p1 = _buy(svc, c, t1, key="b1")
        _buy(svc, c, t2, key="b2")
        vals = {p1: PositionValuation(p1, mark_eur=D("10"), liquidation_eur=D("2"), method="cpmm_estimate",
                                      observed_at=T0)}
        st_ = svc.state(c, T0, vals)
        assert st_.positions_mark == D(10) and st_.positions_liquidation == D(2)
        assert st_.unvalued_positions == 1
        expected = st_.cash_total + st_.deposits + D(2) - 0
        assert st_.net_worth_liquidable == expected
        assert st_.objective == st_.net_worth_liquidable - D(100)


def test_cannot_oversell_or_refund_wrong_deposit(engine, cfg):
    svc = PortfolioService(cfg)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tok = new_token(c)
        pos = _buy(svc, c, tok)
        with pytest.raises(ValueError):
            _sell(svc, c, pos, tok, T0, "s1", qty="1001", out="1")
        with pytest.raises(ValueError):
            _sell(svc, c, pos, tok, T0, "s2", qty="1000", out="1", refund="0.5", close=True)
        with pytest.raises(ValueError):
            _sell(svc, c, pos, tok, T0, "s3", qty="1000", out="1")  # full exit must close account


# Property-based: arbitrary sequences never create money and always reconcile.
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.tuples(st.sampled_from(["buy", "sell_part", "sell_all"]),
                          st.decimals(min_value="0", max_value="40", places=4)), min_size=1, max_size=25))
def test_property_reconciliation_holds(ops):
    eng = make_engine("sqlite://")
    create_all(eng)
    svc = PortfolioService(StrategyConfig())
    with eng.begin() as c:
        svc.contribute_month(c, T0)
        tokens: list[int] = []
        for i, (op, mult) in enumerate(ops):
            t = T0 + dt.timedelta(minutes=i + 1)
            open_ = [p for p in svc.active_positions(c)]
            if op == "buy" or not open_:
                tok = new_token(c)
                try:
                    _buy(svc, c, tok, t=t, key=f"b{i}")
                    tokens.append(tok)
                except InsufficientFunds:
                    pass
            else:
                p = open_[i % len(open_)]
                cost = p.cost_basis_eur
                if op == "sell_all":
                    out = (cost * mult).quantize(D("1e-12"))
                    _sell(svc, c, p.id, p.token_id, t, f"s{i}", qty=str(p.qty), out=str(out),
                          refund=str(p.deposit_eur), close=True)
                else:
                    q = (p.qty / 3).quantize(D("1e-6"), rounding="ROUND_DOWN")
                    out = (cost / 3 * mult).quantize(D("1e-12"))
                    if out >= D("0.0068"):
                        _sell(svc, c, p.id, p.token_id, t, f"s{i}", qty=str(q), out=str(out))
            rep = svc.reconcile(c)
            assert rep.ok, rep.checks
        st_ = svc.state(c, T0 + dt.timedelta(days=1), {})
        # Without valuations, NW = cash + deposits - unpaid tax; never above what bookkeeping allows
        assert st_.net_worth_liquidable <= st_.contributions_total + max(D(0), st_.realized_pnl_total)
