"""Temporal research: retrospective duration labels (with censoring) and a Kaplan-Meier baseline.

LABELS ARE RETROSPECTIVE. They are targets for survival/quantile models and evaluation, never
features available at decision time. An episode whose end has not been observed at `as_of` is
right-censored (it is NOT dropped and NOT assumed to have ended — dropping it would bias durations
downwards).

`empirical_duration` returns a population-level Kaplan-Meier estimate only when enough events have
been observed; otherwise the status is `insufficient_data` and no number is produced. No per-token
temporal prediction exists until a validated model is registered (docs/MODEL_REGISTRY.md).
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from aegis.core.clock import ensure_utc
from aegis.storage import schema as s

HOT = {"EXPANSION", "EXPLOSION", "DISTRIBUTION", "SECOND_WAVE"}

EPISODE_KINDS = (
    "time_to_expansion",  # first seen -> first EXPANSION/EXPLOSION
    "explosion_duration",  # explosion episode start -> end
    "time_to_exhaustion",  # explosion start -> first DISTRIBUTION
    "time_to_collapse",  # explosion start -> first COLLAPSE
    "time_to_recovery",  # COLLAPSE -> first RECOVERY
    "time_to_second_wave",  # end of first explosion -> SECOND_WAVE
    "investment_duration",  # position opened -> closed
    "time_to_capital_recovery",  # position opened -> capital recovered
)


@dataclass(frozen=True)
class Episode:
    token_id: int
    kind: str
    start: dt.datetime
    end: dt.datetime
    censored: bool  # True: the event had not happened by `end` (= as_of or episode end)

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _first_after(trans: list[tuple[dt.datetime, str]], start: dt.datetime, states: set[str],
                 before: dt.datetime | None = None) -> dt.datetime | None:
    for t, st in trans:
        if t > start and st in states and (before is None or t <= before):
            return t
    return None


def episodes(conn: Connection, as_of: dt.datetime) -> list[Episode]:
    as_of = ensure_utc(as_of)
    out: list[Episode] = []
    trans: dict[int, list[tuple[dt.datetime, str]]] = defaultdict(list)
    for r in conn.execute(select(s.token_states.c.token_id, s.token_states.c.valid_from, s.token_states.c.state)
                          .where(s.token_states.c.valid_from <= as_of).order_by(s.token_states.c.valid_from)):
        trans[r.token_id].append((r.valid_from, r.state))
    first_seen = {r.id: r.first_seen_at for r in conn.execute(select(s.tokens.c.id, s.tokens.c.first_seen_at))}

    for tid, tr in trans.items():
        fs = first_seen.get(tid)
        if fs is not None and fs <= as_of:
            hit = _first_after(tr, fs - dt.timedelta(seconds=1), {"EXPANSION", "EXPLOSION"})
            out.append(Episode(tid, "time_to_expansion", fs, hit or as_of, hit is None))
        for t, st in tr:
            if st == "COLLAPSE":
                hit = _first_after(tr, t, {"RECOVERY", "SECOND_WAVE"})
                out.append(Episode(tid, "time_to_recovery", t, hit or as_of, hit is None))

    eps = conn.execute(select(s.explosion_events).where(s.explosion_events.c.started_at <= as_of)
                       .order_by(s.explosion_events.c.token_id, s.explosion_events.c.started_at)).all()
    first_end: dict[int, dt.datetime] = {}
    for e in eps:
        ended = e.ended_at if e.ended_at is not None and e.ended_at <= as_of else None
        out.append(Episode(e.token_id, "explosion_duration", e.started_at, ended or as_of, ended is None))
        tr = trans.get(e.token_id, [])
        for kind, states in (("time_to_exhaustion", {"DISTRIBUTION"}), ("time_to_collapse", {"COLLAPSE"})):
            hit = _first_after(tr, e.started_at, states)
            out.append(Episode(e.token_id, kind, e.started_at, hit or as_of, hit is None))
        if e.wave_number == 1 and ended is not None:
            first_end[e.token_id] = ended
    for tid, end in first_end.items():
        hit = _first_after(trans.get(tid, []), end, {"SECOND_WAVE"})
        out.append(Episode(tid, "time_to_second_wave", end, hit or as_of, hit is None))

    for p in conn.execute(select(s.positions).where(s.positions.c.opened_at <= as_of)):
        closed = p.closed_at if p.closed_at is not None and p.closed_at <= as_of else None
        out.append(Episode(p.token_id, "investment_duration", p.opened_at, closed or as_of, closed is None))
        rec = p.capital_recovered_at if p.capital_recovered_at is not None and p.capital_recovered_at <= as_of else None
        out.append(Episode(p.token_id, "time_to_capital_recovery", p.opened_at, rec or as_of, rec is None))
    return out


@dataclass(frozen=True)
class KMResult:
    times: list[float]
    survival: list[float]
    variances: list[float]  # Greenwood variance of S(t)
    n: int
    n_events: int

    def quantile_time(self, q: float) -> float | None:
        """Smallest t with S(t) <= 1 - q (q=0.5 -> median)."""
        target = 1 - q
        for t, sv in zip(self.times, self.survival, strict=True):
            if sv <= target + 1e-12:
                return t
        return None

    def median_ci(self, z: float = 1.96) -> tuple[float | None, float | None]:
        """Brookmeyer-Crowley style interval: times where |S(t) - 0.5| <= z * se(t)."""
        inside = [t for t, sv, v in zip(self.times, self.survival, self.variances, strict=True)
                  if abs(sv - 0.5) <= z * math.sqrt(max(v, 0.0))]
        if not inside:
            return None, None
        return min(inside), max(inside)


def kaplan_meier(durations: list[float], observed: list[bool]) -> KMResult:
    data = sorted(zip(durations, observed, strict=True))
    n = len(data)
    at_risk = n
    s_val = 1.0
    gw = 0.0
    times: list[float] = []
    surv: list[float] = []
    var: list[float] = []
    i = 0
    while i < n:
        t = data[i][0]
        d = c = 0
        while i < n and data[i][0] == t:
            if data[i][1]:
                d += 1
            else:
                c += 1
            i += 1
        if d > 0 and at_risk > 0:
            s_val *= 1 - d / at_risk
            if at_risk - d > 0:
                gw += d / (at_risk * (at_risk - d))
            times.append(t)
            surv.append(s_val)
            var.append(s_val * s_val * gw)
        at_risk -= d + c
    return KMResult(times, surv, var, n, sum(1 for _, o in data if o))


@dataclass(frozen=True)
class TemporalEstimate:
    kind: str
    status: str  # unavailable | insufficient_data | empirical_baseline | model
    median_hours: float | None = None
    ci_low_hours: float | None = None
    ci_high_hours: float | None = None
    p25_hours: float | None = None
    p75_hours: float | None = None
    n: int = 0
    n_events: int = 0
    note: str = ""


def empirical_duration(eps: list[Episode], kind: str, min_events: int = 30) -> TemporalEstimate:
    sel = [e for e in eps if e.kind == kind]
    n_ev = sum(1 for e in sel if not e.censored)
    if not sel:
        return TemporalEstimate(kind, "unavailable", note="no episodes observed")
    if n_ev < min_events:
        return TemporalEstimate(kind, "insufficient_data", n=len(sel), n_events=n_ev,
                                note=f"{n_ev} observed events < {min_events} required; no estimate produced")
    km = kaplan_meier([e.hours for e in sel], [not e.censored for e in sel])
    lo, hi = km.median_ci()
    return TemporalEstimate(kind, "empirical_baseline", km.quantile_time(0.5), lo, hi, km.quantile_time(0.25),
                            km.quantile_time(0.75), km.n, km.n_events,
                            note="population-level Kaplan-Meier (right-censored); not a per-token prediction")
