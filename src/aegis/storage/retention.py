"""Retention of the OPERATIONAL database (see docs/COST_ANALYSIS.md §3).

Never pruned (financial audit trail): ledger, orders, executions, positions, lots, disposals,
contributions, security assessments, configuration versions, audit logs, token states, explosion
events, shadow positions.

Prunable, and ONLY rows already present in a verified research-archive batch (storage/archive.py):
- market_snapshots older than `snapshot_full_days` are thinned to one per token per hour, EXCEPT inside
  episode windows (explosion episodes and DISTRIBUTION/COLLAPSE/RECOVERY/SECOND_WAVE transitions,
  extended by `episode_pre_hours` / `episode_post_hours`), which keep full resolution;
- features older than `feature_days`;
- HOLD/WATCH/REJECT/WAIT decisions without orders older than `trivial_decision_days`;
- system_events older than `system_event_days`.
The archive keeps every row at full resolution, so thinning the operational DB loses nothing.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.engine import Connection

from aegis.storage import schema as s
from aegis.storage.archive import archived_id_ranges

EPISODE_STATES = ("DISTRIBUTION", "COLLAPSE", "RECOVERY", "SECOND_WAVE", "EXPLOSION", "EXPANSION")


@dataclass(frozen=True)
class RetentionPolicy:
    snapshot_full_days: int = 14
    feature_days: int = 30
    trivial_decision_days: int = 14
    system_event_days: int = 30
    episode_pre_hours: int = 48
    episode_post_hours: int = 48


def _in_ranges(col, ranges: list[tuple[int, int]]):  # type: ignore[no-untyped-def]
    if not ranges:
        return col.is_(None) & col.is_not(None)  # always false
    return or_(*[and_(col >= a, col <= b) for a, b in ranges])


Window = tuple[dt.datetime, dt.datetime]


def episode_windows(conn: Connection, now: dt.datetime, policy: RetentionPolicy) -> dict[int, list[Window]]:
    pre, post = dt.timedelta(hours=policy.episode_pre_hours), dt.timedelta(hours=policy.episode_post_hours)
    out: dict[int, list[Window]] = defaultdict(list)
    for e in conn.execute(select(s.explosion_events.c.token_id, s.explosion_events.c.started_at, s.explosion_events.c.ended_at)):
        out[e.token_id].append((e.started_at - pre, (e.ended_at or now) + post))
    for r in conn.execute(select(s.token_states.c.token_id, s.token_states.c.valid_from)
                          .where(s.token_states.c.state.in_(EPISODE_STATES))):
        out[r.token_id].append((r.valid_from - pre, r.valid_from + post))
    return out


def prune(conn: Connection, now: dt.datetime, archive_root: Path, policy: RetentionPolicy = RetentionPolicy()) -> dict[str, int]:
    out: dict[str, int] = {}
    # ---- market snapshots: thin to hourly outside episode windows, only archived rows
    cut = now - dt.timedelta(days=policy.snapshot_full_days)
    ranges = archived_id_ranges(conn, archive_root, "market_snapshots")
    windows = episode_windows(conn, now, policy)
    hour = func.strftime("%Y-%m-%d %H", s.market_snapshots.c.observed_at) if conn.dialect.name == "sqlite" else \
        func.date_trunc("hour", s.market_snapshots.c.observed_at)
    keep = {r[0] for r in conn.execute(select(func.min(s.market_snapshots.c.id))
                                       .where(s.market_snapshots.c.observed_at < cut)
                                       .group_by(s.market_snapshots.c.token_id, hour))}
    cand = conn.execute(select(s.market_snapshots.c.id, s.market_snapshots.c.token_id, s.market_snapshots.c.observed_at)
                        .where(and_(s.market_snapshots.c.observed_at < cut, _in_ranges(s.market_snapshots.c.id, ranges)))).all()
    protected = 0
    to_delete = []
    for r in cand:
        if r.id in keep:
            continue
        if any(a <= r.observed_at <= b for a, b in windows.get(r.token_id, [])):
            protected += 1
            continue
        to_delete.append(r.id)
    for i in range(0, len(to_delete), 500):
        conn.execute(delete(s.market_snapshots).where(s.market_snapshots.c.id.in_(to_delete[i:i + 500])))
    out["market_snapshots"] = len(to_delete)
    out["market_snapshots_protected_episode_rows"] = protected
    out["market_snapshots_not_archived_skipped"] = int(conn.execute(
        select(func.count()).select_from(s.market_snapshots).where(and_(
            s.market_snapshots.c.observed_at < cut, ~_in_ranges(s.market_snapshots.c.id, ranges)))).scalar_one())

    # ---- features
    fr = archived_id_ranges(conn, archive_root, "features")
    out["features"] = conn.execute(delete(s.features).where(and_(
        s.features.c.as_of < now - dt.timedelta(days=policy.feature_days), _in_ranges(s.features.c.id, fr)))).rowcount

    # ---- trivial decisions (never those referenced by orders)
    dr = archived_id_ranges(conn, archive_root, "decisions")
    referenced = select(s.orders.c.decision_id).where(s.orders.c.decision_id.is_not(None))
    out["decisions"] = conn.execute(delete(s.decisions).where(and_(
        s.decisions.c.decided_at < now - dt.timedelta(days=policy.trivial_decision_days),
        s.decisions.c.action.in_(["HOLD", "WATCH", "REJECT", "WAIT"]),
        s.decisions.c.id.not_in(referenced), _in_ranges(s.decisions.c.id, dr)))).rowcount

    # ---- system events
    er = archived_id_ranges(conn, archive_root, "system_events")
    out["system_events"] = conn.execute(delete(s.system_events).where(and_(
        s.system_events.c.occurred_at < now - dt.timedelta(days=policy.system_event_days),
        _in_ranges(s.system_events.c.id, er)))).rowcount
    return out
