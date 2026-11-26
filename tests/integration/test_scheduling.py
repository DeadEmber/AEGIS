"""Audit P1 — effective monitoring frequency, starvation and priority (findings reproduced on 45240ba).

Before the fix: an unobserved position was rescheduled `now + 600 s` every cycle (reason
`position_unobserved`), so it never became due again (permanent starvation) and lost its priority;
and with a 300 s worker interval the configured 60/120/180 s intervals were unreachable.
"""

from __future__ import annotations

import datetime as dt
import itertools

from sqlalchemy import insert, select

from aegis.core.clock import ManualClock
from aegis.research.simmarket import TOKENS
from aegis.storage import schema as s
from aegis.worker import scheduler
from tests.conftest import T0, new_token
from tests.integration.test_worker_cycle import START, _worker

FULL = 300


def _loop(w, clock, hours: float, fast_lane: bool = True):
    """Emulates Worker.run_forever with a ManualClock (same next_wake logic)."""
    end = clock.now() + dt.timedelta(hours=hours)
    next_full = clock.now()
    reports = []
    while clock.now() < end:
        start = clock.now()
        fast = start < next_full
        reports.append(w.run_cycle(fast=fast))
        if not fast:
            next_full = start + dt.timedelta(seconds=FULL)
        if fast_lane:
            wake, _ = w.next_wake(clock.now(), next_full, start)
        else:
            wake = next_full
        clock.set(max(wake, clock.now() + dt.timedelta(seconds=1)))
    return reports


def _held(w):
    with w.engine.connect() as c:
        return [r.token_id for r in c.execute(select(s.positions.c.token_id).where(s.positions.c.closed_at.is_(None)))]


def _gaps(w, token_id, start, end):
    with w.engine.connect() as c:
        ts = [r.observed_at for r in c.execute(select(s.market_snapshots.c.observed_at)
                                                 .where(s.market_snapshots.c.token_id == token_id)
                                                 .order_by(s.market_snapshots.c.observed_at))
              if start <= r.observed_at <= end]
    return [(b - a).total_seconds() for a, b in itertools.pairwise(ts)]


def _without(fn, token):
    def wrapped():
        return [o for o in fn() if o.token_address != token]
    return wrapped


def test_unobserved_position_stays_due_escalates_and_counts_misses(tmp_path):
    clock = ManualClock(START)
    w, gw = _worker(f"sqlite:///{tmp_path}/a.sqlite", clock)
    _loop(w, clock, hours=6, fast_lane=False)
    tid = _held(w)[0]
    with w.engine.connect() as c:
        addr = c.execute(select(s.tokens.c.address).where(s.tokens.c.id == tid)).scalar_one()
    gw.hidden_primary.add(addr)
    gw.fallback_missing.add(addr)  # neither source returns it
    for i in range(1, 4):
        rep = w.run_cycle()
        with w.engine.connect() as c:
            q = c.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == tid)).first()
        assert q.next_due_at <= clock.now(), "a missed position must stay overdue, never be postponed"
        assert q.consecutive_misses == i and q.priority == 0 and q.reason == "position_missed_refresh"
        assert rep.scheduling["missed_positions"] >= 1
        assert rep.scheduling["position_observation_age_s"]["max"] >= 0
        clock.advance(seconds=FULL)


def test_fallback_source_observes_position_missing_from_primary(tmp_path):
    clock = ManualClock(START)
    w, gw = _worker(f"sqlite:///{tmp_path}/b.sqlite", clock)
    _loop(w, clock, hours=6, fast_lane=False)
    tid = _held(w)[0]
    with w.engine.connect() as c:
        addr = c.execute(select(s.tokens.c.address).where(s.tokens.c.id == tid)).scalar_one()
        before = c.execute(select(s.market_snapshots.c.id).where(s.market_snapshots.c.token_id == tid)).all()
    gw.hidden_primary.add(addr)
    clock.advance(seconds=700)  # make it due
    rep = w.run_cycle()
    with w.engine.connect() as c:
        after = c.execute(select(s.market_snapshots.c.id).where(s.market_snapshots.c.token_id == tid)).all()
        q = c.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == tid)).first()
    assert len(after) == len(before) + 1, "fallback source must provide the observation"
    assert q.consecutive_misses == 0 and q.last_observed_at == clock.now()
    assert rep.scheduling["missed_positions"] == 0


def test_effective_frequency_fast_lane_vs_full_cycle_only(tmp_path):
    """Configured hot-position interval is 180 s. Full cycles only: effective gap 300 s (configured
    interval NOT met). With the fast lane: gaps <= 180 s."""
    window = (START + dt.timedelta(hours=6), START + dt.timedelta(hours=12))  # rocket explosion phase
    results = {}
    for lane in (False, True):
        clock = ManualClock(START)
        w, gw = _worker(f"sqlite:///{tmp_path}/f{int(lane)}.sqlite", clock)
        # isolate the scheduler: discovery no longer returns the rocket, only scheduled refreshes do
        _loop(w, clock, hours=5, fast_lane=lane)
        with w.engine.connect() as c:
            rocket = c.execute(select(s.tokens.c.id).where(s.tokens.c.address == TOKENS["rocket"])).scalar_one()
        assert rocket in _held(w)
        gw.discover = _without(gw.discover, TOKENS["rocket"])  # type: ignore[method-assign]
        reps = _loop(w, clock, hours=7, fast_lane=lane)
        results[lane] = (_gaps(w, rocket, *window), reps)
    slow_gaps, _ = results[False]
    fast_gaps, fast_reps = results[True]
    assert max(slow_gaps) >= 300  # configured 180 s is not achievable with 300 s cycles only
    assert max(fast_gaps) <= 180 + 1, max(fast_gaps)
    assert any(r.fast for r in fast_reps) and not any(r.buys for r in fast_reps if r.fast)  # fast lane: exits only
    lat = [v["max"] for r in fast_reps for k, v in r.scheduling.get("lateness_s", {}).items() if k == "position_hot"]
    assert lat and max(lat) <= 60  # recorded lateness against the scheduled time


