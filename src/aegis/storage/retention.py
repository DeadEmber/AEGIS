"""Retention policy (see docs/COST_ANALYSIS.md §Almacenamiento).

Ledger, orders, executions, positions, lots, disposals, contributions, security assessments,
configuration versions and audit logs are NEVER pruned (financial audit trail).
Market snapshots: full resolution for `snapshot_full_days`, then one per token per hour.
Features: kept `feature_days` (decisions keep their own scores + inputs hash).
HOLD/WATCH/REJECT/WAIT decisions without orders: kept `trivial_decision_days`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select
from sqlalchemy.engine import Connection

from aegis.storage import schema as s


@dataclass(frozen=True)
class RetentionPolicy:
    snapshot_full_days: int = 14
    feature_days: int = 30
    trivial_decision_days: int = 14
    system_event_days: int = 30


def prune(conn: Connection, now: dt.datetime, policy: RetentionPolicy = RetentionPolicy()) -> dict[str, int]:
    out: dict[str, int] = {}
    cut = now - dt.timedelta(days=policy.snapshot_full_days)
    # keep the first snapshot of each (token, hour) older than the cut-off
    hour = func.strftime("%Y-%m-%d %H", s.market_snapshots.c.observed_at) if conn.dialect.name == "sqlite" else \
        func.date_trunc("hour", s.market_snapshots.c.observed_at)
    keep = (select(func.min(s.market_snapshots.c.id)).where(s.market_snapshots.c.observed_at < cut)
            .group_by(s.market_snapshots.c.token_id, hour))
    out["market_snapshots"] = conn.execute(delete(s.market_snapshots).where(and_(
        s.market_snapshots.c.observed_at < cut, s.market_snapshots.c.id.not_in(keep)))).rowcount
    out["features"] = conn.execute(delete(s.features).where(
        s.features.c.as_of < now - dt.timedelta(days=policy.feature_days))).rowcount
    referenced = select(s.orders.c.decision_id).where(s.orders.c.decision_id.is_not(None))
    out["decisions"] = conn.execute(delete(s.decisions).where(and_(
        s.decisions.c.decided_at < now - dt.timedelta(days=policy.trivial_decision_days),
        s.decisions.c.action.in_(["HOLD", "WATCH", "REJECT", "WAIT"]),
        s.decisions.c.id.not_in(referenced)))).rowcount
    out["system_events"] = conn.execute(delete(s.system_events).where(
        s.system_events.c.occurred_at < now - dt.timedelta(days=policy.system_event_days))).rowcount
    return out
