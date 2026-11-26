"""Persistent worker: one atomic-per-phase cycle of observe -> assess -> decide -> execute (simulated)
-> account -> reconcile. All state lives in the database, so the process can be killed and restarted
at any point without losing or duplicating bookings (idempotency keys + per-phase transactions).
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, and_, insert, select, update
from sqlalchemy.engine import Connection

from aegis import __version__
from aegis.accounting.ledger import Account
from aegis.accounting.portfolio import PortfolioService, PositionValuation
from aegis.allocation.engine import AllocationEngine, market_opportunity_index, regime_multiplier
from aegis.config import ExecutionMode, RiskLimits, Settings, StrategyConfig
from aegis.core import logging as alog
from aegis.core.assets import WSOL_MINT
from aegis.core.clock import Clock, ManualClock, ensure_utc
from aegis.core.hashing import canonical_json
from aegis.core.money import ONE, ZERO, q_eur, sol_to_lamports
from aegis.decision.ecv import EmpiricalBaseline
from aegis.decision.engine import Action, Candidate, Decision, DecisionEngine, PositionView
from aegis.discovery.engine import Eligibility, evaluate_eligibility, primary_pool
from aegis.execution.costs import CostModel
from aegis.execution.simulator import SimulatedExecutionEngine, TokenRef
from aegis.execution.types import MarketContext, SwapQuote
from aegis.features.library import FeatureContext, FeatureVector, compute
from aegis.learning import shadow
from aegis.marketdata.fx import usd_to_eur
from aegis.marketdata.http import SourceError
from aegis.marketdata.models import FxRate, PoolObservation, TokenProfile
from aegis.research import temporal
from aegis.risk.controls import ControlState, RiskControls
from aegis.scoring.heuristics import ScoreSet, score_all
from aegis.security.engine import Evidence, SecurityEngine, SecurityInputs, Status
from aegis.state.machine import ENGINE_VERSION as STATE_VERSION
from aegis.state.machine import MarketState, StateInputs, evaluate
from aegis.storage import repo
from aegis.storage import schema as s
from aegis.worker import scheduler
from aegis.worker.gateway import MarketDataGateway

log = alog.get("aegis.worker")
# States in which an explosion episode is still running (UNKNOWN = missing data, not an ending).
EPISODE_CONTINUES = frozenset({MarketState.EXPLOSION, MarketState.DISTRIBUTION, MarketState.SECOND_WAVE,
                               MarketState.EXPANSION, MarketState.UNKNOWN})


@dataclass
class TokenIntel:
    token_id: int
    address: str
    decimals: int | None
    obs: PoolObservation
    features: FeatureVector
    scores: ScoreSet
    eligibility: Eligibility
    state: MarketState
    security: str
    row: Any


@dataclass
class LoopState:
    next_full: dt.datetime
    last_start: dt.datetime | None = None
    cycles: int = 0
    fast_cycles: int = 0


@dataclass
class CycleReport:
    cycle_id: str
    started_at: dt.datetime
    observations: int = 0
    tokens_observed: int = 0
    new_tokens: int = 0
    security_checks: int = 0
    buys: int = 0
    sells: int = 0
    rejected_orders: int = 0
    decisions: int = 0
    shadows_opened: int = 0
    errors: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    net_worth_eur: str | None = None
    reconciliation_ok: bool | None = None
    duration_s: float = 0.0
    fast: bool = False
    scheduling: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["started_at"] = self.started_at.isoformat()
        return d


@dataclass
class ReplayReport:
    cadence: str
    data_resolution_s: int | None
    cycles: int
    fast_cycles: int
    always_reconciled: bool
    effective_step_s: float | None
    max_hot_lateness_s: int | None
    representativeness: dict[str, Any]

    @classmethod
    def build(cls, cfg: StrategyConfig, reports: list[CycleReport], state: LoopState, cadence: str,
              resolution: int | None, fixed_step_s: int, full_interval_s: int) -> ReplayReport:
        mon = cfg.monitoring
        fastest = min(mon.critical_risk, mon.position_hot, mon.explosion)
        span = (reports[-1].started_at - reports[0].started_at).total_seconds() if len(reports) > 1 else None
        hot = [v["max"] for r in reports for k, v in r.scheduling.get("lateness_s", {}).items()
               if k in ("position_critical", "position_hot", "hot_market")]
        effective_res = fixed_step_s if cadence == "fixed" else max(resolution or 0, mon.min_cycle_seconds)
        notes = []
        if cadence != "adaptive":
            notes.append(f"fixed {fixed_step_s} s cadence: NOT equivalent to the operational worker "
                         f"(full cycle {full_interval_s} s + fast lane >= {mon.min_cycle_seconds} s)")
        if effective_res > fastest:
            notes.append(f"effective sampling {effective_res} s > fastest configured interval {fastest} s: moves "
                         "shorter than a few sampling periods (fast explosions, quick reversals) are NOT "
                         "represented; do not present such results as representative")
        return cls(cadence, resolution, state.cycles, state.fast_cycles, all(bool(r.reconciliation_ok) for r in reports),
                   span / (len(reports) - 1) if span else None, max(hot) if hot else None, {
                       "operational_equivalence": cadence == "adaptive",
                       "fast_dynamics_representative": cadence == "adaptive" and effective_res <= fastest,
                       "effective_sampling_s": effective_res, "fastest_configured_interval_s": fastest, "notes": notes})


class Worker:
    def __init__(self, engine: Engine, settings: Settings, strategy: StrategyConfig, limits: RiskLimits,
                 gateway: MarketDataGateway, clock: Clock):
        strategy.check_against(limits)
        self.engine = engine
        self.settings = settings
        self.cfg = strategy
        self.limits = limits
        self.gw = gateway
        self.clock = clock
        self.mode = settings.execution_mode
        self.portfolio = PortfolioService(strategy)
        self.execution = SimulatedExecutionEngine(strategy, self.mode, self.portfolio, clock,
                                                  max_staleness_minutes=limits.max_data_staleness_minutes_for_buys)
        self.allocation = AllocationEngine(strategy, limits)
        self.decider = DecisionEngine(strategy)
        self.security = SecurityEngine(strategy.security)
        self.risk = RiskControls(limits)
        self.costs = CostModel(strategy.execution)
        self._fx: FxRate | None = None
        self._sol: FxRate | None = None
        self._source_failures: dict[str, int] = defaultdict(int)
        self._pending_meta: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self) -> None:
        now = self.clock.now()
        data_mode = getattr(self.gw, "data_mode", None) or ("simulated" if self.gw.is_simulated else "live")
        with self.engine.begin() as c:
            stored = repo.get_flag(c, "data_mode")
            if stored is not None and stored != data_mode:
                raise RuntimeError(f"database holds {stored} data; refusing to mix with {data_mode} data")
            if stored is None:
                repo.set_flag(c, "data_mode", data_mode, now, "bootstrap", "first run")
            for kind, obj in (("strategy", self.cfg), ("risk_limits", self.limits)):
                h = obj.config_hash if kind == "strategy" else _hash_model(obj)
                if c.execute(select(s.configuration_versions.c.hash).where(s.configuration_versions.c.hash == h)).first() is None:
                    c.execute(insert(s.configuration_versions).values(hash=h, created_at=now, kind=kind, label=self.cfg.name,
                                                                      config=obj.model_dump(mode="json")))
            self._heartbeat(c, "starting", {})

    def _heartbeat(self, c: Connection, status: str, last: dict[str, Any]) -> None:
        now = self.clock.now()
        row = c.execute(select(s.worker_heartbeats).where(s.worker_heartbeats.c.worker_id == self.settings.worker_id)).first()
        if row is None:
            c.execute(insert(s.worker_heartbeats).values(worker_id=self.settings.worker_id, started_at=now, last_beat_at=now,
                                                         cycles=0, status=status, version=__version__, last_cycle=last))
        else:
            c.execute(update(s.worker_heartbeats).where(s.worker_heartbeats.c.worker_id == self.settings.worker_id).values(
                last_beat_at=now, status=status, cycles=row.cycles + (1 if last else 0), last_cycle=last or row.last_cycle,
                version=__version__))

    # ------------------------------------------------------------------ helpers
    def _call(self, report: CycleReport, source: str, fn, *args):  # type: ignore[no-untyped-def]
        try:
            out = fn(*args)
            self._source_failures[source] = 0
            return out
        except SourceError as exc:
            self._source_failures[source] += 1
            report.errors.append(str(exc)[:200])
            return None

    def _rates(self, report: CycleReport) -> None:
        fx = self._call(report, "frankfurter_ecb", self.gw.eur_usd)
        if fx is not None:
            self._fx = fx
        sol = self._call(report, "coingecko", self.gw.sol_eur)
        if sol is not None:
            self._sol = sol
        if self._fx is None or self._sol is None:  # restart: fall back to last stored rates (with their age)
            with self.engine.connect() as c:
                for base, quote, attr in (("EUR", "USD", "_fx"), ("SOL", "EUR", "_sol")):
                    if getattr(self, attr) is None:
                        r = c.execute(select(s.exchange_rates).where(and_(s.exchange_rates.c.base == base,
                                                                          s.exchange_rates.c.quote == quote))
                                      .order_by(s.exchange_rates.c.fetched_at.desc()).limit(1)).first()
                        if r is not None:
                            setattr(self, attr, FxRate(base, quote, r.rate, r.source, r.rate_time, r.fetched_at))

    # ------------------------------------------------------------------ main cycle
    def run_cycle(self, fast: bool = False) -> CycleReport:
        """Full cycle (discovery, security, entries and exits) or fast-lane cycle (due high-priority
        refreshes and exits only)."""
        t0 = time.monotonic()
        now = ensure_utc(self.clock.now())
        cid = now.strftime("%Y%m%dT%H%M%S") + ("f" if fast else "")
        rep = CycleReport(cid, now, fast=fast)
        self._rates(rep)
        eur_usd = self._fx.rate if self._fx else None
        mon = self.cfg.monitoring

        # ---- 1. observe (network, no DB lock)
        discovered = [] if fast else (self._call(rep, "geckoterminal", self.gw.discover) or [])
        with self.engine.connect() as c:
            due = scheduler.due(c, now, mon.max_fast_lane_refreshes if fast else mon.max_pool_refreshes_per_cycle,
                                max_priority=mon.fast_lane_max_priority if fast else None)
            held_ids = {p.token_id for p in self.portfolio.active_positions(c)}
            addr_of = {r.id: r.address for r in c.execute(
                select(s.tokens.c.id, s.tokens.c.address).where(s.tokens.c.id.in_([d.token_id for d in due])))}
        pools = [d.pool_address for d in due if d.pool_address]
        refreshed = (self._call(rep, "geckoterminal", self.gw.refresh, pools) or []) if pools else []
        got = {o.token_address for o in discovered + refreshed}
        # Fallback source for POSITIONS the primary source did not return (pool delisted, partial response...).
        missing_pos = [addr_of[d.token_id] for d in due if d.token_id in held_ids and d.token_id in addr_of
                       and addr_of[d.token_id] not in got]
        fallback = (self._call(rep, "dexscreener", self.gw.refresh_tokens, missing_pos) or []) if missing_pos else []
        observations = [o for o in discovered + refreshed + fallback if o.observed_at <= now + dt.timedelta(minutes=5)]
        rep.observations = len(observations)

        # ---- 2. persist observations + monthly contribution (txn A)
        by_token: dict[str, list[PoolObservation]] = defaultdict(list)
        for o in observations:
            by_token[o.token_address].append(o)
        primary: dict[int, PoolObservation] = {}
        with self.engine.begin() as c:
            self.portfolio.contribute_month(c, now)
            fx_id = repo.store_fx(c, self._fx) if self._fx else None
            if self._sol:
                repo.store_fx(c, self._sol)
            if eur_usd is not None:
                for addr, obs_list in by_token.items():
                    existed = c.execute(select(s.tokens.c.id).where(and_(s.tokens.c.network == "solana",
                                                                         s.tokens.c.address == addr))).first()
                    tid = repo.upsert_token(c, obs_list[0], obs_list[0].source)
                    rep.new_tokens += existed is None
                    pool_ids = {}
                    for o in obs_list:
                        pid = repo.upsert_pool(c, tid, o)
                        pool_ids[o.pool_address] = pid
                        repo.insert_snapshot(c, tid, pid, o, eur_usd, fx_id)
                    best = primary_pool(obs_list, self.cfg.discovery.allow_bonding_curve_pools)
                    if best is not None:
                        repo.set_primary_pool(c, tid, pool_ids[best.pool_address])
                        primary[tid] = best
                        self._update_extremes(c, tid, best, now)
        rep.tokens_observed = len(primary)

        # ---- 3. intelligence (read)
        intel: dict[int, TokenIntel] = {}
        if eur_usd is not None:
            with self.engine.connect() as c:
                for tid, obs in primary.items():
                    intel[tid] = self._intel(c, tid, obs, eur_usd, now)

        # ---- 4. security assessments for the most promising unassessed candidates (network)
        assessed = {} if fast else self._security_pass(intel, now, rep)

        # ---- 5. persist states / features / security (txn B)
        with self.engine.begin() as c:
            for tid, a in assessed.items():
                self._store_assessment(c, tid, a, now)
            for tid in list(intel):
                if tid in assessed:  # re-evaluate with the new security verdict
                    intel[tid] = self._intel(c, tid, intel[tid].obs, eur_usd, now)  # type: ignore[arg-type]
                self._store_state(c, intel[tid], now)
            open_positions = len(self.portfolio.active_positions(c))
            recon_prev = c.execute(select(s.portfolio_snapshots.c.reconciliation_ok)
                                   .order_by(s.portfolio_snapshots.c.as_of.desc()).limit(1)).first()
            ctl = self.risk.evaluate(c, now, fx_time=self._fx.rate_time if self._fx else None,
                                     sol_time=self._sol.fetched_at if self._sol else None,
                                     source_failures=dict(self._source_failures), open_positions=open_positions,
                                     reconciliation_ok=None if recon_prev is None else bool(recon_prev.reconciliation_ok))
        rep.controls = ctl.reasons

        # ---- 6. decide + execute
        if eur_usd is not None and self._sol is not None:
            self._decide_and_execute(intel, ctl, now, cid, rep, eur_usd, entries=not fast)

        # ---- 7. valuation, snapshot, reconciliation, scheduling (txn E)
        with self.engine.begin() as c:
            fresh = {}
            for tid, ti in intel.items():
                pe = usd_to_eur(ti.obs.price_usd, eur_usd) if eur_usd else None
                if pe is not None:
                    fresh[tid] = (pe, ti.obs.liquidity_usd, usd_to_eur(ti.obs.liquidity_usd, eur_usd), ti.obs.observed_at)
            shadow.update_shadows(c, now, fresh, valuer=self._liquidation, cfg=self.cfg.shadow)
            for tid in shadow.checkpoint_tokens(c, now, self.cfg.shadow, self.cfg.shadow.checkpoint_refreshes_per_cycle):
                if tid not in intel:  # a horizon window is open: make sure the token is refreshed
                    scheduler.request_now(c, tid, now, 7, "shadow_checkpoint")
            vals = self._valuations(c, now, eur_usd)
            st = self.portfolio.state(c, now, vals)
            if not fast or rep.buys or rep.sells or not st.reconciliation.ok:  # fast lane: snapshot only on change
                self.portfolio.persist_snapshot(c, st, {"cycle": cid, "mode": str(self.mode),
                                                        "data": getattr(self.gw, "data_mode", None) or
                                                        ("simulated" if self.gw.is_simulated else "live")})
            rep.net_worth_eur = str(q_eur(st.net_worth_liquidable))
            rep.reconciliation_ok = st.reconciliation.ok
            if not st.reconciliation.ok:
                self.risk.trip_kill_switch(c, now, "reconciliation failure: " + canonical_json(
                    {k: v for k, v in st.reconciliation.checks.items() if not v["ok"]}))
            rep.scheduling = self._reschedule(c, intel, due, now)
            self._persist_health(c, now)
            rep.duration_s = round(time.monotonic() - t0, 3)
            repo.record_event(c, s.system_events, now, kind="cycle", severity="info" if not rep.errors else "warn",
                              message=f"cycle {cid}", details=rep.as_dict())
            self._heartbeat(c, "running", rep.as_dict())
        alog.event(log, "cycle_complete", **{k: v for k, v in rep.as_dict().items() if k != "errors"},
                   error_count=len(rep.errors))
        return rep

    # ------------------------------------------------------------------ scheduling
    def _reschedule(self, c: Connection, intel: dict[int, TokenIntel], due: list[Any], now: dt.datetime) -> dict[str, Any]:
        """Anchor next refresh on the actual observation; keep missed items overdue; record lateness/age."""
        held = {p.token_id for p in self.portfolio.active_positions(c)}
        lateness: dict[str, list[int]] = defaultdict(list)
        due_reason = {d.token_id: d.reason for d in due}
        for tid, ti in intel.items():
            pr, iv, why = scheduler.classify(ti.state, tid in held, ti.security == "BLOCK",
                                             bool(ti.row.explosion_count or ti.row.entries), self.cfg.monitoring)
            late = scheduler.observed(c, tid, ti.obs.observed_at, now, pr, iv, why)
            if late is not None and tid in due_reason:
                lateness[due_reason[tid]].append(late)
        missed = [d.token_id for d in due if d.token_id not in intel]
        max_misses = 0
        for tid in missed:
            max_misses = max(max_misses, scheduler.missed(c, tid, now, is_position=tid in held))
        for tid in held:  # positions never fall out of the queue (and are never postponed)
            scheduler.ensure_due(c, tid, now, 0, "position_unqueued")
        ages = []
        for tid in held:
            snap = repo.latest_snapshot(c, tid)
            ages.append(None if snap is None else int((now - snap.observed_at).total_seconds()))
        known = [a for a in ages if a is not None]
        return {
            "due": len(due), "refreshed": len(due) - len(missed), "missed": len(missed),
            "missed_positions": sum(1 for t in missed if t in held), "max_consecutive_misses": max_misses,
            "lateness_s": {k: {"n": len(v), "median": sorted(v)[len(v) // 2], "max": max(v)} for k, v in lateness.items()},
            "position_observation_age_s": {"max": max(known) if known else None,
                                           "unobserved": sum(1 for a in ages if a is None)},
        }

    def next_wake(self, now: dt.datetime, next_full: dt.datetime, last_start: dt.datetime) -> tuple[dt.datetime, bool]:
        """When to run the next cycle and whether it is a fast-lane cycle (never sooner than min_cycle)."""
        mon = self.cfg.monitoring
        floor = last_start + dt.timedelta(seconds=mon.min_cycle_seconds)
        with self.engine.connect() as c:
            earliest = scheduler.earliest_due(c, mon.fast_lane_max_priority)
        if earliest is None or earliest >= next_full:
            return max(next_full, floor), False
        wake = max(earliest, floor)
        if wake >= next_full:
            return max(next_full, floor), False
        return wake, True

    # ------------------------------------------------------------------ pieces
    def _update_extremes(self, c: Connection, tid: int, obs: PoolObservation, now: dt.datetime) -> None:
        row = c.execute(select(s.tokens).where(s.tokens.c.id == tid)).first()
        p = obs.price_usd
        if p is None or p <= 0:
            return
        vals: dict[str, Any] = {}
        if row.ath_price_usd is None or p > row.ath_price_usd:
            vals.update(ath_price_usd=p, ath_at=now, low_since_ath_usd=p)
        elif row.low_since_ath_usd is None or p < row.low_since_ath_usd:
            vals["low_since_ath_usd"] = p
        if vals:
            c.execute(update(s.tokens).where(s.tokens.c.id == tid).values(**vals))

    def _profile_obj(self, row: Any) -> TokenProfile | None:
        if row is None:
            return None
        extra = (row.raw or {}).get("extra", {})
        return TokenProfile(source=row.source, token_address="", observed_at=row.observed_at,
                            holders_count=row.holders_count, top10_pct=row.top10_pct, dev_holding_pct=row.dev_holding_pct,
                            circulating_supply=row.circulating_supply, total_supply=row.total_supply,
                            market_cap_usd=row.market_cap_usd, organic_score=row.organic_score, extra=extra)

    def _intel(self, c: Connection, tid: int, obs: PoolObservation, eur_usd: Decimal, now: dt.datetime) -> TokenIntel:
        row = c.execute(select(s.tokens).where(s.tokens.c.id == tid)).first()
        hist = [h for h in repo.history(c, tid, now) if h.observed_at < obs.observed_at]
        prof = self._profile_obj(repo.latest_profile(c, tid, now))
        as_of = max(now, obs.observed_at)
        open_ep = c.execute(select(s.explosion_events.c.started_at).where(and_(
            s.explosion_events.c.token_id == tid, s.explosion_events.c.ended_at.is_(None)))
            .order_by(s.explosion_events.c.started_at.desc()).limit(1)).first()
        fv = compute(FeatureContext(as_of=as_of, obs=obs, history=hist, profile=prof, token_created_at=row.chain_created_at,
                                    ath_price_usd=row.ath_price_usd, ath_at=row.ath_at,
                                    low_since_ath_usd=row.low_since_ath_usd, first_seen_at=row.first_seen_at,
                                    market_state=row.market_state, market_state_since=row.market_state_since,
                                    active_explosion_started_at=open_ep.started_at if open_ep else None,
                                    explosion_count=row.explosion_count, last_exit_at=row.last_exit_at))
        age_min = (now - obs.observed_at).total_seconds() / 60
        sc = score_all(fv, data_age_minutes=max(0.0, age_min))
        elig = evaluate_eligibility(obs, eur_usd, now, self.cfg.discovery, prof, row.chain_created_at)
        sec = repo.latest_security(c, tid)
        verdict = "UNKNOWN"
        confirmed_block = False
        if sec is not None and sec.expires_at >= now:
            verdict = sec.verdict
            confirmed_block = any(f.get("critical") and f.get("status") == "CRITICAL" and f.get("evidence") == "CONFIRMED"
                                  for f in sec.findings)
        meta = row.meta or {}
        no_route_since = meta.get("no_sell_route_since")
        hours_nr = 0.0
        if no_route_since:
            hours_nr = (now - dt.datetime.fromisoformat(no_route_since)).total_seconds() / 3600
        decision = evaluate(StateInputs(
            features=fv, scores=sc, previous=MarketState(row.market_state), data_available=age_min <= 60,
            security_verdict=verdict, security_confirmed_block=confirmed_block,
            consecutive_no_sell_route=int(meta.get("no_sell_route_count", 0)), hours_without_sell_route=hours_nr,
            eligible=elig.eligible and verdict in ("PASS", "RISK"), explosion_count=row.explosion_count,
        ))
        return TokenIntel(tid, row.address, row.decimals, obs, fv, sc, elig, decision.state, verdict, row)

    def _security_pass(self, intel: dict[int, TokenIntel], now: dt.datetime, rep: CycleReport) -> dict[int, Any]:
        need = []
        with self.engine.connect() as c:
            held = {p.token_id for p in self.portfolio.active_positions(c)}
            for tid, ti in intel.items():
                sec = repo.latest_security(c, tid)
                fresh = sec is not None and sec.expires_at >= now
                if fresh:
                    continue
                if tid in held or (ti.eligibility.eligible and ti.state not in (MarketState.REJECTED, MarketState.UNTRADABLE)):
                    need.append(ti)
        need.sort(key=lambda ti: (ti.token_id not in held, -(ti.scores.epi.value or 0)))
        need = need[: self.cfg.monitoring.security_checks_per_cycle]
        if not need:
            return {}
        addrs = [ti.address for ti in need]
        mints = self._call(rep, "solana_rpc", self.gw.mints, addrs)
        profiles = self._call(rep, "jupiter", self.gw.profiles, addrs) or {}
        out: dict[int, Any] = {}
        sol_eur = self._sol.rate if self._sol else None
        for ti in need:
            mint = (mints or {}).get(ti.address)
            buy_q = sell_q = None
            failed = False
            if sol_eur:
                lam = sol_to_lamports(self.cfg.capital.ticket_eur * Decimal("0.85") / sol_eur)
                try:
                    buy_q = self.gw.quote(WSOL_MINT, ti.address, lam)
                    if buy_q is not None:
                        sell_q = self.gw.quote(ti.address, WSOL_MINT, buy_q.out_amount_raw)
                except SourceError as exc:
                    failed = True
                    rep.errors.append(str(exc)[:200])
            liq_eur = usd_to_eur(ti.obs.liquidity_usd, self._fx.rate) if self._fx else None
            a = self.security.assess(SecurityInputs(
                token_address=ti.address, mint=mint, mint_fetch_failed=mints is None, jup_profile=profiles.get(ti.address),
                pool=ti.obs, liquidity_eur=liq_eur, buy_quote=buy_q, sell_quote=sell_q,
                quotes_attempted=sol_eur is not None, quote_fetch_failed=failed,
                evidence_ceiling=Evidence(c) if (c := getattr(self.gw, "evidence_ceiling", None)) else None,
            ))
            out[ti.token_id] = (a, mint, profiles.get(ti.address))
            rep.security_checks += 1
        return out

    def _store_assessment(self, c: Connection, tid: int, payload: Any, now: dt.datetime) -> None:
        a, mint, prof = payload
        c.execute(insert(s.security_assessments).values(
            token_id=tid, assessed_at=now, expires_at=now + dt.timedelta(minutes=self.cfg.security.assessment_ttl_minutes),
            verdict=str(a.verdict), engine_version=a.engine_version, findings=a.to_json(), inputs_hash=a.inputs_hash))
        row = c.execute(select(s.tokens).where(s.tokens.c.id == tid)).first()
        meta = dict(row.meta or {})
        vals: dict[str, Any] = {}
        route = next((f for f in a.findings if f.check == "sell_route"), None)
        if route is not None and route.status == Status.CRITICAL and route.evidence == Evidence.CONFIRMED:
            meta["no_sell_route_count"] = int(meta.get("no_sell_route_count", 0)) + 1
            meta.setdefault("no_sell_route_since", now.isoformat())
            c.execute(insert(s.security_events).values(token_id=tid, occurred_at=now, kind="no_sell_route",
                                                       status="CONFIRMED", details={}))
        elif route is not None and route.status == Status.PASS:
            meta.pop("no_sell_route_count", None)
            meta.pop("no_sell_route_since", None)
        for f in a.findings:
            if f.status == Status.CRITICAL:
                c.execute(insert(s.security_events).values(token_id=tid, occurred_at=now, kind=f.check,
                                                           status=str(f.evidence), details={"message": f.message}))
        vals["meta"] = meta
        if mint is not None:
            vals.update(decimals=mint.decimals, token_program=mint.program)
        if prof is not None:
            repo.insert_profile(c, tid, prof)
            if prof.created_at is not None and row.chain_created_at is None:
                vals["chain_created_at"] = prof.created_at
            if prof.launchpad:
                vals["launchpad"] = prof.launchpad
        c.execute(update(s.tokens).where(s.tokens.c.id == tid).values(**vals))

    def _store_state(self, c: Connection, ti: TokenIntel, now: dt.datetime) -> None:
        row = c.execute(select(s.tokens.c.market_state, s.tokens.c.explosion_count, s.tokens.c.meta)
                        .where(s.tokens.c.id == ti.token_id)).first()
        meta = dict(row.meta or {})
        meta["eligibility"] = {"eligible": ti.eligibility.eligible, "failed": ti.eligibility.failed}
        meta["scores"] = ti.scores.as_dict()
        vals: dict[str, Any] = {"meta": meta, "universes": ti.eligibility.tags}
        if row.market_state != ti.state:
            c.execute(insert(s.token_states).values(
                token_id=ti.token_id, valid_from=now, state=str(ti.state), previous_state=row.market_state,
                reason=f"{ti.state}", evidence={"scores": ti.scores.as_dict()}, engine_version=STATE_VERSION))
            vals.update(market_state=str(ti.state), market_state_since=now)
            if ti.state in (MarketState.EXPLOSION, MarketState.SECOND_WAVE) and row.market_state not in (
                    "EXPLOSION", "SECOND_WAVE", "DISTRIBUTION"):
                vals["explosion_count"] = row.explosion_count + 1
                # base = lowest price observed in the 24 h before the episode (point-in-time)
                prior = [h.price_usd for h in repo.history(c, ti.token_id, now, hours=24) if h.price_usd]
                base = min([*prior, ti.obs.price_usd]) if ti.obs.price_usd else (min(prior) if prior else ZERO)
                c.execute(insert(s.explosion_events).values(
                    token_id=ti.token_id, started_at=now, wave_number=row.explosion_count + 1,
                    base_price_usd=base, peak_price_usd=ti.obs.price_usd, peak_at=now,
                    peak_multiple=(ti.obs.price_usd / base) if ti.obs.price_usd and base else None,
                    peak_liquidity_usd=ti.obs.liquidity_usd))
        self._track_episode(c, ti, now)
        c.execute(update(s.tokens).where(s.tokens.c.id == ti.token_id).values(**vals))
        # Storage budget: persist feature vectors only when they matter (held, eligible, state change) or hourly.
        last = c.execute(select(s.features.c.as_of).where(s.features.c.token_id == ti.token_id)
                         .order_by(s.features.c.as_of.desc()).limit(1)).first()
        held = self.portfolio.open_position(c, ti.token_id) is not None
        due = last is None or ti.features.as_of - last.as_of >= dt.timedelta(hours=1)
        if held or ti.eligibility.eligible or row.market_state != ti.state or due:
            c.execute(insert(s.features).values(token_id=ti.token_id, as_of=ti.features.as_of,
                                                feature_set_version=ti.features.version,
                                                values={k: round(v, 8) for k, v in ti.features.values.items()},
                                                missing=sorted(ti.features.missing)))

    def _track_episode(self, c: Connection, ti: TokenIntel, now: dt.datetime) -> None:
        """Update the open explosion episode: peak while hot, close it when the move is over."""
        ep = c.execute(select(s.explosion_events).where(and_(
            s.explosion_events.c.token_id == ti.token_id, s.explosion_events.c.ended_at.is_(None)))
            .order_by(s.explosion_events.c.started_at.desc()).limit(1)).first()
        if ep is None:
            return
        vals: dict[str, Any] = {}
        p = ti.obs.price_usd
        if p is not None and (ep.peak_price_usd is None or p > ep.peak_price_usd):
            vals.update(peak_price_usd=p, peak_at=now, peak_liquidity_usd=ti.obs.liquidity_usd,
                        peak_multiple=(p / ep.base_price_usd) if ep.base_price_usd else None)
        if ti.state not in EPISODE_CONTINUES:
            vals["ended_at"] = now
        if vals:
            c.execute(update(s.explosion_events).where(s.explosion_events.c.id == ep.id).values(**vals))

    # ------------------------------------------------------------------ valuation
    def _liquidation(self, qty: Decimal, price_eur: Decimal | None, liq_eur: Decimal | None) -> Decimal | None:
        if price_eur is None or liq_eur is None or price_eur <= 0 or liq_eur <= 0 or self._sol is None:
            return None
        gross = self.costs.estimate_sell(qty, price_eur, liq_eur).out_amount
        net = self.costs.network_costs(self._sol.rate, new_account=False, rent_lamports=0)
        return max(ZERO, q_eur(gross - net.unrecoverable))

    def _valuations(self, c: Connection, now: dt.datetime, eur_usd: Decimal | None) -> dict[int, PositionValuation]:
        out: dict[int, PositionValuation] = {}
        vc = self.cfg.valuation
        for p in self.portfolio.active_positions(c):
            snap = repo.latest_snapshot(c, p.token_id)
            if snap is None or eur_usd is None:
                out[p.id] = PositionValuation(p.id, None, None, "unvalued", None)
                continue
            age = now - snap.observed_at
            price = usd_to_eur(snap.price_usd, eur_usd)
            liq = usd_to_eur(snap.liquidity_usd, eur_usd)
            mark = None if price is None else q_eur(p.qty * price)
            liq_val = self._liquidation(p.qty, price, liq)
            if age > dt.timedelta(hours=vc.unvalued_after_hours):
                out[p.id] = PositionValuation(p.id, mark, None, "unvalued", snap.observed_at, stale=True)
            elif age > dt.timedelta(minutes=vc.stale_after_minutes):
                out[p.id] = PositionValuation(p.id, mark, None if liq_val is None else q_eur(liq_val * vc.stale_haircut),
                                              "stale_haircut", snap.observed_at, stale=True)
            else:
                out[p.id] = PositionValuation(p.id, mark, liq_val, "cpmm_estimate", snap.observed_at)
            if price is not None and age <= dt.timedelta(minutes=vc.stale_after_minutes):
                vals: dict[str, Any] = {"last_mark_eur": mark, "last_liquidation_eur": liq_val, "last_valued_at": now,
                                        "valuation_method": out[p.id].method}
                if p.peak_price_eur is None or price > p.peak_price_eur:
                    vals["peak_price_eur"] = q_eur(price) if price >= Decimal("1e-12") else price
                c.execute(update(s.positions).where(s.positions.c.id == p.id).values(**vals))
        return out

    # ------------------------------------------------------------------ decide & execute
    def _decide_and_execute(self, intel: dict[int, TokenIntel], ctl: ControlState, now: dt.datetime, cid: str,
                            rep: CycleReport, eur_usd: Decimal, entries: bool = True) -> None:
        assert self._sol is not None
        sol_eur = self._sol.rate
        exits: list[tuple[Decision, Any, TokenIntel | None]] = []
        with self.engine.connect() as c:
            self.decider.estimates = EmpiricalBaseline(c, now, temporal.episodes(c, now),
                                                       self.cfg.ecv.min_closed_positions)
            positions = self.portfolio.active_positions(c)
            for p in positions:
                ti = intel.get(p.token_id)
                exits.append((self.decider.evaluate_position(self._position_view(c, p, ti, now, eur_usd), now), p, ti))
            held = {p.token_id for p in positions}
            rets = [v for ti in intel.values() if (v := ti.features.get("ret_h1")) is not None]
            shares = [v for ti in intel.values() if (v := ti.features.get("buyer_share_h1")) is not None]
            moi = market_opportunity_index(rets, shares)
            budget = self.allocation.budget(c, now, regime_multiplier(moi))
            slots = budget.slots if ctl.buys_allowed else {"exploration": 0, "selection": 0, "second_chance": 0}
            cands = [self._candidate(ti, sol_eur, eur_usd) for ti in intel.values()]
            if entries:
                residual = {p.token_id for p in positions if p.status == "RESIDUAL"}
                buys, others = self.decider.plan_entries(cands, slots, now, held_tokens=held,
                                                         seed_material=f"{self.cfg.seed}:{cid}", held_residual=residual)
            else:
                buys, others = [], []

        # Network: fetch executable quotes for planned orders before opening the write transaction.
        quotes: dict[tuple[int, str], SwapQuote | None] = {}
        if ctl.sells_allowed:
            for d, p, ti in exits:
                if ti is None or d.action not in (Action.PARTIAL_SELL, Action.FULL_SELL) or d.method == "burn_close":
                    continue
                qty = p.qty if d.fraction is None or d.fraction >= ONE else p.qty * d.fraction
                raw = int(qty * (Decimal(10) ** (ti.decimals if ti.decimals is not None else 6)))
                quotes[(p.token_id, "SELL")] = self._call(rep, "jupiter", self.gw.quote, ti.address, WSOL_MINT, raw)
        if ctl.buys_allowed:
            for d in buys:
                ti = intel[d.token_id]
                net = self.costs.network_costs(sol_eur, new_account=True, rent_lamports=self.gw.rent_lamports(
                    ti.row.token_program))
                swap_in = self.cfg.capital.ticket_eur - net.deposit - net.unrecoverable
                if swap_in > 0:
                    quotes[(d.token_id, "BUY")] = self._call(rep, "jupiter", self.gw.quote, WSOL_MINT, ti.address,
                                                             sol_to_lamports(swap_in / sol_eur))

        with self.engine.begin() as c:
            for tid, meta in self._pending_meta.items():
                c.execute(update(s.tokens).where(s.tokens.c.id == tid).values(meta=meta))
            self._pending_meta.clear()
            if not ctl.buys_allowed and (buys or others):
                self._decision_row(c, Decision(Action.SUSPEND, 0, ctl.reasons), now, outcome="suspended", token=False)
            if ctl.sells_allowed:
                for d, p, ti in exits:
                    trades = d.action in (Action.PARTIAL_SELL, Action.FULL_SELL) and ti is not None
                    did = self._decision_row(c, d, now, outcome="pending" if trades else "no_order")
                    rep.decisions += 1
                    if not trades:
                        continue
                    tok = TokenRef(p.token_id, ti.address, ti.decimals)
                    key = f"{cid}:{p.id}:{d.action}"
                    if d.method == "burn_close":
                        res = self.execution.burn_close(c, token=tok, position_id=p.id, sol_eur=sol_eur, idempotency_key=key,
                                                        decision_id=did)
                    else:
                        res = self.execution.sell(c, token=tok, position_id=p.id, fraction=d.fraction or ONE,
                                                  market=self._market(ti, eur_usd, sol_eur), idempotency_key=key,
                                                  decision_id=did, quote=quotes.get((p.token_id, "SELL")),
                                                  critical=d.critical)
                    self._outcome(c, did, res.status)
                    if res.filled:
                        rep.sells += 1
                        if res.sale and res.sale.closed:
                            c.execute(update(s.tokens).where(s.tokens.c.id == p.token_id).values(
                                last_exit_at=now, liquidity_at_exit_usd=ti.obs.liquidity_usd))
                        self._shadow_units_sold(c, p, d, res, ti, now)
                    else:
                        rep.rejected_orders += 1
            if ctl.buys_allowed:
                # Each buy may only draw what the allocation budget granted per cash account (recycled cap).
                allowance = {Account.CASH_INVESTMENT: budget.investment_available_today,
                             Account.CASH_RECYCLED: budget.recycled_available_today}
                for d in sorted(buys, key=lambda d: -d.priority):
                    ti = intel[d.token_id]
                    did = self._decision_row(c, d, now)
                    rep.decisions += 1
                    res = self.execution.buy(
                        c, token=TokenRef(d.token_id, ti.address, ti.decimals), bucket=d.bucket or "exploration",
                        ticket_eur=self.cfg.capital.ticket_eur, market=self._market(ti, eur_usd, sol_eur),
                        idempotency_key=f"{cid}:{d.token_id}:BUY", decision_id=did, quote=quotes.get((d.token_id, "BUY")),
                        rent_lamports=self.gw.rent_lamports(ti.row.token_program), allowance=allowance)
                    for acct, amt in res.funding:
                        allowance[acct] -= amt
                    self._outcome(c, did, res.status)
                    if res.filled:
                        rep.buys += 1
                        c.execute(update(s.tokens).where(s.tokens.c.id == d.token_id).values(entries=s.tokens.c.entries + 1))
                    else:
                        rep.rejected_orders += 1
                        shadow.open_shadow(c, token_id=d.token_id, cohort="order_rejected", reason=res.reason, t=now,
                                           price_eur=usd_to_eur(ti.obs.price_usd, eur_usd),
                                           notional_eur=self.cfg.capital.ticket_eur, est_cost_eur=Decimal("0.12"),
                                           liquidity_usd=ti.obs.liquidity_usd, cfg=self.cfg.shadow)
            opened = rejects_logged = 0
            for d in others:
                ti = intel[d.token_id]
                if d.action == Action.REJECT:
                    if rejects_logged >= 50:  # cap audit volume of trivial rejections per cycle
                        continue
                    rejects_logged += 1
                self._decision_row(c, d, now, outcome="recorded")
                rep.decisions += 1
                if d.action == Action.REJECT:
                    cohort = "rejected"
                elif d.scores.get("E3"):  # second-wave signal blocked by an open residual (hypothesis E3)
                    cohort = "reentry_blocked_residual"
                elif ti.row.entries >= 1:  # former position whose re-entry was discarded
                    cohort = "reentry_discarded"
                else:
                    cohort = "not_selected"
                if opened < self.cfg.shadow.max_per_cycle and shadow.open_shadow(
                        c, token_id=d.token_id, cohort=cohort, reason=" ".join(d.reasons)[:300], t=now,
                        price_eur=usd_to_eur(ti.obs.price_usd, eur_usd), notional_eur=self.cfg.capital.ticket_eur,
                        est_cost_eur=Decimal("0.12"), liquidity_usd=ti.obs.liquidity_usd, cfg=self.cfg.shadow):
                    opened += 1
            rep.shadows_opened = opened

    def _shadow_units_sold(self, c: Connection, p: Any, d: Decision, res: Any, ti: TokenIntel, now: dt.datetime) -> None:
        """Counterfactual of the units actually sold (partial or full): what would they be worth later?
        Separate from the ledger: realised proceeds stay in the books, this only measures forgone upside."""
        fill, sale = res.sell, res.sale
        if fill is None or sale is None or fill.token_qty <= 0 or fill.net_proceeds_eur <= 0:
            return  # burn-and-close (no proceeds) has no meaningful counterfactual price
        shadow.open_shadow(
            c, token_id=p.token_id, cohort="after_exit" if sale.closed else "after_partial_sell",
            reason=" ".join(d.reasons), t=fill.executed_at, price_eur=fill.net_proceeds_eur / fill.token_qty,
            notional_eur=fill.net_proceeds_eur, est_cost_eur=ZERO, liquidity_usd=ti.obs.liquidity_usd,
            cfg=self.cfg.shadow, qty=fill.token_qty,
            sale={"kind": "units_sold", "position_id": p.id, "execution_id": res.execution_id,
                  "fraction": d.fraction, "qty_sold": fill.token_qty, "net_proceeds_eur": fill.net_proceeds_eur,
                  "gross_output_eur": fill.swap_output_eur,
                  "costs_eur": fill.network_fee_eur + fill.priority_fee_eur + fill.pool_fee_eur + fill.aggregator_fee_eur,
                  "reference_price_eur": fill.reference_price_eur, "realized_gain_eur": sale.realized_gain,
                  "closed": sale.closed})

    def _position_view(self, c: Connection, p: Any, ti: TokenIntel | None, now: dt.datetime, eur_usd: Decimal) -> PositionView:
        snap = repo.latest_snapshot(c, p.token_id)
        stale = snap is None or now - snap.observed_at > dt.timedelta(minutes=self.cfg.valuation.stale_after_minutes)
        price = usd_to_eur(snap.price_usd, eur_usd) if snap is not None else None
        liq = usd_to_eur(snap.liquidity_usd, eur_usd) if snap is not None else None
        orig = sum((r.qty_initial for r in c.execute(select(s.position_lots.c.qty_initial)
                                                     .where(s.position_lots.c.position_id == p.id))), ZERO)
        last_sell = c.execute(select(s.executions.c.executed_at).join(s.orders, s.orders.c.id == s.executions.c.order_id)
                              .where(and_(s.orders.c.token_id == p.token_id, s.orders.c.side == "SELL"))
                              .order_by(s.executions.c.executed_at.desc()).limit(1)).first()
        tok = c.execute(select(s.tokens.c.meta, s.tokens.c.market_state).where(s.tokens.c.id == p.token_id)).first()
        meta = dict(tok.meta or {})
        critical = liq is not None and liq < self.cfg.exits.critical_liquidity_eur
        dead_since = meta.get("dead_since")
        if critical and not dead_since:
            meta["dead_since"] = now.isoformat()
            dead_since = meta["dead_since"]
        elif not critical and dead_since and not stale:
            meta.pop("dead_since", None)
            dead_since = None
        if meta != (tok.meta or {}):
            self._pending_meta[p.token_id] = meta
        state = ti.state if ti else MarketState(tok.market_state)
        sec = ti.security if ti else "UNKNOWN"
        return PositionView(
            position_id=p.id, token_id=p.token_id, status=p.status, qty=p.qty, original_qty=orig or p.qty,
            cost_basis=p.cost_basis_eur, invested_total=p.invested_eur_total, realized_proceeds=p.realized_proceeds_eur,
            capital_recovered=p.capital_recovered, peak_price_eur=p.peak_price_eur, price_eur=price,
            liquidation_eur=self._liquidation(p.qty, price, liq), liquidity_eur=liq, deposit_eur=p.deposit_eur,
            state=state, security_verdict=sec, features=ti.features if ti else None, scores=ti.scores if ti else None,
            last_partial_at=last_sell.executed_at if last_sell else None,
            dead_since=dt.datetime.fromisoformat(dead_since) if dead_since else None, data_stale=stale,
            opened_at=p.opened_at,
        )

    def _candidate(self, ti: TokenIntel, sol_eur: Decimal, eur_usd: Decimal) -> Candidate:
        price = usd_to_eur(ti.obs.price_usd, eur_usd)
        liq = usd_to_eur(ti.obs.liquidity_usd, eur_usd)
        cost = None
        if price is not None and liq is not None and price > 0 and liq > 0:
            cost = self.costs.roundtrip_cost_pct(self.cfg.capital.ticket_eur, price, liq, sol_eur, 1_488_440)
        return Candidate(ti.token_id, ti.address, ti.state, ti.eligibility, ti.security, ti.features, ti.scores,
                         ti.row.entries, ti.row.last_exit_at, ti.obs.liquidity_usd, ti.row.liquidity_at_exit_usd,
                         ti.row.explosion_count, cost)

    def _market(self, ti: TokenIntel, eur_usd: Decimal, sol_eur: Decimal) -> MarketContext:
        return MarketContext(price_eur=usd_to_eur(ti.obs.price_usd, eur_usd),
                             liquidity_eur=usd_to_eur(ti.obs.liquidity_usd, eur_usd), sol_eur=sol_eur,
                             observed_at=ti.obs.observed_at, pool_type=ti.obs.pool_type)

    def _decision_row(self, c: Connection, d: Decision, now: dt.datetime, outcome: str = "pending",
                      token: bool = True) -> int:
        return int(c.execute(insert(s.decisions).values(
            decided_at=now, token_id=d.token_id if token else None, position_id=d.position_id, action=str(d.action),
            bucket=d.bucket, fraction=None if d.fraction is None else q_eur(d.fraction), reasons=d.reasons,
            scores=d.scores, inputs_hash=d.inputs_hash or "-", strategy_hash=self.cfg.config_hash,
            engine_version="decision-0.2", mode=str(self.mode), outcome=outcome,
        )).inserted_primary_key[0])

    def _outcome(self, c: Connection, did: int, status: str) -> None:
        c.execute(update(s.decisions).where(s.decisions.c.id == did).values(outcome=status.lower()))

    def _persist_health(self, c: Connection, now: dt.datetime) -> None:
        for st in self.gw.health():
            snap = st.snapshot()
            row = c.execute(select(s.source_health.c.source).where(s.source_health.c.source == st.source)).first()
            vals = dict(last_ok_at=snap["last_ok_at"], last_error_at=snap["last_error_at"], last_error=snap["last_error"],
                        consecutive_errors=snap["consecutive_errors"], calls_total=snap["calls"],
                        errors_total=snap["errors"], rate_limited_total=snap["rate_limited"],
                        latency_ms_ewma=snap["latency_ms_ewma"], updated_at=now)
            if row is None:
                c.execute(insert(s.source_health).values(source=st.source, **vals))
            else:
                c.execute(update(s.source_health).where(s.source_health.c.source == st.source).values(**vals))

    # ------------------------------------------------------------------ loop
    # ------------------------------------------------------------------ loop (shared by live and replay)
    def step(self, state: LoopState, full_interval_s: int) -> CycleReport | None:
        """Run ONE cycle at clock.now(): full if the full-cycle time has come, else fast lane."""
        start = self.clock.now()
        fast = start < state.next_full
        rep: CycleReport | None = None
        try:
            rep = self.run_cycle(fast=fast)
        except Exception as exc:  # the loop must survive; the error is recorded, nothing is fabricated
            alog.event(log, "cycle_failed", 40, error=repr(exc)[:500])
            with self.engine.begin() as c:
                repo.record_event(c, s.system_events, self.clock.now(), kind="cycle_failed", severity="error",
                                  message=repr(exc)[:500], details={"fast": fast})
        if not fast:
            state.next_full = start + dt.timedelta(seconds=full_interval_s)
        state.last_start = start
        state.cycles += 1
        state.fast_cycles += fast
        return rep

    def plan_wake(self, state: LoopState, data_resolution_s: int | None = None) -> dt.datetime:
        """Next cycle time from the scheduler (next_wake). With historical data of resolution r, waking
        between grid points cannot see new data, so the wake is rounded UP to the data grid."""
        wake, _ = self.next_wake(self.clock.now(), state.next_full, state.last_start or self.clock.now())
        if data_resolution_s:
            epoch = wake.timestamp()
            q = math.ceil(epoch / data_resolution_s) * data_resolution_s
            wake = dt.datetime.fromtimestamp(q, wake.tzinfo)
        return wake

    def run_forever(self, interval_s: int, stop: threading.Event | None = None, max_cycles: int | None = None) -> None:
        """Full cycle every `interval_s`; fast-lane cycles in between at the earliest due high-priority item."""
        self.bootstrap()
        stop = stop or threading.Event()
        state = LoopState(next_full=self.clock.now())
        while not stop.is_set():
            self.step(state, interval_s)
            if max_cycles is not None and state.cycles >= max_cycles:
                break
            wake = self.plan_wake(state)
            stop.wait(max(1.0, (wake - self.clock.now()).total_seconds()))

    def replay(self, until: dt.datetime, *, full_interval_s: int = 300, cadence: str = "adaptive",
               fixed_step_s: int = 1800, data_resolution_s: int | None = None) -> ReplayReport:
        """Historical / simulated replay with a ManualClock using the SAME scheduling as run_forever
        (cadence='adaptive'). cadence='fixed' is kept for cheap mechanical tests and is flagged as NOT
        equivalent to the operational worker."""
        if not isinstance(self.clock, ManualClock):
            raise TypeError("replay requires a ManualClock")
        state = LoopState(next_full=self.clock.now())
        reports: list[CycleReport] = []
        while self.clock.now() < until:
            rep = self.step(state, full_interval_s)
            if rep is not None:
                reports.append(rep)
            if cadence == "adaptive":
                wake = self.plan_wake(state, data_resolution_s)
            else:
                wake = self.clock.now() + dt.timedelta(seconds=fixed_step_s)
            self.clock.set(max(wake, self.clock.now() + dt.timedelta(seconds=1)))
        return ReplayReport.build(self.cfg, reports, state, cadence, data_resolution_s, fixed_step_s, full_interval_s)


def _hash_model(m: Any) -> str:
    from aegis.core.hashing import stable_hash

    return stable_hash(m.model_dump(mode="json"))


__all__ = ["CycleReport", "ExecutionMode", "Worker"]