def test_due_order_preserves_position_priority_under_refresh_limit(engine):
    with engine.begin() as c:
        watch = [new_token(c) for _ in range(5)]
        pos = new_token(c)
        for t in watch:  # watchlist items overdue for a long time
            c.execute(insert(s.scheduler_queue).values(token_id=t, next_due_at=T0 - dt.timedelta(hours=5), priority=6,
                                                       interval_seconds=3600, reason="watchlist", consecutive_misses=0))
        c.execute(insert(s.scheduler_queue).values(token_id=pos, next_due_at=T0 - dt.timedelta(seconds=10), priority=1,
                                                   interval_seconds=180, reason="position_hot", consecutive_misses=0))
        due = scheduler.due(c, T0, limit=1)
        assert [d.token_id for d in due] == [pos]
        assert [d.token_id for d in scheduler.due(c, T0, limit=10, max_priority=3)] == [pos]


def test_lateness_is_measured_against_scheduled_time(engine):
    with engine.begin() as c:
        t = new_token(c)
        assert scheduler.observed(c, t, T0, T0, 1, 180, "position_hot") is None
        late = scheduler.observed(c, t, T0 + dt.timedelta(seconds=250), T0 + dt.timedelta(seconds=250), 1, 180,
                                  "position_hot")
        assert late == 70
        q = c.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == t)).first()
        assert q.next_due_at == T0 + dt.timedelta(seconds=250 + 180)  # anchored on the observation


def test_next_wake_respects_floor_and_full_cycle(tmp_path):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/w.sqlite", clock)
    with w.engine.begin() as c:
        t = new_token(c, t=START)
        c.execute(insert(s.scheduler_queue).values(token_id=t, next_due_at=START + dt.timedelta(seconds=20),
                                                   priority=0, interval_seconds=60, reason="position_critical",
                                                   consecutive_misses=0))
    wake, fast = w.next_wake(START, START + dt.timedelta(seconds=FULL), START)
    assert fast and wake == START + dt.timedelta(seconds=60)  # floor: min_cycle_seconds
    # due item (+20 s) before the next full cycle (+30 s) and floor already passed: fast wake at +20 s
    wake, fast = w.next_wake(START, START + dt.timedelta(seconds=30), START - dt.timedelta(seconds=100))
    assert fast and wake == START + dt.timedelta(seconds=20)
    # full cycle comes first
    wake, fast = w.next_wake(START, START + dt.timedelta(seconds=10), START - dt.timedelta(seconds=100))
    assert not fast and wake == START + dt.timedelta(seconds=10)


class _AdvanceEvent:
    """threading.Event stand-in: wait() advances the ManualClock instead of sleeping."""

    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, seconds):
        self.clock.advance(seconds=seconds)
        return False


def _cycle_log(w):
    with w.engine.connect() as c:
        return [(r.details["started_at"], r.details["fast"]) for r in c.execute(
            select(s.system_events.c.details).where(s.system_events.c.kind == "cycle").order_by(s.system_events.c.id))]


def test_replay_uses_exactly_the_operational_schedule(tmp_path):
    """Audit P1 (2nd): the comparator used fixed 30-min steps. Worker.replay now shares step/plan_wake with
    run_forever: same cycle times and same fast/full sequence."""
    c1, c2 = ManualClock(START), ManualClock(START)
    live, _ = _worker(f"sqlite:///{tmp_path}/live.sqlite", c1)
    live.run_forever(FULL, stop=_AdvanceEvent(c1), max_cycles=40)
    rp, _ = _worker(f"sqlite:///{tmp_path}/rp.sqlite", c2)
    rr = rp.replay(c1.now() + dt.timedelta(seconds=1), full_interval_s=FULL)  # include the last live cycle
    assert _cycle_log(live) == _cycle_log(rp) and len(_cycle_log(rp)) == 40
    assert rr.fast_cycles > 0 and rr.representativeness["operational_equivalence"]
    assert rr.representativeness["fast_dynamics_representative"]  # continuous simulated data


def test_replay_respects_data_resolution_and_flags_fast_dynamics(tmp_path):
    clock = ManualClock(START)
    w, gw = _worker(f"sqlite:///{tmp_path}/r300.sqlite", clock)
    gw.data_resolution_seconds = 300  # e.g. 5-minute historical candles
    rr = w.replay(START + dt.timedelta(hours=6), full_interval_s=FULL, data_resolution_s=300)
    starts = [dt.datetime.fromisoformat(t) for t, _ in _cycle_log(w)]
    assert all(int(t.timestamp()) % 300 == 0 for t in starts)  # never wakes between data points
    rep = rr.representativeness
    assert rep["operational_equivalence"] and not rep["fast_dynamics_representative"]
    assert rep["effective_sampling_s"] == 300 and any("NOT" in n for n in rep["notes"])
    with w.engine.connect() as c:  # observations are stamped on the data grid (no look-ahead)
        obs = [r.observed_at for r in c.execute(select(s.market_snapshots.c.observed_at))]
    assert all(int(t.timestamp()) % 300 == 0 for t in obs)


def test_fixed_cadence_is_flagged_not_equivalent(tmp_path):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/fx.sqlite", clock)
    rr = w.replay(START + dt.timedelta(hours=3), cadence="fixed", fixed_step_s=1800)
    assert rr.cycles == 6 and rr.fast_cycles == 0
    assert not rr.representativeness["operational_equivalence"]
    assert not rr.representativeness["fast_dynamics_representative"]
