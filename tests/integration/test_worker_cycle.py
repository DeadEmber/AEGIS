"""End-to-end worker cycles on the SIMULATED market (cases 2, 3, 6, 7, 8, 10, 11, 12, 13, 15, 18, 19, 26, 30)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from aegis.accounting.ledger import Account
from aegis.config import ExecutionMode, RiskLimits, Settings, StrategyConfig
from aegis.core.clock import UTC, ManualClock
from aegis.research.simmarket import TOKENS, frozen_mint, make_script
from aegis.risk.controls import KILL_SWITCH
from aegis.storage import repo
from aegis.storage import schema as s
from aegis.storage.db import create_all, make_engine
from aegis.worker.gateway import ScriptedGateway
from aegis.worker.runner import Worker

START = dt.datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def _cfg() -> StrategyConfig:
    cfg = StrategyConfig()
    cfg.discovery.min_token_age_hours = Decimal(24)
    cfg.discovery.min_txns_24h = 50
    cfg.discovery.min_unique_traders_24h = 20
    cfg.monitoring.security_checks_per_cycle = 10
    return cfg


def _worker(url: str, clock: ManualClock, mode=ExecutionMode.PAPER_TRADING, gw=None, cfg=None) -> tuple[Worker, ScriptedGateway]:
    eng = make_engine(url)
    create_all(eng)
    gw = gw or ScriptedGateway(script=make_script(START), clock=clock.now, unsellable={TOKENS["honeypot"]},
                               mint_overrides={TOKENS["frozen"]: frozen_mint(START)})
    w = Worker(eng, Settings(database_url=url, execution_mode=mode), cfg or _cfg(), RiskLimits(), gw, clock)
    w.bootstrap()
    return w, gw


def _run(w: Worker, clock: ManualClock, hours: float, step_min: int = 30):
    reports = []
    end = clock.now() + dt.timedelta(hours=hours)
    while clock.now() < end:
        reports.append(w.run_cycle())
        clock.advance(minutes=step_min)
    return reports


def _scalar(w, q):
    with w.engine.connect() as c:
        return c.execute(q).scalar_one()


def test_full_lifecycle_on_simulated_market(tmp_path):
    clock = ManualClock(START)
    w, gw = _worker(f"sqlite:///{tmp_path}/a.sqlite", clock)
    reps = _run(w, clock, hours=60)
    assert all(r.reconciliation_ok for r in reps), [r.errors for r in reps if not r.reconciliation_ok]
    with w.engine.connect() as c:
        # case 1/2: one contribution; buys bounded by budget & pacing
        assert repo.count(c, s.contributions) == 1
        bought = {r.token_id for r in c.execute(select(s.positions.c.token_id))}
        addr = {r.id: r.address for r in c.execute(select(s.tokens.c.id, s.tokens.c.address))}
        held_addrs = {addr[t] for t in bought}
        # case 3/8: blocked tokens never bought
        assert TOKENS["honeypot"] not in held_addrs and TOKENS["frozen"] not in held_addrs
        assert TOKENS["rocket"] in held_addrs
        # rocket was monetised (partial/full sells happened) and P&L realised
        sells = c.execute(select(func.count()).select_from(s.orders).where(s.orders.c.side == "SELL",
                                                                            s.orders.c.status == "FILLED")).scalar_one()
        assert sells >= 1
        # case 15: reconciliation identity holds in the final state
        st = w.portfolio.state(c, clock.now(), {})
        assert st.reconciliation.ok
        # spent never exceeds contributions + recycled; tax reserve untouched by buys
        assert st.contributions_total == Decimal(100)
        # case 18: shadow rows exist and never touch ledger
        assert repo.count(c, s.shadow_positions) > 0
        tx_kinds = {r.kind for r in c.execute(select(s.ledger_transactions.c.kind))}
        assert tx_kinds <= {"contribution", "buy", "sell", "reserve_profit", "reserve_tax"}
        # security assessments recorded with BLOCK for the frozen token
        blocked = c.execute(select(s.security_assessments.c.verdict).join(s.tokens)
                            .where(s.tokens.c.address == TOKENS["frozen"])).scalars().all()
        assert blocked and set(blocked) == {"BLOCK"}


def test_restart_without_state_loss(tmp_path):  # cases 12, 13, 30
    url = f"sqlite:///{tmp_path}/r.sqlite"
    clock = ManualClock(START)
    w1, _ = _worker(url, clock)
    _run(w1, clock, hours=6)
    with w1.engine.connect() as c:
        before = (repo.count(c, s.ledger_transactions), w1.portfolio.ledger.balances(c), repo.count(c, s.positions))
    w1.engine.dispose()  # simulate crash / process death
    w2, _ = _worker(url, clock)  # new process, same database
    with w2.engine.connect() as c:
        assert (repo.count(c, s.ledger_transactions), w2.portfolio.ledger.balances(c),
                repo.count(c, s.positions)) == before
    _run(w2, clock, hours=3)
    with w2.engine.connect() as c:
        assert repo.count(c, s.contributions) == 1  # no duplicated contribution after restart
        assert w2.portfolio.reconcile(c).ok


def test_kill_switch_blocks_all_orders(tmp_path):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/k.sqlite", clock)
    with w.engine.begin() as c:
        repo.set_flag(c, KILL_SWITCH, "on", clock.now(), "test", "manual")
    reps = _run(w, clock, hours=2)
    assert sum(r.buys + r.sells for r in reps) == 0
    assert any("kill switch ON" in r.controls for r in reps)


def test_api_outage_and_stale_data_suspend_buys(tmp_path):  # cases 10, 11
    clock = ManualClock(START)
    w, gw = _worker(f"sqlite:///{tmp_path}/o.sqlite", clock)
    gw.fail_sources = {"coingecko", "frankfurter_ecb"}  # no reference rates at all
    rep = w.run_cycle()
    assert rep.buys == 0 and rep.errors
    gw.fail_sources = set()
    _run(w, clock, hours=1)
    gw.fail_sources = {"coingecko"}  # SOL/EUR goes stale
    clock.advance(hours=2)
    rep = w.run_cycle()
    assert rep.buys == 0 and any("SOL/EUR" in r for r in rep.controls)
    gw.fail_sources = {"geckoterminal"}
    rep = w.run_cycle()
    assert rep.observations == 0  # no fabricated observations during outage
    with w.engine.connect() as c:
        assert w.portfolio.reconcile(c).ok


def test_shadow_mode_never_books(tmp_path):  # case 18
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/sh.sqlite", clock, mode=ExecutionMode.SHADOW_TRADING)
    _run(w, clock, hours=4)
    with w.engine.connect() as c:
        assert repo.count(c, s.positions) == 0
        assert repo.count(c, s.orders, s.orders.c.status == "SHADOW") > 0
        assert w.portfolio.ledger.balance(c, Account.CASH_INVESTMENT) == Decimal(100)


def test_live_disabled_mode_refuses_to_start(tmp_path):  # case 20
    from aegis.execution.modes import LiveExecutionDisabled

    clock = ManualClock(START)
    with pytest.raises(LiveExecutionDisabled):
        _worker(f"sqlite:///{tmp_path}/l.sqlite", clock, mode=ExecutionMode.LIVE_DISABLED)


def test_decisions_are_reproducible(tmp_path):  # case 19
    hashes = []
    for name in ("x", "y"):
        clock = ManualClock(START)
        w, _ = _worker(f"sqlite:///{tmp_path}/{name}.sqlite", clock)
        _run(w, clock, hours=8)
        with w.engine.connect() as c:
            hashes.append([(r.action, r.inputs_hash) for r in c.execute(
                select(s.decisions.c.action, s.decisions.c.inputs_hash).order_by(s.decisions.c.id))])
    assert hashes[0] == hashes[1] and len(hashes[0]) > 0


def test_simulated_and_live_data_never_mixed(tmp_path):
    url = f"sqlite:///{tmp_path}/m.sqlite"
    clock = ManualClock(START)
    w, _ = _worker(url, clock)
    with w.engine.begin() as c:
        repo.set_flag(c, "data_mode", "live", clock.now(), "test")
    with pytest.raises(RuntimeError, match="refusing to mix"):
        _worker(url, clock)


def test_retention_never_touches_financial_records(tmp_path):
    from aegis.storage.archive import archive_all
    from aegis.storage.retention import RetentionPolicy, prune

    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/p.sqlite", clock)
    _run(w, clock, hours=12)
    root = tmp_path / "archive"
    with w.engine.begin() as c:
        before = {t: repo.count(c, getattr(s, t)) for t in ("ledger_postings", "orders", "executions", "position_lots")}
        snaps = repo.count(c, s.market_snapshots)
        archive_all(c, root, clock.now() + dt.timedelta(hours=1))
        out = prune(c, clock.now(), root, RetentionPolicy(snapshot_full_days=0, feature_days=0, trivial_decision_days=0,
                                                         episode_pre_hours=0, episode_post_hours=0))
        after = {t: repo.count(c, getattr(s, t)) for t in before}
        assert before == after
        assert out["market_snapshots"] > 0 and repo.count(c, s.market_snapshots) < snaps
        assert w.portfolio.reconcile(c).ok


def test_strategy_comparison_same_conditions():  # case 26
    from aegis.research.compare import compare, hold_only, naive_stop

    def gw(clock):
        return ScriptedGateway(script=make_script(START), clock=clock.now, unsellable={TOKENS["honeypot"]},
                               mint_overrides={TOKENS["frozen"]: frozen_mint(START)})

    base = _cfg()
    res = {r["strategy"]: r for r in compare([base, hold_only(base), naive_stop(base)], gw, START, hours=60)}
    assert set(res) == {"aegis-baseline", "buy_and_hold", "stop_loss_0.7", "cash"}
    for name in ("aegis-baseline", "buy_and_hold", "stop_loss_0.7"):
        assert res[name]["always_reconciled"] and res[name]["buys"] == res["aegis-baseline"]["buys"]
    # identical entries, different exits => differences come only from exit policy
    assert res["buy_and_hold"]["sells"] == 0 and res["aegis-baseline"]["sells"] > 0
    # on THIS simulated path (rocket x10 then -85%) monetisation beats holding; not a general claim
    assert res["aegis-baseline"]["objective_eur"] > res["buy_and_hold"]["objective_eur"]


def test_explosion_episodes_tracked_and_capital_timing_recorded(tmp_path):
    from aegis.accounting import efficiency
    from aegis.research.temporal import episodes

    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/e.sqlite", clock)
    _run(w, clock, hours=60)
    with w.engine.connect() as c:
        rocket = c.execute(select(s.tokens.c.id).where(s.tokens.c.address == TOKENS["rocket"])).scalar_one()
        ep = c.execute(select(s.explosion_events).where(s.explosion_events.c.token_id == rocket)).first()
        assert ep is not None and ep.peak_at is not None and ep.ended_at is not None  # x10 run then collapse
        assert ep.peak_multiple > 2 and ep.started_at < ep.peak_at <= ep.ended_at
        kinds = {e.kind for e in episodes(c, clock.now()) if e.token_id == rocket}
        assert {"explosion_duration", "time_to_collapse", "investment_duration"} <= kinds
        eff = efficiency.compute(c, clock.now(), w.portfolio.ledger)
        assert eff.avg_execution_to_availability_s == pytest.approx(15.0)
        assert eff.investments_total >= 3 and eff.capital_turnover is not None
        hold = c.execute(select(s.decisions.c.scores).where(s.decisions.c.action == "HOLD").limit(1)).scalar_one()
        assert hold["ECV_status"] == "unavailable" and "velocity_to_date_per_day" in hold


def test_ecv_variant_identical_while_ecv_unavailable():  # experiment E1 harness, same economic conditions
    from aegis.research.compare import compare, with_ecv

    def gw(clock):
        return ScriptedGateway(script=make_script(START), clock=clock.now, unsellable={TOKENS["honeypot"]},
                               mint_overrides={TOKENS["frozen"]: frozen_mint(START)})

    base = _cfg()
    res = {r["strategy"]: r for r in compare([base, with_ecv(base)], gw, START, hours=36)}
    a, b = res["aegis-baseline"], res["aegis-ecv"]
    assert a["strategy_hash"] != b["strategy_hash"]
    assert (a["objective_eur"], a["buys"], a["sells"]) == (b["objective_eur"], b["buys"], b["sells"])


def test_shadow_horizons_and_checkpoints_in_worker(tmp_path):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/sh24.sqlite", clock)
    _run(w, clock, hours=26)
    with w.engine.connect() as c:
        rows = c.execute(select(s.shadow_positions.c.outcome, s.shadow_positions.c.closed_at)).all()
    assert rows and all(r.outcome.get("version") == 2 for r in rows)
    statuses = {r.outcome["horizons"]["24h"]["status"] for r in rows}
    assert "observed" in statuses  # simulated tokens are observed every cycle
    assert all(r.closed_at is None for r in rows)  # 7d..365d still censored
