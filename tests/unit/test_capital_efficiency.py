"""Capital-time efficiency: settlement availability, recovery timing, efficiency metrics, liquidity caps,
temporal features/labels with censoring, Kaplan-Meier baseline, and the experimental ECV indicator."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import insert

from aegis.accounting import efficiency
from aegis.accounting.ledger import Account
from aegis.accounting.portfolio import PortfolioService
from aegis.config import ExecutionMode, StrategyConfig
from aegis.core.clock import ManualClock
from aegis.decision.ecv import EmpiricalBaseline, NoEstimates, OutcomeEstimate, compute_ecv, ecv_value
from aegis.decision.engine import Action, DecisionEngine
from aegis.execution.simulator import SimulatedExecutionEngine, TokenRef
from aegis.execution.types import MarketContext
from aegis.features.library import FeatureContext, compute
from aegis.research.temporal import Episode, empirical_duration, episodes, kaplan_meier
from aegis.storage import schema as s
from tests.conftest import T0, new_token
from tests.unit.test_decision_allocation import NOW, _cand, _fv, _pos
from tests.unit.test_execution import MINT, SOL_EUR, _market

D = Decimal


def _engine(engine, cfg=None):
    cfg = cfg or StrategyConfig()
    clock = ManualClock(T0)
    svc = PortfolioService(cfg)
    ex = SimulatedExecutionEngine(cfg, ExecutionMode.PAPER_TRADING, svc, clock)
    with engine.begin() as c:
        svc.contribute_month(c, T0)
    return cfg, clock, svc, ex


def _tok(c, address=MINT):
    return TokenRef(new_token(c, address=address), address, 6)


# ----------------------------------------------------------------- execution timing & settlement
def test_decision_execution_and_capital_availability_are_recorded(engine):
    cfg, clock, svc, ex = _engine(engine)
    with engine.begin() as c:
        tok = _tok(c)
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        clock.advance(hours=1)
        hot = _market(price="0.03", liq="400000", at=clock.now())
        r = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1), market=hot, idempotency_key="s")
        row = c.execute(s.executions.select().where(s.executions.c.id == r.execution_id)).first()
        assert row.details["decision_to_execution_ms"] == 2000  # simulated latency
        assert row.capital_available_at - row.executed_at == dt.timedelta(seconds=cfg.execution.settlement_seconds)
        buy_row = c.execute(s.executions.select().where(s.executions.c.id == b.execution_id)).first()
        assert buy_row.capital_available_at is None  # purchases commit capital; nothing becomes available


def test_recycled_proceeds_not_spendable_before_settlement(engine):
    cfg, clock, svc, ex = _engine(engine)
    with engine.begin() as c:
        tok = _tok(c)
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        clock.advance(hours=1)
        r = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1),
                    market=_market(price="0.03", liq="400000", at=clock.now()), idempotency_key="s")
        executed = r.sell.executed_at
        recycled = svc.ledger.balance(c, Account.CASH_RECYCLED)
        assert recycled > 0
        assert svc.spendable(c, at=executed + dt.timedelta(seconds=5))[Account.CASH_RECYCLED] == 0
        assert svc.unsettled_proceeds(c, executed) == r.sell.cash_in_eur
        assert svc.spendable(c, at=executed + dt.timedelta(seconds=16))[Account.CASH_RECYCLED] == recycled


def test_exit_impact_limit_rejects_non_critical_but_allows_critical(engine):
    cfg, clock, svc, ex = _engine(engine)
    with engine.begin() as c:
        tok = _tok(c)
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        clock.advance(hours=1)
        thin = _market(price="0.5", liq="80", at=clock.now())  # position worth ~60 EUR vs 40 EUR per side
        r = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1), market=thin, idempotency_key="s1")
        assert r.status == "REJECTED" and "exit_impact" in r.reason
        r2 = ex.sell(c, token=tok, position_id=b.position_id, fraction=D(1), market=thin, idempotency_key="s2",
                     critical=True)
        assert r2.filled and r2.sell.details["critical"] is True


def test_decision_engine_caps_non_critical_exits_to_liquidity():
    from aegis.state.machine import MarketState

    # Pool above the critical threshold (1500 EUR) but thin vs. a 500 EUR position: trailing exit to residual
    # (90%) would be 450 EUR; with 15% max impact the half-pool of 750 EUR allows ~132.35 EUR.
    p = _pos(qty=D(10000), original_qty=D(10000), liquidation_eur=D(400), liquidity_eur=D(1500), price_eur=D("0.05"),
             peak_price_eur=D("0.10"), capital_recovered=True, realized_proceeds=D(1), state=MarketState.EXPLOSION)
    d = DecisionEngine(StrategyConfig()).evaluate_position(p, NOW)
    assert d.action == Action.PARTIAL_SELL and any("liquidity-capped" in r for r in d.reasons)
    assert abs(d.fraction - (D(750) * D("0.15") / D("0.85")) / D(500)) < D("1e-9")
    assert not d.critical
    # critical liquidity (< 1000 EUR) is a critical exit: never capped
    crit = DecisionEngine(StrategyConfig()).evaluate_position(_pos(liquidity_eur=D(100)), NOW)
    assert crit.action == Action.FULL_SELL and crit.critical and crit.fraction == 1
    sec = DecisionEngine(StrategyConfig()).evaluate_position(_pos(security_verdict="BLOCK"), NOW)
    assert sec.critical


# ----------------------------------------------------------------- accounting metrics
def test_capital_efficiency_metrics(engine):
    cfg, clock, svc, ex = _engine(engine)
    with engine.begin() as c:
        a, bt = _tok(c), _tok(c, address="Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpumq")
        buy_a = ex.buy(c, token=a, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="ba",
                       rent_lamports=1_488_440)
        svc.persist_snapshot(c, svc.state(c, clock.now(), {}))
        clock.advance(days=2)
        sale = ex.sell(c, token=a, position_id=buy_a.position_id, fraction=D(1),
                       market=_market(price="0.03", liq="400000", at=clock.now()), idempotency_key="sa")
        svc.persist_snapshot(c, svc.state(c, clock.now(), {}))
        clock.advance(days=1)
        ex.buy(c, token=bt, bucket="selection", ticket_eur=D(1),
               market=MarketContext(D("0.0075"), D(190000), SOL_EUR, clock.now(), "amm"), idempotency_key="bb",
               rent_lamports=1_488_440)
        svc.persist_snapshot(c, svc.state(c, clock.now(), {}))
        e = efficiency.compute(c, clock.now(), svc.ledger, svc.spendable(c, at=clock.now()))
    assert e.contributions_total_eur == D(100)
    assert e.purchases_total_eur == D(2) and e.investments_total == 2
    assert e.purchases_from_recycled_eur == D(1) and e.investments_funded_by_recycled == 1
    assert e.investments_fully_recycled == 1 and e.capital_recycling_ratio == D("0.01")
    assert e.recycled_inflows_total_eur == sale.sell.cash_in_eur
    # snapshots: committed (cost + deposit) = c1 for 2 days, 0 for 1 day -> time-weighted average 2/3 * c1
    assert e.avg_committed_capital_cost_eur is not None and e.capital_turnover is not None
    assert abs(float(e.capital_turnover) - 2 / float(e.avg_committed_capital_cost_eur)) < 1e-9
    assert e.period_days == pytest.approx(3.0)
    assert e.avg_holding_period_days_realized == pytest.approx(2.0, abs=1e-3)  # cost-weighted, 2 days
    assert e.positions_capital_recovered == 1 and e.mean_days_to_capital_recovery == pytest.approx(2.0, abs=1e-3)
    assert e.avg_decision_to_execution_s == pytest.approx(2.0)
    assert e.avg_execution_to_availability_s == pytest.approx(15.0)
    assert D(0) < e.avg_partial_recovery_fraction <= D(1)


def test_partial_recovery_sets_recovery_time_only_when_crossed(engine):
    cfg, clock, svc, ex = _engine(engine)
    with engine.begin() as c:
        tok = _tok(c)
        b = ex.buy(c, token=tok, bucket="exploration", ticket_eur=D(1), market=_market(), idempotency_key="b",
                   rent_lamports=1_488_440)
        clock.advance(hours=5)
        ex.sell(c, token=tok, position_id=b.position_id, fraction=D("0.10"),
                market=_market(price="0.0080", liq="190000", at=clock.now()), idempotency_key="s1")
        pos = svc.open_position(c, tok.id)
        assert pos.capital_recovered_at is None and pos.realized_proceeds_eur < pos.invested_eur_total
        clock.advance(hours=5)
        ex.sell(c, token=tok, position_id=b.position_id, fraction=D("0.5"),
                market=_market(price="0.05", liq="900000", at=clock.now()), idempotency_key="s2")
        pos = svc.open_position(c, tok.id)
        assert pos.capital_recovered and pos.capital_recovered_at is not None
        assert dt.timedelta(hours=10) <= pos.capital_recovered_at - pos.opened_at <= dt.timedelta(hours=10, seconds=5)


# ----------------------------------------------------------------- temporal features & labels
def test_temporal_features_point_in_time():
    from tests.unit.test_intelligence import NOW as N2
    from tests.unit.test_intelligence import _hot_pool

    ctx = FeatureContext(as_of=N2, obs=_hot_pool(), history=[], market_state="EXPLOSION",
                         market_state_since=N2 - dt.timedelta(hours=3),
                         active_explosion_started_at=N2 - dt.timedelta(hours=5), explosion_count=2)
    fv = compute(ctx)
    assert fv.values["hours_in_state"] == pytest.approx(3) and fv.values["hours_since_explosion_start"] == pytest.approx(5)
    assert fv.values["explosion_count"] == 2 and fv.missing["hours_since_last_exit"] == "never_exited"
    with pytest.raises(ValueError, match="look-ahead"):
        compute(FeatureContext(as_of=N2, obs=_hot_pool(), history=[], market_state_since=N2 + dt.timedelta(hours=1)))


def test_kaplan_meier_with_censoring():
    km = kaplan_meier([1, 2, 3, 4], [True, False, True, True])
    assert km.survival == pytest.approx([0.75, 0.375, 0.0])
    assert km.quantile_time(0.5) == 3
    assert kaplan_meier([1, 2, 3, 4, 5], [True] * 5).quantile_time(0.5) == 3


def test_temporal_labels_censor_unfinished_episodes(engine):
    t = T0
    with engine.begin() as c:
        tok = new_token(c, t=t)
        for h, st in ((1, "EXPANSION"), (2, "EXPLOSION"), (5, "DISTRIBUTION"), (7, "COLLAPSE")):
            c.execute(insert(s.token_states).values(token_id=tok, valid_from=t + dt.timedelta(hours=h), state=st,
                                                    reason="test", evidence={}, engine_version="t"))
        c.execute(insert(s.explosion_events).values(token_id=tok, started_at=t + dt.timedelta(hours=2), wave_number=1,
                                                    base_price_usd=D(1), ended_at=t + dt.timedelta(hours=7)))
        eps = {e.kind: e for e in episodes(c, t + dt.timedelta(hours=10))}
    assert eps["time_to_expansion"].hours == pytest.approx(1) and not eps["time_to_expansion"].censored
    assert eps["explosion_duration"].hours == pytest.approx(5) and not eps["explosion_duration"].censored
    assert eps["time_to_exhaustion"].hours == pytest.approx(3)
    assert eps["time_to_collapse"].hours == pytest.approx(5)
    assert eps["time_to_recovery"].censored and eps["time_to_recovery"].hours == pytest.approx(3)
    assert eps["time_to_second_wave"].censored


def test_no_temporal_estimate_without_enough_events():
    few = [Episode(i, "explosion_duration", T0, T0 + dt.timedelta(hours=i + 1), False) for i in range(5)]
    est = empirical_duration(few, "explosion_duration", min_events=30)
    assert est.status == "insufficient_data" and est.median_hours is None
    many = [Episode(i, "explosion_duration", T0, T0 + dt.timedelta(hours=i % 10 + 1), i % 7 == 0) for i in range(80)]
    est2 = empirical_duration(many, "explosion_duration", min_events=30)
    assert est2.status == "empirical_baseline" and est2.median_hours is not None
    assert est2.ci_low_hours <= est2.median_hours <= est2.ci_high_hours
    assert empirical_duration([], "time_to_collapse").status == "unavailable"


# ----------------------------------------------------------------- ECV
def test_ecv_formula_and_unavailability():
    assert ecv_value(0.5, 1.0, 10) == pytest.approx(0.05)
    assert ecv_value(1, 0, 1) is None and ecv_value(1, 1, 0) is None
    r = compute_ecv(D("2.5"), NoEstimates().position(1), "liquidable_value")
    assert r.status == "unavailable" and r.value_per_day is None
    est = OutcomeEstimate(0.20, 4.0, source="model")
    r2 = compute_ecv(D("2.5"), est, "liquidable_value")
    assert r2.status == "model" and r2.value_per_day == pytest.approx(0.05) and r2.expected_net_profit_eur == pytest.approx(0.5)
    assert compute_ecv(D(1), OutcomeEstimate(0.2, 4.0, source="empirical_baseline"), "ticket").status == "experimental"


def test_position_ecv_uses_liquidable_value_not_cost():
    class Prov(NoEstimates):
        def position(self, position_id):
            return OutcomeEstimate(0.10, 2.0, source="model")

    eng = DecisionEngine(StrategyConfig(), Prov())
    d = eng.evaluate_position(_pos(liquidation_eur=D(6), cost_basis=D("0.9"), opened_at=NOW - dt.timedelta(days=2),
                                   capital_recovered=True, realized_proceeds=D(1)), NOW)
    assert d.scores["ECV_basis"] == "liquidable_value" and d.scores["ECV"] == pytest.approx(0.05)
    assert d.scores["velocity_to_date_per_day"] == pytest.approx((6 + 1 - 1) / 1 / 2)
    d0 = DecisionEngine(StrategyConfig()).evaluate_position(_pos(), NOW)
    assert d0.scores["ECV_status"] == "unavailable"


def test_ecv_is_complementary_and_inert_when_unavailable():
    strong = dict(acceleration_short=2.5, buyer_growth_short=1.2, ret_h1=0.5, buyer_share_h1=0.7, buyer_share_h6=0.7,
                  buyer_share_h24=0.7, buyer_rate_h1=500, organic_score=60)
    cands = [_cand(i, **strong) for i in range(1, 6)] + [_cand(i) for i in range(6, 20)]
    slots = {"exploration": 2, "selection": 2, "second_chance": 0}
    on = StrategyConfig()
    on.ecv.enabled_in_selection = True
    base_buys, _ = DecisionEngine(StrategyConfig()).plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="x")
    on_buys, _ = DecisionEngine(on).plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="x")
    assert [b.token_id for b in base_buys] == [b.token_id for b in on_buys]  # unavailable ECV changes nothing
    assert all(b.scores["ECV_status"] == "unavailable" for b in on_buys)

    class Prov(NoEstimates):
        def candidate(self, token_id):  # pretend model: favours token 5
            return OutcomeEstimate(0.5 if token_id == 5 else 0.0, 5.0, source="model")

    ranked, _ = DecisionEngine(on, Prov()).plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="x")
    sel = [b.token_id for b in ranked if b.bucket == "selection"]
    assert 5 in sel
    # ECV never qualifies a candidate on its own: weak candidates are not selected even with high ECV
    class Weak(NoEstimates):
        def candidate(self, token_id):
            return OutcomeEstimate(5.0 if token_id >= 6 else 0.0, 1.0, source="model")

    ranked2, _ = DecisionEngine(on, Weak()).plan_entries(cands, slots, NOW, held_tokens=set(), seed_material="x")
    assert all(b.token_id <= 5 for b in ranked2 if b.bucket == "selection")


def test_empirical_baseline_requires_enough_closed_positions(engine):
    with engine.connect() as c:
        eb = EmpiricalBaseline(c, NOW, [], min_closed=30)
    est = eb.candidate(1)
    assert est.source == "none" and "insufficient data" in est.note
    assert _fv  # imported helper used by other tests
