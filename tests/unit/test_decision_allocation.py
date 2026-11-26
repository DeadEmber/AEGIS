"""Decision & allocation engines (cases 2, 7, 24, 26 + regressions found in simulation review)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from aegis.allocation.engine import AllocationEngine, reassign_unused, split_slots
from aegis.config import RiskLimits, StrategyConfig
from aegis.core.clock import UTC
from aegis.decision.engine import Action, Candidate, DecisionEngine, PositionView
from aegis.discovery.engine import Eligibility
from aegis.features.library import FeatureVector
from aegis.scoring.heuristics import score_all
from aegis.state.machine import MarketState
from tests.conftest import T0, buy_fill, new_execution, new_token

D = Decimal
NOW = dt.datetime(2026, 5, 10, 12, tzinfo=UTC)


def _fv(**vals) -> FeatureVector:
    return FeatureVector(NOW, "fs-test", values={k: float(v) for k, v in vals.items()})


def _pos(**kw) -> PositionView:
    base = dict(position_id=1, token_id=1, status="OPEN", qty=D(1000), original_qty=D(1000), cost_basis=D("0.9"),
                invested_total=D(1), realized_proceeds=D(0), capital_recovered=False, peak_price_eur=D("0.001"),
                price_eur=D("0.001"), liquidation_eur=D("0.95"), liquidity_eur=D(50000), deposit_eur=D("0.0955"),
                state=MarketState.EXPANSION, security_verdict="PASS", features=_fv(), scores=score_all(_fv()),
                last_partial_at=None, dead_since=None, data_stale=False)
    base.update(kw)
    return PositionView(**base)


def test_capital_recovery_keeps_residual_no_fixed_ceiling():
    d = DecisionEngine(StrategyConfig()).evaluate_position(_pos(liquidation_eur=D("2.5")), NOW)
    assert d.action == Action.PARTIAL_SELL and "capital recovery" in d.reasons[0]
    assert D("0.40") < d.fraction < D("0.45")  # ~1/2.5 plus a small buffer, never the whole position
    huge = DecisionEngine(StrategyConfig()).evaluate_position(
        _pos(liquidation_eur=D(500), capital_recovered=True, realized_proceeds=D(1)), NOW)
    assert huge.action == Action.HOLD  # a x500 winner with no exhaustion evidence is held, not capped


def test_residual_is_never_dust_traded():  # regression: 36 zero-quantity orders in simulation
    p = _pos(status="RESIDUAL", qty=D(100), capital_recovered=True, realized_proceeds=D(3), liquidation_eur=D("0.3"),
             peak_price_eur=D("0.01"), price_eur=D("0.003"))
    d = DecisionEngine(StrategyConfig()).evaluate_position(p, NOW)
    assert d.action == Action.HOLD


def test_trailing_floor_sells_to_residual_on_large_winner():
    p = _pos(liquidation_eur=D(6), capital_recovered=True, realized_proceeds=D(1), qty=D(500),
             peak_price_eur=D("0.02"), price_eur=D("0.012"), features=_fv(realized_vol_h6=0.05))
    d = DecisionEngine(StrategyConfig()).evaluate_position(p, NOW)
    assert d.action == Action.PARTIAL_SELL and "trailing floor" in d.reasons[0]
    assert d.fraction == D(1) - D("0.10") / D("0.5")


def test_deterioration_exit_recovers_capital():  # regression: slow death lost 93% without action
    p = _pos(liquidation_eur=D("0.4"), state=MarketState.WATCHLIST,
             features=_fv(liquidity_change_h24=-0.6, buyer_share_h24=0.40))
    d = DecisionEngine(StrategyConfig()).evaluate_position(p, NOW)
    assert d.action == Action.FULL_SELL and "deteriorating" in d.reasons[0]
    cfg = StrategyConfig()
    cfg.exits.deterioration_exit = False
    assert DecisionEngine(cfg).evaluate_position(p, NOW).action == Action.HOLD


def test_fast_explosion_sale_decision_is_immediate():  # case 24
    p = _pos(liquidation_eur=D(40), state=MarketState.EXPLOSION, peak_price_eur=D("0.05"), price_eur=D("0.05"))
    d = DecisionEngine(StrategyConfig()).evaluate_position(p, NOW)
    assert d.action == Action.PARTIAL_SELL and d.priority >= 6  # high-priority capital recovery at x40
    assert d.fraction < D("0.05") * 2  # sells ~1/40 only: keeps the upside


def test_critical_paths_and_unknowns():
    eng = DecisionEngine(StrategyConfig())
    assert eng.evaluate_position(_pos(data_stale=True), NOW).action == Action.HOLD
    assert eng.evaluate_position(_pos(security_verdict="BLOCK"), NOW).action == Action.FULL_SELL
    dead = _pos(liquidity_eur=D(100), liquidation_eur=D("0.01"), dead_since=NOW - dt.timedelta(days=8))
    d = eng.evaluate_position(dead, NOW)
    assert d.action == Action.FULL_SELL and d.method == "burn_close"


def _cand(tid, epi_vals=None, entries=0, last_exit=None, state=MarketState.ELIGIBLE, eligible=True, sec="PASS",
          liq=D(50000), liq_exit=D(60000), **fv):
    f = _fv(**(epi_vals or {}), **fv)
    return Candidate(tid, f"A{tid}", state, Eligibility(eligible), sec, f, score_all(f), entries, last_exit, liq,
                     liq_exit, 1 if entries else 0, D(3))


def test_entry_buckets_preserve_exploration_and_are_deterministic():  # case 2/26
    eng = DecisionEngine(StrategyConfig())
    cands = [_cand(i, acceleration_short=2.5, buyer_growth_short=1.2, ret_h1=0.5, buyer_share_h1=0.7,
                   buyer_share_h6=0.7, buyer_share_h24=0.7, buyer_rate_h1=500, organic_score=60) for i in range(1, 4)]
    cands += [_cand(i) for i in range(4, 30)]  # eligible but no signal: exploration pool
    slots = split_slots(10, StrategyConfig())
    buys, others = eng.plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="s1")
    buckets = [b.bucket for b in buys]
    assert buckets.count("selection") == 3  # only 3 qualified; spare slots go to exploration
    assert buckets.count("exploration") == 7 and len(buys) == 10
    buys2, _ = eng.plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="s1")
    assert [b.token_id for b in buys] == [b.token_id for b in buys2]
    assert {o.action for o in others} <= {Action.WATCH, Action.WAIT}


def test_reentry_into_former_winner():  # case 7
    eng = DecisionEngine(StrategyConfig())
    c = _cand(1, entries=1, last_exit=NOW - dt.timedelta(days=3), state=MarketState.RECOVERY,
              drawdown_from_ath=0.9, multiple_from_low_since_ath=3.0, buyer_share_h1=0.65)
    buys, _ = eng.plan_entries([c], {"exploration": 0, "selection": 0, "second_chance": 1}, NOW, held_tokens=set(),
                               seed_material="x")
    assert len(buys) == 1 and buys[0].action == Action.REENTRY and buys[0].bucket == "second_chance"
    too_soon = _cand(1, entries=1, last_exit=NOW - dt.timedelta(hours=2), state=MarketState.RECOVERY,
                     drawdown_from_ath=0.9, multiple_from_low_since_ath=3.0, buyer_share_h1=0.65)
    buys, others = eng.plan_entries([too_soon], {"exploration": 0, "selection": 0, "second_chance": 1}, NOW,
                                    held_tokens=set(), seed_material="x")
    assert not buys and "too soon after exit" in others[0].reasons


def test_blocked_or_collapsed_candidates_rejected():
    eng = DecisionEngine(StrategyConfig())
    _, others = eng.plan_entries([_cand(1, sec="BLOCK"), _cand(2, state=MarketState.COLLAPSE)],
                                 {"exploration": 5, "selection": 0, "second_chance": 0}, NOW, held_tokens=set(),
                                 seed_material="x")
    assert all(o.action == Action.REJECT for o in others) and len(others) == 2


def test_slot_split_and_reassignment():
    assert split_slots(10, StrategyConfig()) == {"exploration": 4, "selection": 5, "second_chance": 1}
    assert sum(split_slots(3, StrategyConfig()).values()) == 3
    assert reassign_unused({"exploration": 4, "selection": 5, "second_chance": 1},
                           {"exploration": 20, "selection": 1, "second_chance": 0}) == \
        {"exploration": 9, "selection": 1, "second_chance": 0}


def test_budget_pacing_respects_month_and_reserves(engine):  # case 2 & 28
    from aegis.accounting.ledger import Account
    from aegis.accounting.portfolio import PortfolioService

    cfg = StrategyConfig()
    svc = PortfolioService(cfg)
    alloc = AllocationEngine(cfg, RiskLimits())
    t = dt.datetime(2026, 1, 1, 9, tzinfo=UTC)
    with engine.begin() as c:
        svc.contribute_month(c, t)
        b = alloc.budget(c, t)
        assert b.investment_available_today == D(100) / 31 * D("1.5")  # paced, not 100 at once
        assert b.total_slots == 4
        # spend today's allowance
        for i in range(4):
            tok = new_token(c, t=t)
            ex = new_execution(c, tok, "BUY", t, f"k{i}")
            fill = buy_fill(t=t)
            svc.record_buy(c, token_id=tok, bucket="exploration", execution_id=ex, fill=fill,
                           funding=[(Account.CASH_INVESTMENT, fill.cash_out_eur)], idempotency_key=f"k{i}")
        assert alloc.budget(c, t + dt.timedelta(hours=1)).total_slots == 0
        assert alloc.budget(c, t + dt.timedelta(days=1)).total_slots == 4
    assert T0  # fixture module import sanity


def test_residual_reentry_signal_recorded_not_bought():  # audit P2 (2nd): hypothesis E3, policy unchanged
    eng = DecisionEngine(StrategyConfig())
    sig = dict(drawdown_from_ath=0.9, multiple_from_low_since_ath=3.0, buyer_share_h1=0.65)
    c = _cand(1, entries=1, state=MarketState.SECOND_WAVE, **sig)
    buys, others = eng.plan_entries([c], {"exploration": 3, "selection": 3, "second_chance": 3}, NOW,
                                    held_tokens={1}, held_residual={1}, seed_material="x")
    assert buys == [] and len(others) == 1
    assert others[0].action == Action.WATCH and others[0].scores["E3"] is True
    assert "reentry_blocked_by_open_residual" in others[0].reasons[0]
    # held but not residual, or residual without a second-wave signal: nothing recorded (unchanged behaviour)
    assert eng.plan_entries([c], {"exploration": 3, "selection": 3, "second_chance": 3}, NOW, held_tokens={1},
                            seed_material="x") == ([], [])
    weak = _cand(1, entries=1, state=MarketState.SECOND_WAVE)
    assert eng.plan_entries([weak], {"exploration": 3, "selection": 0, "second_chance": 0}, NOW, held_tokens={1},
                            held_residual={1}, seed_material="x") == ([], [])
