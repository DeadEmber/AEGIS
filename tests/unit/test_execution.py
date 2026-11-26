"""Execution simulator: costs, quotes, idempotency, modes (cases 9, 14, 20, 21, 22)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aegis.accounting.ledger import Account
from aegis.accounting.portfolio import PortfolioService
from aegis.config import ExecutionMode, StrategyConfig
from aegis.core.assets import WSOL_MINT
from aegis.core.clock import ManualClock
from aegis.execution.costs import CostModel
from aegis.execution.modes import LiveExecutionDisabled, assert_simulated
from aegis.execution.simulator import SimulatedExecutionEngine, TokenRef
from aegis.execution.types import MarketContext, SwapQuote
from tests.conftest import T0, new_token

D = Decimal
MINT = "Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpump"
SOL_EUR = D("64.17")


def _setup(engine, mode=ExecutionMode.PAPER_TRADING, cfg=None):
    cfg = cfg or StrategyConfig()
    clock = ManualClock(T0)
    svc = PortfolioService(cfg)
    ex = SimulatedExecutionEngine(cfg, mode, svc, clock)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
        tid = new_token(c, address=MINT)
    return cfg, clock, svc, ex, TokenRef(tid, MINT, 6)


def _market(price="0.0075", liq="190000", at=T0):
    return MarketContext(price_eur=D(price), liquidity_eur=D(liq), sol_eur=SOL_EUR, observed_at=at, pool_type="amm")


def _quote(at, in_raw=13_989_000, out_raw=122_451_083, inp=WSOL_MINT, outp=MINT):
    return SwapQuote(source="jupiter_swap_v1", input_mint=inp, output_mint=outp, in_amount_raw=in_raw,
                     out_amount_raw=out_raw, min_out_raw=int(out_raw * 0.97), price_impact_pct=D("0.19"),
                     route_labels=("Pump.fun Amm",), fetched_at=at)


def test_live_modes_cannot_execute():  # case 20
    with pytest.raises(LiveExecutionDisabled):
        assert_simulated(ExecutionMode.LIVE_DISABLED)
    with pytest.raises(LiveExecutionDisabled):
        SimulatedExecutionEngine(StrategyConfig(), ExecutionMode.LIVE_DISABLED, PortfolioService(StrategyConfig()),
                                 ManualClock(T0))


def test_buy_with_executable_quote_books_all_costs(engine):
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k1",
                   quote=_quote(T0), rent_lamports=1_488_440)
        assert r.filled, r.reason
        f = r.buy
        assert f.cash_out_eur == D(1)  # all-in ticket respected exactly
        assert f.deposit_eur == D("0.095513194800")  # 1,488,440 lamports at 64.17 EUR/SOL
        assert f.method == "jupiter_quote"
        # adverse 1% haircut applied to quoted output
        assert f.token_qty == D("121.226572")
        assert svc.reconcile(c).ok
        assert svc.ledger.balance(c, Account.CASH_INVESTMENT) == D(99)


def test_duplicate_order_key_does_not_double_fill(engine):  # case 14
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        r1 = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="same",
                    rent_lamports=1_488_440)
        r2 = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="same",
                    rent_lamports=1_488_440)
        assert r1.filled and r2.status == "DUPLICATE" and r2.order_id == r1.order_id
        assert svc.ledger.balance(c, Account.CASH_INVESTMENT) == D(99)


def test_expired_quote_rejected(engine):  # case 22
    cfg, clock, svc, ex, tok = _setup(engine)
    old = _quote(T0 - dt.timedelta(seconds=60))
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k",
                   quote=old, rent_lamports=1_488_440)
        assert r.status == "EXPIRED" and r.reason == "quote_expired"
        assert svc.ledger.balance(c, Account.CASH_INVESTMENT) == D(100)


def test_costs_above_microposition_rejected(engine):  # case 21
    cfg = StrategyConfig()
    cfg.execution.priority_fee_lamports = 10_000_000  # 0.01 SOL ~ 0.64 EUR per tx in congestion
    _, clock, svc, ex, tok = _setup(engine, cfg=cfg)
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k",
                   rent_lamports=1_488_440)
        assert r.status == "REJECTED" and "roundtrip_cost" in r.reason


def test_ticket_smaller_than_fixed_costs_rejected(engine):
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D("0.05"), market=_market(),
                   idempotency_key="k", rent_lamports=1_488_440)
        assert r.status == "REJECTED" and r.reason == "costs_exceed_ticket"


def test_stale_market_data_blocks_order(engine):  # case 11 (execution layer)
    cfg, clock, svc, ex, tok = _setup(engine)
    clock.set(T0 + dt.timedelta(hours=2))
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(at=T0), idempotency_key="k")
        assert r.reason == "stale_market_data"


def test_liquidity_disappearance_makes_sale_unviable_then_burn_close(engine):  # case 9
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        assert b.filled
        clock.advance(days=8)
        dead = _market(price="0.00000001", liq="20", at=clock.now())
        r = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1), market=dead, idempotency_key="s")
        assert r.status == "REJECTED" and r.reason == "proceeds_below_minimum_sale"
        bc = ex.burn_close(c, token=tok, position_id=b.position_id, sol_eur=SOL_EUR, idempotency_key="bc")
        assert bc.filled
        assert bc.sale.realized_gain < 0  # full write-off of cost basis, minus close fee
        assert svc.ledger.balance(c, Account.DEPOSITS) == 0  # rent recovered
        assert svc.reconcile(c).ok


def test_partial_then_full_sell_with_quote(engine):
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        clock.advance(hours=3)
        hot = _market(price="0.03", liq="400000", at=clock.now())
        r = ex.sell(c, token=tok, position_id=b.position_id, fraction=D("0.5"), market=hot, idempotency_key="s1")
        assert r.filled and r.sale.realized_gain > 0 and not r.sale.closed
        pos = svc.open_position(c, tok.id)
        q = _quote(clock.now(), in_raw=int(pos.qty * 10**6), out_raw=900_000_000, inp=MINT, outp=WSOL_MINT)
        r2 = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1), market=hot, idempotency_key="s2", quote=q)
        assert r2.filled and r2.sale.closed and r2.sell.method == "jupiter_quote"
        assert svc.reconcile(c).ok


def test_shadow_mode_never_books(engine):  # case 18 (execution side)
    cfg, clock, svc, ex, tok = _setup(engine, mode=ExecutionMode.SHADOW_TRADING)
    with engine.begin() as c:
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k",
                   rent_lamports=1_488_440)
        assert r.status == "SHADOW"
        assert svc.ledger.balance(c, Account.CASH_INVESTMENT) == D(100)
        assert svc.active_positions(c) == []


def test_cpmm_math():
    res = CostModel(StrategyConfig().execution).cpmm_swap(D(1), D(1000), D(1000), 0)
    assert abs(res.out_amount - D(1000) / D(1001)) < D("1e-30")
    assert abs(res.price_impact_pct - D(100) / D(1001)) < D("1e-30")


def test_roundtrip_cost_of_one_euro_ticket_is_material():
    cm = CostModel(StrategyConfig().execution)
    pct = cm.roundtrip_cost_pct(D(1), D("0.0001"), D(20000), SOL_EUR, 1_488_440)
    assert D("1.5") < pct < D("5")  # ~0.6% pool fees+impact per side + fees; deposit excluded


def test_buy_draws_only_the_budgeted_allowance_per_cash_account(engine):
    """Regression (phase 2): a buy funded 'recycled first' used to drain recycled cash beyond the allocation
    budget's recycled cap (and with recycling='none'). The allowance makes the split follow the budget."""
    from aegis.accounting.ledger import Posting
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        svc.ledger.post(c, occurred_at=T0, recorded_at=T0, kind="adjustment", description="test recycled cash",
                        postings=[Posting(Account.CASH_RECYCLED, D(5)), Posting(Account.PNL_REALIZED, D(-5))],
                        idempotency_key="seed-recycled")
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k-allow",
                   quote=_quote(T0), rent_lamports=1_488_440,
                   allowance={Account.CASH_INVESTMENT: D(1), Account.CASH_RECYCLED: D(0)})
        assert r.filled and dict(r.funding) == {Account.CASH_INVESTMENT: D(1)}
        assert svc.ledger.balance(c, Account.CASH_RECYCLED) == D(5)
        r2 = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k-none",
                    quote=_quote(T0), rent_lamports=1_488_440, allowance={Account.CASH_INVESTMENT: D(0)})
        assert not r2.filled and r2.reason == "insufficient_budgeted_cash"
        r3 = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k-legacy",
                    quote=_quote(T0), rent_lamports=1_488_440)
        assert r3.filled and dict(r3.funding)[Account.CASH_RECYCLED] > 0  # no allowance: recycled first (legacy)
        rec = svc.reconcile(c)  # the seeded P&L has no disposal behind it: only the balance checks apply here
        assert rec.checks["trial_balance_zero"]["ok"] and rec.checks["capital_identity"]["ok"]


def test_allowance_with_excess_precision_is_rounded_down_to_the_ledger_scale(engine):
    """Regression: a recycled daily cap (balance x 0.10) carries > 12 decimals; funding must stay quantized."""
    from aegis.accounting.ledger import Posting
    cfg, clock, svc, ex, tok = _setup(engine)
    with engine.begin() as c:
        svc.ledger.post(c, occurred_at=T0, recorded_at=T0, kind="adjustment", description="test recycled cash",
                        postings=[Posting(Account.CASH_RECYCLED, D("4.542370929019")),
                                  Posting(Account.PNL_REALIZED, D("-4.542370929019"))], idempotency_key="seed-r2")
        allow = {Account.CASH_RECYCLED: D("4.542370929019") * D("0.10"), Account.CASH_INVESTMENT: D(1)}
        r = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="k-prec",
                   quote=_quote(T0), rent_lamports=1_488_440, allowance=allow)
        assert r.filled, r.reason
        assert all(amt == amt.quantize(D("1e-12")) for _, amt in r.funding)
        assert dict(r.funding)[Account.CASH_RECYCLED] == D("0.454237092901")  # rounded DOWN, never above the cap
