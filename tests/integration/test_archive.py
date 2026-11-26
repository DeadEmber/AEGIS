"""Audit P1 — research archive (finding reproduced on 45240ba: prune deleted 128 snapshots, 117 features
and 117 decisions with no copy anywhere)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, insert, select

from aegis.core.clock import ManualClock
from aegis.research.simmarket import TOKENS
from aegis.storage import archive
from aegis.storage import schema as s
from aegis.storage.retention import RetentionPolicy, prune
from tests.integration.test_worker_cycle import START, _run, _worker

AGGRESSIVE = RetentionPolicy(snapshot_full_days=0, feature_days=0, trivial_decision_days=0, system_event_days=0,
                             episode_pre_hours=0, episode_post_hours=0)


def _count(c, table):
    return c.execute(select(func.count()).select_from(getattr(s, table))).scalar_one()


@pytest.fixture
def run(tmp_path):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/a.sqlite", clock)
    _run(w, clock, hours=40)
    return w, clock, tmp_path / "archive"


def test_prune_deletes_nothing_without_verified_archive(run):
    w, clock, root = run
    with w.engine.begin() as c:
        before = {t: _count(c, t) for t in ("market_snapshots", "features", "decisions", "system_events")}
        out = prune(c, clock.now() + dt.timedelta(days=60), root, AGGRESSIVE)
        after = {t: _count(c, t) for t in before}
    assert before == after
    assert out["market_snapshots"] == out["features"] == out["decisions"] == 0
    assert out["market_snapshots_not_archived_skipped"] > 0


def test_archive_is_verified_complete_and_queryable(run):
    w, clock, root = run
    with w.engine.begin() as c:
        res = archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1), provenance={"data_mode": "simulated"})
        counts = {t: _count(c, t) for t in archive.ARCHIVE_TABLES}
        batches = c.execute(select(s.archive_batches)).all()
        snap = c.execute(select(s.market_snapshots).order_by(s.market_snapshots.c.id).limit(1)).first()
    assert res and all(r.status == "verified" for r in res)
    assert all(b.status == "verified" and b.verified_at is not None and len(b.sha256) == 64 for b in batches)
    con = archive.duckdb_connect(root)
    for t, n in counts.items():
        if n:
            assert con.execute(f"select count(*) from {t}").fetchone()[0] == n, t
    # exact decimals, timestamps and provenance preserved
    price, observed = con.execute(f"select price_usd, observed_at from market_snapshots where id={snap.id}").fetchone()
    assert Decimal(price) == snap.price_usd and observed.replace(tzinfo=None) == snap.observed_at.replace(tzinfo=None)
    f = next(root.rglob("market_snapshots_*.parquet"))
    md = pq.read_schema(f).metadata
    assert md[b"aegis_table"] == b"market_snapshots" and md[b"data_mode"] == b"simulated"
    fs = con.execute("select distinct feature_set_version from features").fetchall()
    assert fs and fs[0][0].startswith("fs-")
    # incremental: a second run archives nothing new (no duplicates)
    with w.engine.begin() as c:
        assert archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1)) == []


def test_prune_after_archive_keeps_research_data_in_archive(run):
    w, clock, root = run
    with w.engine.begin() as c:
        archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1))
        total = _count(c, "market_snapshots")
        out = prune(c, clock.now() + dt.timedelta(days=60), root, AGGRESSIVE)
        remaining = _count(c, "market_snapshots")
    assert out["market_snapshots"] > 0 and remaining < total
    con = archive.duckdb_connect(root)
    assert con.execute("select count(*) from market_snapshots").fetchone()[0] == total  # nothing lost


def test_tampered_or_missing_archive_blocks_deletion(run):
    w, clock, root = run
    with w.engine.begin() as c:
        archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1))
        f = next(root.rglob("market_snapshots_*.parquet"))
        tbl = pq.read_table(f)
        pq.write_table(tbl.slice(1), f)  # corrupt: one row missing
        before = _count(c, "market_snapshots")
        prune(c, clock.now() + dt.timedelta(days=60), root, AGGRESSIVE)
        assert _count(c, "market_snapshots") == before
        f.unlink()
        prune(c, clock.now() + dt.timedelta(days=60), root, AGGRESSIVE)
        assert _count(c, "market_snapshots") == before


def test_late_row_inside_archived_range_is_not_pruned(run):
    """A row committed late with an id inside an already-archived range (possible with concurrent writers
    on PostgreSQL) must block deletion of that range until it is reconciled."""
    w, clock, root = run
    with w.engine.begin() as c:
        ids = sorted(r.id for r in c.execute(select(s.system_events.c.id)))
        gap = ids[len(ids) // 2]
        row = dict(c.execute(select(s.system_events).where(s.system_events.c.id == gap)).first()._mapping)
        c.execute(s.system_events.delete().where(s.system_events.c.id == gap))  # id not yet committed at archive time
        archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1))
        c.execute(insert(s.system_events).values(**row))  # the late commit
        prune(c, clock.now() + dt.timedelta(days=60), root, AGGRESSIVE)
        assert c.execute(select(s.system_events.c.id).where(s.system_events.c.id == gap)).first() is not None
        neighbour = ids[len(ids) // 2 - 1]
        assert c.execute(select(s.system_events.c.id).where(s.system_events.c.id == neighbour)).first() is not None


def test_episode_windows_keep_full_resolution(run):
    w, clock, root = run
    pol = RetentionPolicy(snapshot_full_days=0, feature_days=30, trivial_decision_days=30, system_event_days=30)
    with w.engine.begin() as c:
        rocket = c.execute(select(s.tokens.c.id).where(s.tokens.c.address == TOKENS["rocket"])).scalar_one()
        chop = c.execute(select(s.tokens.c.id).where(s.tokens.c.address == TOKENS["chop"])).scalar_one()
        archive.archive_all(c, root, clock.now() + dt.timedelta(hours=1))
        r_before = c.execute(select(func.count()).where(s.market_snapshots.c.token_id == rocket)).scalar_one()
        c_before = c.execute(select(func.count()).where(s.market_snapshots.c.token_id == chop)).scalar_one()
        out = prune(c, clock.now() + dt.timedelta(days=1), root, pol)
        r_after = c.execute(select(func.count()).where(s.market_snapshots.c.token_id == rocket)).scalar_one()
        c_after = c.execute(select(func.count()).where(s.market_snapshots.c.token_id == chop)).scalar_one()
    assert out["market_snapshots_protected_episode_rows"] > 0
    assert r_after == r_before  # explosion/collapse episode (+/-48h) kept at full resolution
    assert c_after < c_before  # a token without episodes is thinned to hourly
