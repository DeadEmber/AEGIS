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

from sqlalchemy import and_, delete, func, select
from sqlalchemy.engine import Connection

from aegis.storage import schema as s
from aegis.storage.archive import archived_ids

EPISODE_STATES = ("DISTRIBUTION", "COLLAPSE", "RECOVERY", "SECOND_WAVE", "EXPLOSION", "EXPANSION")


@dataclass(frozen=True)
class RetentionPolicy:
    snapshot_full_days: int = 14
    feature_days: int = 30
    trivial_decision_days: int = 14
    system_event_days: int = 30
    episode_pre_hours: int = 48
    episode_post_hours: int = 48


def _delete_ids(conn: Connection, table, ids: list[int]) -> int:  # type: ignore[no-untyped-def]
    for k in range(0, len(ids), 500):
        conn.execute(delete(table).where(table.c.id.in_(ids[k:k + 500])))
    return len(ids)


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
    """Delete only rows whose id is present in an integrity-verified archive batch."""
    out: dict[str, int] = {}
    # ---- market snapshots: thin to hourly outside episode windows
    cut = now - dt.timedelta(days=policy.snapshot_full_days)
    covered = archived_ids(conn, archive_root, "market_snapshots")
    windows = episode_windows(conn, now, policy)
    hour = func.strftime("%Y-%m-%d %H", s.market_snapshots.c.observed_at) if conn.dialect.name == "sqlite" else \
        func.date_trunc("hour", s.market_snapshots.c.observed_at)
    keep = {r[0] for r in conn.execute(select(func.min(s.market_snapshots.c.id))
                                       .where(s.market_snapshots.c.observed_at < cut)
                                       .group_by(s.market_snapshots.c.token_id, hour))}
    protected = not_archived = 0
    to_delete: list[int] = []
    for r in conn.execute(select(s.market_snapshots.c.id, s.market_snapshots.c.token_id, s.market_snapshots.c.observed_at)
                          .where(s.market_snapshots.c.observed_at < cut)):
        if r.id in keep:
            continue
        if any(a <= r.observed_at <= b for a, b in windows.get(r.token_id, [])):
            protected += 1
            continue
        if r.id not in covered:
            not_archived += 1
            continue
        to_delete.append(r.id)
    out["market_snapshots"] = _delete_ids(conn, s.market_snapshots, to_delete)
    out["market_snapshots_protected_episode_rows"] = protected
    out["market_snapshots_not_archived_skipped"] = not_archived

    # ---- features
    fc = archived_ids(conn, archive_root, "features")
    ids = [r.id for r in conn.execute(select(s.features.c.id).where(
        s.features.c.as_of < now - dt.timedelta(days=policy.feature_days)))]
    out["features"] = _delete_ids(conn, s.features, [i for i in ids if i in fc])
    out["features_not_archived_skipped"] = len(ids) - out["features"]

    # ---- trivial decisions (never those referenced by orders)
    dc = archived_ids(conn, archive_root, "decisions")
    referenced = select(s.orders.c.decision_id).where(s.orders.c.decision_id.is_not(None))
    ids = [r.id for r in conn.execute(select(s.decisions.c.id).where(and_(
        s.decisions.c.decided_at < now - dt.timedelta(days=policy.trivial_decision_days),
        s.decisions.c.action.in_(["HOLD", "WATCH", "REJECT", "WAIT"]), s.decisions.c.id.not_in(referenced))))]
    out["decisions"] = _delete_ids(conn, s.decisions, [i for i in ids if i in dc])

    # ---- system events
    ec = archived_ids(conn, archive_root, "system_events")
    ids = [r.id for r in conn.execute(select(s.system_events.c.id).where(
        s.system_events.c.occurred_at < now - dt.timedelta(days=policy.system_event_days)))]
    out["system_events"] = _delete_ids(conn, s.system_events, [i for i in ids if i in ec])
    return out
