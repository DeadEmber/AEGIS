"""Adaptive monitoring scheduler (priority queue persisted in `scheduler_queue`).

Refresh frequency depends on capital exposed, market state and risk; cold tokens are visited
rarely so thousands can be tracked within free-tier rate limits (see COST_ANALYSIS.md).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection

from aegis.config import MonitoringConfig
from aegis.state.machine import HOT_STATES, MarketState
from aegis.storage import schema as s


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


def schedule(conn: Connection, token_id: int, now: dt.datetime, priority: int, interval: int, reason: str) -> None:
    due = now + dt.timedelta(seconds=interval)
    if conn.execute(select(s.scheduler_queue.c.token_id).where(s.scheduler_queue.c.token_id == token_id)).first():
        conn.execute(update(s.scheduler_queue).where(s.scheduler_queue.c.token_id == token_id).values(
            next_due_at=due, priority=priority, interval_seconds=interval, reason=reason))
    else:
        conn.execute(insert(s.scheduler_queue).values(token_id=token_id, next_due_at=due, priority=priority,
                                                      interval_seconds=interval, reason=reason))


def due_pools(conn: Connection, now: dt.datetime, limit: int) -> list[tuple[int, str]]:
    rows = conn.execute(
        select(s.scheduler_queue.c.token_id, s.pools.c.address)
        .join(s.pools, and_(s.pools.c.token_id == s.scheduler_queue.c.token_id, s.pools.c.is_primary.is_(True)))
        .where(s.scheduler_queue.c.next_due_at <= now)
        .order_by(s.scheduler_queue.c.priority, s.scheduler_queue.c.next_due_at)
        .limit(limit)
    )
    return [(int(r.token_id), r.address) for r in rows]
