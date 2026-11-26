"""Risk Management: kill switch, circuit breakers and operating limits.

Controls are evaluated by the worker every cycle BEFORE any order. They are configuration of the
owner (RiskLimits, control_flags) and are never modified by the learning system.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection

from aegis.config import RiskLimits
from aegis.storage import repo
from aegis.storage import schema as s

KILL_SWITCH = "kill_switch"
BUYS_SUSPENDED = "buys_suspended"


@dataclass
class ControlState:
    kill_switch: bool = False
    buys_allowed: bool = True
    sells_allowed: bool = True
    reasons: list[str] = field(default_factory=list)

    def block_buys(self, why: str) -> None:
        self.buys_allowed = False
        self.reasons.append(why)


class RiskControls:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def evaluate(self, conn: Connection, now: dt.datetime, *, fx_time: dt.datetime | None,
                 sol_time: dt.datetime | None, source_failures: dict[str, int], open_positions: int,
                 reconciliation_ok: bool | None) -> ControlState:
        st = ControlState()
        if repo.get_flag(conn, KILL_SWITCH) == "on":
            st.kill_switch = True
            st.buys_allowed = st.sells_allowed = False
            st.reasons.append("kill switch ON")
            return st
        if repo.get_flag(conn, BUYS_SUSPENDED) == "on":
            st.block_buys("buys suspended by owner")
        if fx_time is None or now - fx_time > dt.timedelta(hours=self.limits.max_fx_staleness_hours):
            st.block_buys("EUR/USD reference rate stale or missing")
        if sol_time is None or now - sol_time > dt.timedelta(minutes=self.limits.max_data_staleness_minutes_for_buys):
            st.block_buys("SOL/EUR rate stale or missing")
            st.sells_allowed = False  # cannot price costs/proceeds in EUR
            st.reasons.append("sells paused: SOL/EUR unknown")
        for src, n in source_failures.items():
            if n >= self.limits.max_consecutive_source_failures:
                st.block_buys(f"source {src} failing ({n} consecutive errors)")
        if open_positions >= self.limits.max_open_positions:
            st.block_buys("max open positions reached")
        if reconciliation_ok is False:
            st.kill_switch = True
            st.buys_allowed = st.sells_allowed = False
            st.reasons.append("reconciliation failure")
        return st

    def trip_kill_switch(self, conn: Connection, now: dt.datetime, reason: str, actor: str = "system") -> None:
        repo.set_flag(conn, KILL_SWITCH, "on", now, actor, reason)
        repo.record_event(conn, s.risk_events, now, kind="kill_switch", severity="critical", token_id=None,
                          details={"reason": reason})
