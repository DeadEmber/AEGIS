"""Adaptive monitoring scheduler (priority queue persisted in `scheduler_queue`).

Refresh frequency depends on capital exposed, market state and risk; cold tokens are visited rarely
so thousands can be tracked within free-tier rate limits (see COST_ANALYSIS.md).

Guarantees (audit 2026-11-26, see TESTING.md):
- `next_due_at` is anchored on the time the token was actually OBSERVED, never pushed forward for a
  token that was requested but not observed. A missed token stays overdue (so it is requested again
  on the next cycle), its miss counter grows and, if it is a position, its priority escalates.
  Previously an unobserved position was rescheduled +600 s every cycle and was never requested again.
- Configured interval != effective interval. Every refresh records its lateness against the scheduled
  time; the worker's fast lane (Worker.next_wake) wakes at the earliest due high-priority item so
  60/120/180 s intervals are achievable when the full cycle is 300 s.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection

from aegis.config import MonitoringConfig
from aegis.state.machine import HOT_STATES, MarketState
from aegis.storage import schema as s

ESCALATED_PRIORITY = 0


def classify(state: MarketState, has_position: bool, security_block: bool, former: bool,
             cfg: MonitoringConfig) -> tuple[int, int, str]:
    """Returns (priority [0 = most urgent], interval seconds, reason)."""
    if has_position and (state in (MarketState.COLLAPSE, MarketState.UNTRADABLE) or security_block):
        return 0, cfg.critical_risk, "position_critical"
    if has_position and state in HOT_STATES:
        return 1, cfg.position_hot, "position_hot"
    if has_position:
        return 2, cfg.position_stable, "position_stable"
    if state in HOT_STATES:
        return 3, cfg.explosion, "hot_market"
    if state == MarketState.ACCUMULATION:
        return 4, cfg.accumulation, "accumulation"
    if former or state in (MarketState.RECOVERY,):
        return 5, cfg.former_winner, "former_winner"
    if state in (MarketState.ELIGIBLE, MarketState.WATCHLIST, MarketState.DISCOVERED, MarketState.UNKNOWN):
        return 6, cfg.watchlist, "watchlist"
    return 9, cfg.cold, "cold"


@dataclass(frozen=True)
class DueItem:
    token_id: int
    pool_address: str | None
    priority: int
    next_due_at: dt.datetime
    reason: str


def observed(conn: Connection, token_id: int, observed_at: dt.datetime, now: dt.datetime, priority: int,
             interval: int, reason: str) -> int | None:
    """Reschedule after an actual observation. Returns lateness (s) if the token was overdue."""
    row = conn.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id)).first()
    due = observed_at + dt.timedelta(seconds=interval)
    if row is None:
        conn.execute(insert(s.scheduler_queue).values(
            token_id=token_id, next_due_at=due, priority=priority, interval_seconds=interval, reason=reason,
            last_observed_at=observed_at, last_lateness_seconds=None, consecutive_misses=0))
        return None
    lateness = None
    if row.next_due_at <= observed_at:
        lateness = int((observed_at - row.next_due_at).total_seconds())
    conn.execute(update(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id).values(
        next_due_at=due, priority=priority, interval_seconds=interval, reason=reason, last_observed_at=observed_at,
        last_lateness_seconds=lateness if lateness is not None else row.last_lateness_seconds, consecutive_misses=0))
    return lateness


def missed(conn: Connection, token_id: int, now: dt.datetime, *, is_position: bool) -> int:
    """A due token was requested but not observed: keep it overdue, count the miss, escalate positions."""
    row = conn.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id)).first()
    if row is None:
        conn.execute(insert(s.scheduler_queue).values(
            token_id=token_id, next_due_at=now, priority=ESCALATED_PRIORITY if is_position else 6,
            interval_seconds=60, reason="missed_refresh", consecutive_misses=1))
        return 1
    misses = (row.consecutive_misses or 0) + 1
    vals: dict[str, object] = {"consecutive_misses": misses, "next_due_at": min(row.next_due_at, now)}
    if is_position:
        vals.update(priority=ESCALATED_PRIORITY, reason="position_missed_refresh")
    conn.execute(update(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id).values(**vals))
    return misses


def ensure_due(conn: Connection, token_id: int, now: dt.datetime, priority: int, reason: str) -> None:
    """Make sure a token (e.g. an open position) is in the queue; never postpones an existing due time."""
    row = conn.execute(select(s.scheduler_queue.c.next_due_at).where(s.scheduler_queue.c.token_id == token_id)).first()
    if row is None:
        conn.execute(insert(s.scheduler_queue).values(token_id=token_id, next_due_at=now, priority=priority,
                                                      interval_seconds=60, reason=reason, consecutive_misses=0))


def request_now(conn: Connection, token_id: int, now: dt.datetime, priority: int, reason: str) -> None:
    """Make a token due now (e.g. a shadow horizon window is open). Never lowers an existing priority."""
    row = conn.execute(select(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id)).first()
    if row is None:
        conn.execute(insert(s.scheduler_queue).values(token_id=token_id, next_due_at=now, priority=priority,
                                                      interval_seconds=3600, reason=reason, consecutive_misses=0))
    elif row.next_due_at > now:
        conn.execute(update(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id).values(
            next_due_at=now, priority=min(row.priority, priority)))


def due(conn: Connection, now: dt.datetime, limit: int, max_priority: int | None = None) -> list[DueItem]:
    q = (select(s.scheduler_queue.c.token_id, s.scheduler_queue.c.priority, s.scheduler_queue.c.next_due_at,
                s.scheduler_queue.c.reason, s.pools.c.address)
         .outerjoin(s.pools, and_(s.pools.c.token_id == s.scheduler_queue.c.token_id, s.pools.c.is_primary.is_(True)))
         .where(s.scheduler_queue.c.next_due_at <= now)
         .order_by(s.scheduler_queue.c.priority, s.scheduler_queue.c.next_due_at)
         .limit(limit))
    if max_priority is not None:
        q = q.where(s.scheduler_queue.c.priority <= max_priority)
    return [DueItem(int(r.token_id), r.address, int(r.priority), r.next_due_at, r.reason) for r in conn.execute(q)]


def due_pools(conn: Connection, now: dt.datetime, limit: int) -> list[tuple[int, str]]:
    """Backwards-compatible helper: due tokens that have a primary pool."""
    return [(d.token_id, d.pool_address) for d in due(conn, now, limit) if d.pool_address]


def earliest_due(conn: Connection, max_priority: int) -> dt.datetime | None:
    row = conn.execute(select(s.scheduler_queue.c.next_due_at).where(s.scheduler_queue.c.priority <= max_priority)
                       .order_by(s.scheduler_queue.c.next_due_at).limit(1)).first()
    return None if row is None else row.next_due_at


# kept for callers that only need a plain reschedule (tests/tools)
def schedule(conn: Connection, token_id: int, now: dt.datetime, priority: int, interval: int, reason: str) -> None:
    observed(conn, token_id, now, now, priority, interval, reason)
