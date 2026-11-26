"""Fast event-driven historical simulator (research tier of ADR-18).

Replays every point-in-time observation of a `HistoricalStore` in time order and lets an entry and an
exit policy act on it. Orders go through the PRODUCTION `SimulatedExecutionEngine` and are booked by the
PRODUCTION `PortfolioService`/`Ledger` (double entry, FIFO lots, settlement, deposits, reserves,
reconciliation) on a throwaway in-memory database. Only the market interface differs from the worker:
fills come from the exact constant-product state of the pool (`history.markets.swap`) at decision time +
latency — trade-level state when the dataset has it (Launch Tape), otherwise the last observation.

What this tier does NOT do (use `Worker.replay` + `HistoricalGateway` for that): discovery of the live
universe, security assessments, scheduler cadence, shadow portfolio. Results are research evidence for a
given dataset/period, never a promise of future returns.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from aegis.accounting.ledger import Account
from aegis.accounting.portfolio import PortfolioService, PositionValuation
from aegis.allocation.engine import AllocationEngine
from aegis.config import ExecutionMode, RiskLimits, StrategyConfig
from aegis.core.assets import WSOL_MINT
from aegis.core.clock import ManualClock
from aegis.core.hashing import stable_hash
from aegis.core.money import ONE, ZERO, q_eur, q_qty, sol_to_lamports
from aegis.execution.simulator import SimulatedExecutionEngine, TokenRef
from aegis.execution.types import MarketContext, SwapQuote
from aegis.history.fx import EcbTable
from aegis.history.markets import FEE_SCHEDULES, PUMPSWAP_2026_10, MarketType, PoolState, UnsupportedMarket, swap
from aegis.history.store import HistoricalStore, MarketView, Obs, TokenTrack, to_pool_observation, ts
from aegis.research.policies import Context, EntryPolicy, ExitAction, ExitPolicy, Held
from aegis.storage import repo
from aegis.storage import schema as s
from aegis.storage.db import create_all, make_engine

DAY = 86400.0


@dataclass
class BacktestConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    limits: RiskLimits = field(default_factory=RiskLimits)
    fee_schedule: str = PUMPSWAP_2026_10.name
    latency_s: int = 2
    adverse_bps: int = 100  # paper haircut on every fill (same default as the production simulator)
    rent_lamports: int = 1_488_440  # measured 2026-11-26 for a 165-byte account; recoverable on close
    start: float | None = None
    end: float | None = None
    equity_every_s: int = 3600
    label: str = ""
    regime_rate_threshold: float = 0.05  # contextual recycling: favourable if opportunity rate >= this

    def fingerprint(self) -> str:
        return stable_hash({"strategy": self.strategy.model_dump(mode="json"), "fee": self.fee_schedule,
                            "latency": self.latency_s, "adverse": self.adverse_bps, "rent": self.rent_lamports,
                            "start": self.start, "end": self.end})


@dataclass
class ClosedPosition:
    mint: str
    opened_at: float
    closed_at: float
    invested: float
    realized: float  # net sale proceeds + refunded rent deposit (EUR actually returned to cash)
    multiple: float  # realized / invested
    peak_multiple: float  # max net liquidable multiple observed while held (point-in-time, deposit excluded)
    entry_since_grad: float | None
    exits: list[str]


@dataclass
class BacktestResult:
    label: str
    dataset: str
    revision: str
    fingerprint: str
    period: tuple[str, str]
    metrics: dict[str, Any]
    equity: list[tuple[str, float, float, float]]  # (time, nw_liquidable, cash, contributions)
    closed: list[ClosedPosition]
    open_end: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    rejections: dict[str, int]
    warnings: list[str]

    def summary(self) -> dict[str, Any]:
        return {"label": self.label, "dataset": f"{self.dataset}@{self.revision[:8]}", "period": self.period,
                **self.metrics}


class Backtester:
    def __init__(self, store: HistoricalStore, cfg: BacktestConfig, entry: EntryPolicy, exit_policy: ExitPolicy,
                 fx: EcbTable | None = None):
        ex = cfg.strategy.execution.model_copy(update={
            "simulated_latency_seconds": cfg.latency_s,
            "estimated_pool_fee_bps": FEE_SCHEDULES[cfg.fee_schedule].bps(None),  # worst tier for the cost check
        })
        self.strategy = cfg.strategy.model_copy(update={"execution": ex})
        self.cfg = cfg
        self.store = store
        self.entry = entry
        self.exit = exit_policy
        self.fx = fx or EcbTable.bundled()
        self.fees = FEE_SCHEDULES[cfg.fee_schedule]
        self.view = MarketView(store)
        self.engine = make_engine("sqlite://")
        create_all(self.engine)
        t0 = cfg.start if cfg.start is not None else store.start
        self.clock = ManualClock(ts(t0))
        self.portfolio = PortfolioService(self.strategy)
        self.execution = SimulatedExecutionEngine(
            self.strategy, ExecutionMode.HISTORICAL_BACKTEST, self.portfolio, self.clock,
            max_staleness_minutes=cfg.limits.max_data_staleness_minutes_for_buys, paper_adverse_bps=cfg.adverse_bps)
        self.alloc = AllocationEngine(self.strategy, cfg.limits)
        self.adverse = Decimal(cfg.adverse_bps) / Decimal(10_000)
        self.token_ids: dict[str, int] = {}
        self.entries: Counter[str] = Counter()
        self.held: dict[str, Held] = {}
        self.closed: list[ClosedPosition] = []
        self.trades: list[dict[str, Any]] = []
        self.rejections: Counter[str] = Counter()
        self.exit_log: dict[int, list[str]] = {}
        self.ctx = Context()
        self._opp: deque[tuple[float, str]] = deque()
        self._opp_seen: set[str] = set()
        self._obs_first: deque[tuple[float, str]] = deque()
        self._seen: set[str] = set()
        self._month: str | None = None
        self._budget_cache: tuple[float, dict[Account, Decimal]] | None = None
        self._sol_usd: float | None = None
        self.equity: list[tuple[str, float, float, float]] = []
        self.idle_cash_eur_s = 0.0
        self.invested_eur_s = 0.0
        self._last_t = t0
        self.warnings: list[str] = []
        self.signals = 0
        self.signalled: set[str] = set()
        self.pool_fees_eur = Decimal(0)
        self.funded: Counter[str] = Counter()  # EUR drawn per cash account by buys
        self._recycled_queue: deque[list[float]] = deque()  # [sale_time, EUR not yet redeployed]
        self.redeploy: list[tuple[float, float]] = []  # (latency_s, EUR) of recycled cash put back to work

    # ------------------------------------------------------------------ market helpers
    def _eur_usd(self, t: float) -> float | None:
        r = self.fx.eur_usd(t)
        return float(r[0]) if r else None

    def _exec_state(self, tr: TokenTrack, o: Obs, t_exec: float) -> PoolState:
        tick = tr.state_at(t_exec)
        if tick is not None and tick[0] >= o.t:  # trade-level state at the fill instant
            cap = None
            if tick[1] > 0 and tr.supply:
                cap = (tick[2] + tick[3]) / tick[1] * tr.supply
            return PoolState(MarketType.CPMM, Decimal(repr(tick[1])), Decimal(repr(tick[2])), Decimal(repr(tick[3])), cap)
        return o.pool_state(tr.supply)

    def _sol_eur(self, o: Obs, eur_usd: float) -> Decimal | None:
        su = o.sol_usd or self._sol_usd
        if su is None:
            return None
        return Decimal(repr(su / eur_usd))

    def _liquidation(self, h: Held, tr: TokenTrack, o: Obs, eur_usd: float) -> Decimal | None:
        """Net EUR receivable selling the whole position at this observation (fees, impact, adverse, network)."""
        sol_eur = self._sol_eur(o, eur_usd)
        if sol_eur is None or h.qty <= 0:
            return None
        try:
            f = swap(o.pool_state(tr.supply), h.qty, sol_in=False, fees=self.fees)
        except UnsupportedMarket:
            return None
        net = self.execution.costs.network_costs(sol_eur, new_account=False, rent_lamports=0)
        return max(ZERO, q_eur(f.out_amount * sol_eur * (ONE - self.adverse) - net.unrecoverable))

    def _market(self, o: Obs, eur_usd: float, sol_eur: Decimal) -> MarketContext:
        pe = Decimal(repr(o.price_usd / eur_usd)) if o.price_usd else None
        le = Decimal(repr(o.liq_usd / eur_usd)) if o.liq_usd else None
        return MarketContext(pe, le, sol_eur, ts(o.t), "amm" if o.market_type == MarketType.CPMM else "unknown")

    def _token_id(self, c: Any, tr: TokenTrack, o: Obs) -> int:
        if tr.mint not in self.token_ids:
            self.token_ids[tr.mint] = repo.upsert_token(c, to_pool_observation(tr, o, self.store.dataset),
                                                        f"historical:{self.store.dataset}")
        return self.token_ids[tr.mint]

    # ------------------------------------------------------------------ allocation
    def _regime(self) -> Decimal:
        if self.strategy.capital.recycling != "contextual":
            return Decimal(1)
        r = self.ctx.opportunity_rate_24h
        return Decimal(1) if r is not None and r >= self.cfg.regime_rate_threshold else Decimal("0.5")

    def _allowance(self, c: Any, now: float) -> dict[Account, Decimal]:
        """Per-account amounts the allocation budget lets buys draw now (cached 60 s, reset after trades)."""
        if self._budget_cache is not None and now - self._budget_cache[0] < 60:
            return self._budget_cache[1]
        b = self.alloc.budget(c, ts(now), self._regime())
        spend = self.portfolio.spendable(c, at=ts(now))
        allow = {Account.CASH_INVESTMENT: min(b.investment_available_today, spend.get(Account.CASH_INVESTMENT, ZERO)),
                 Account.CASH_RECYCLED: min(b.recycled_available_today, spend.get(Account.CASH_RECYCLED, ZERO))}
        self._budget_cache = (now, allow)
        return allow

    # ------------------------------------------------------------------ context
    def _update_context(self, tr: TokenTrack, o: Obs, eur_usd: float, now: float) -> None:
        if tr.mint not in self._seen:
            self._seen.add(tr.mint)
            self._obs_first.append((now, tr.mint))
        if tr.mint not in self._opp_seen and self.entry.base_pass(tr, o, eur_usd)[0]:
            self._opp_seen.add(tr.mint)
            self._opp.append((now, tr.mint))
        while self._opp and self._opp[0][0] < now - DAY:
            self._opp.popleft()
        while self._obs_first and self._obs_first[0][0] < now - DAY:
            self._obs_first.popleft()
        self.ctx.opportunities_24h = len(self._opp)
        self.ctx.observed_24h = len(self._obs_first)

    # ------------------------------------------------------------------ orders
    def _buy(self, tr: TokenTrack, o: Obs, eur_usd: float, now: float, reasons: list[str]) -> None:
        ticket = self.strategy.capital.ticket_eur
        sol_eur = self._sol_eur(o, eur_usd)
        if sol_eur is None:
            self.rejections["sol_price_unknown"] += 1
            return
        with self.engine.begin() as c:
            allowance = self._allowance(c, now)
            if sum(allowance.values(), ZERO) < ticket:
                self.rejections["budget_or_cash"] += 1
                return
            tid = self._token_id(c, tr, o)
            net = self.execution.costs.network_costs(sol_eur, new_account=True, rent_lamports=self.cfg.rent_lamports)
            swap_in = q_eur(ticket - net.deposit - net.unrecoverable) if self.strategy.capital.ticket_includes_costs \
                else q_eur(ticket)
            quote = None
            if swap_in > 0:
                lam = sol_to_lamports(swap_in / sol_eur)
                t_exec = now + self.cfg.latency_s
                try:
                    f = swap(self._exec_state(tr, o, t_exec), Decimal(lam) / Decimal(10**9), sol_in=True, fees=self.fees)
                    out_raw = int(f.out_amount * Decimal(10) ** tr.decimals)
                    quote = SwapQuote("historical_cpmm", WSOL_MINT, tr.mint, lam, out_raw, out_raw, f.price_impact_pct,
                                      ("historical-cpmm",), ts(t_exec), platform_fee_bps=0)
                except UnsupportedMarket as exc:
                    self.rejections[f"unsupported_market:{exc}"] += 1
                    return
            fee_eur = f.fee_in_input * sol_eur if swap_in > 0 else ZERO
            key = f"bt:{tr.mint}:{self.entries[tr.mint]}:BUY"
            res = self.execution.buy(c, token=TokenRef(tid, tr.mint, tr.decimals), bucket="selection", ticket_eur=ticket,
                                     market=self._market(o, eur_usd, sol_eur), idempotency_key=key, quote=quote,
                                     rent_lamports=self.cfg.rent_lamports, allowance=dict(allowance))
            self._budget_cache = None
            if not res.filled:
                self.rejections[f"order:{res.reason.split('_exceeds')[0]}"] += 1
                return
            pos = c.execute(select(s.positions).where(s.positions.c.id == res.position_id)).one()
        self.pool_fees_eur += fee_eur
        for acct, amt in res.funding:
            self.funded[str(acct)] += float(amt)
            if acct == Account.CASH_RECYCLED:
                self._consume_recycled(float(amt), now)
        self.entries[tr.mint] += 1
        self.held[tr.mint] = Held(pos.id, tid, tr.mint, now, pos.invested_eur_total, pos.realized_proceeds_eur, pos.qty,
                                  pos.qty, bool(pos.capital_recovered), pos.status, pos.deposit_eur, o.since_grad_s,
                                  last_obs_t=o.t, peak_price_usd=o.price_usd or 0.0)
        self.exit_log[pos.id] = []
        self.trades.append({"t": ts(now).isoformat(), "mint": tr.mint, "side": "BUY", "eur": str(res.buy.cash_out_eur),
                            "since_grad_s": o.since_grad_s, "reason": reasons[0] if reasons else ""})

    def _sell(self, tr: TokenTrack, h: Held, o: Obs, eur_usd: float, now: float, act: ExitAction) -> None:
        sol_eur = self._sol_eur(o, eur_usd)
        if sol_eur is None:
            return
        frac = min(ONE, act.fraction)
        qty = h.qty if frac >= ONE else q_qty(h.qty * frac, tr.decimals)
        if qty <= 0:
            return
        t_exec = now + self.cfg.latency_s
        try:
            f = swap(self._exec_state(tr, o, t_exec), qty, sol_in=False, fees=self.fees)
        except UnsupportedMarket:
            self.rejections["sell_unsupported_market"] += 1
            return
        in_raw = int(qty * Decimal(10) ** tr.decimals)
        out_lam = sol_to_lamports(f.out_amount)
        quote = SwapQuote("historical_cpmm", tr.mint, WSOL_MINT, in_raw, out_lam, out_lam, f.price_impact_pct,
                          ("historical-cpmm",), ts(t_exec))
        with self.engine.begin() as c:
            key = f"bt:{tr.mint}:{h.position_id}:{h.partials}:{now:.3f}:SELL"
            res = self.execution.sell(c, token=TokenRef(h.token_id, tr.mint, tr.decimals), position_id=h.position_id,
                                      fraction=frac, market=self._market(o, eur_usd, sol_eur), idempotency_key=key,
                                      quote=quote, critical=act.critical)
            if not res.filled:
                self.rejections[f"sell:{res.reason.split('_exceeds')[0]}"] += 1
                return
            pos = c.execute(select(s.positions).where(s.positions.c.id == h.position_id)).one()
        self._budget_cache = None
        self.pool_fees_eur += f.out_amount * sol_eur * Decimal(f.fee_bps) / Decimal(10_000 - f.fee_bps)
        self._recycled_queue.append([now, float(res.sell.cash_in_eur)])
        self.exit_log[h.position_id].append(act.reason)
        self.trades.append({"t": ts(now).isoformat(), "mint": tr.mint, "side": "SELL", "fraction": str(frac),
                            "eur": str(res.sell.cash_in_eur), "reason": act.reason})
        if pos.closed_at is not None:
            del self.held[tr.mint]
            self.ctx.last_exit[tr.mint] = now
            # cash multiple of the closed ticket: net proceeds + the refunded rent deposit, over the EUR invested
            cash_back = pos.realized_proceeds_eur + h.deposit
            self.closed.append(ClosedPosition(tr.mint, h.opened_at, now, float(pos.invested_eur_total),
                                              float(cash_back), float(cash_back / pos.invested_eur_total),
                                              h.peak_multiple, h.entry_since_grad, self.exit_log[h.position_id]))
        else:
            h.qty, h.realized, h.status = pos.qty, pos.realized_proceeds_eur, pos.status
            h.capital_recovered = bool(pos.capital_recovered)
            h.partials += 1
            h.last_partial_t = now

    def _consume_recycled(self, amount: float, now: float) -> None:
        while amount > 1e-12 and self._recycled_queue:
            head = self._recycled_queue[0]
            take = min(head[1], amount)
            self.redeploy.append((now - head[0], take))
            head[1] -= take
            amount -= take
            if head[1] <= 1e-12:
                self._recycled_queue.popleft()

    # ------------------------------------------------------------------ valuation
    def _valuations(self, now: float, *, haircuts: bool = True) -> dict[int, PositionValuation]:
        out: dict[int, PositionValuation] = {}
        vc = self.strategy.valuation
        for mint, h in self.held.items():
            tr = self.store.tokens[mint]
            o = tr.last(now)
            eur_usd = self._eur_usd(now)
            if o is None or eur_usd is None:
                out[h.position_id] = PositionValuation(h.position_id, None, None, "unvalued", None)
                continue
            liq = self._liquidation(h, tr, o, eur_usd)
            age = now - o.t
            if not haircuts or age <= vc.stale_after_minutes * 60:
                out[h.position_id] = PositionValuation(h.position_id, None, liq, "historical_cpmm", ts(o.t))
            elif age <= vc.unvalued_after_hours * 3600:
                cut = None if liq is None else q_eur(liq * vc.stale_haircut)
                out[h.position_id] = PositionValuation(h.position_id, None, cut, "stale_haircut", ts(o.t), stale=True)
            else:
                out[h.position_id] = PositionValuation(h.position_id, None, None, "unvalued", ts(o.t), stale=True)
        return out

    def _snapshot(self, now: float) -> None:
        with self.engine.connect() as c:
            st = self.portfolio.state(c, ts(now), self._valuations(now))
        self.equity.append((ts(now).isoformat(), float(st.net_worth_liquidable), float(st.cash_total),
                            float(st.contributions_total)))

    def _account_time(self, now: float) -> None:
        dtm = now - self._last_t
        if dtm > 0:
            with self.engine.connect() as c:
                bal = self.portfolio.ledger.balances(c)
            cash = float(bal[Account.CASH_INVESTMENT] + bal[Account.CASH_RECYCLED])
            self.idle_cash_eur_s += cash * dtm
            self.invested_eur_s += float(bal[Account.POSITIONS]) * dtm
        self._last_t = now

    # ------------------------------------------------------------------ main loop
    def run(self) -> BacktestResult:
        cfg = self.cfg
        next_equity = (cfg.start if cfg.start is not None else self.store.start)
        last_t = next_equity
        for t, mint, i in self.store.events(cfg.start, cfg.end):
            self.view.advance(t)
            self.clock.set(ts(t))
            if t >= next_equity:
                self._account_time(t)
                self._snapshot(t)
                next_equity = t + cfg.equity_every_s
            month = ts(t).strftime("%Y-%m")
            if month != self._month:
                with self.engine.begin() as c:
                    self.portfolio.contribute_month(c, ts(t))
                self._month = month
                self._budget_cache = None
            tr = self.store.tokens[mint]
            o = tr.obs[i]
            if o.sol_usd:
                self._sol_usd = o.sol_usd
            eur_usd = self._eur_usd(t)
            if eur_usd is None:
                self.rejections["fx_unknown"] += 1
                continue
            self._update_context(tr, o, eur_usd, t)
            h = self.held.get(mint)
            if h is not None:
                liq = self._liquidation(h, tr, o, eur_usd)
                if liq is None:
                    continue
                m = float((liq + h.realized) / h.invested) if h.invested > 0 else 1.0
                h.peak_multiple = max(h.peak_multiple, m)
                h.last_multiple, h.last_obs_t = m, o.t
                if o.price_usd:
                    h.peak_price_usd = max(h.peak_price_usd, o.price_usd)
                act = self.exit.decide(h, o, m, liq, self.view, eur_usd, t)
                if act is not None:
                    self._sell(tr, h, o, eur_usd, t, act)
            else:
                reasons = self.entry.signal(tr, o, eur_usd, self.ctx, self.entries[mint])
                if reasons:
                    self.signals += 1
                    self.signalled.add(mint)
                    self._buy(tr, o, eur_usd, t, reasons)
            last_t = t
        end = (cfg.end if cfg.end is not None else last_t) + 1
        self._account_time(end)
        self._snapshot(end)
        return self._result(end)

    # ------------------------------------------------------------------ metrics
    def _result(self, end: float) -> BacktestResult:
        with self.engine.connect() as c:
            st = self.portfolio.state(c, ts(end), self._valuations(end))
            st_last = self.portfolio.state(c, ts(end), self._valuations(end, haircuts=False))
            rec = self.portfolio.reconcile(c)
            n_exec = c.execute(select(s.executions.c.pool_fee_eur, s.executions.c.network_fee_eur,
                                      s.executions.c.priority_fee_eur, s.executions.c.price_impact_pct)).fetchall()
        open_end = []
        for mint, h in self.held.items():
            tr = self.store.tokens[mint]
            o = tr.last(end)
            open_end.append({"mint": mint, "invested": float(h.invested), "realized": float(h.realized),
                             "last_multiple": h.last_multiple, "peak_multiple": h.peak_multiple,
                             "last_obs": ts(o.t).isoformat() if o else None,
                             "censored": bool(tr.tracking_end is not None and tr.tracking_end <= end)})
        mult = [p.multiple for p in self.closed]
        peaks = [p.peak_multiple for p in self.closed]
        x2_peak = [p for p in self.closed if p.peak_multiple >= 2]
        all_pos = [(p.multiple, p.peak_multiple) for p in self.closed] + \
                  [((x["last_multiple"] or 0.0), x["peak_multiple"]) for x in open_end]
        capture = [m / pk for m, pk in all_pos if pk > 0]
        censored_value = sum((x["last_multiple"] or 0.0) * x["invested"] - x["realized"] for x in open_end
                             if x["censored"])
        dur = max(1.0, end - (self.cfg.start if self.cfg.start is not None else self.store.start))
        pool_fees = float(self.pool_fees_eur) + sum(float(r.pool_fee_eur) for r in n_exec)
        net_fees = sum(float(r.network_fee_eur + r.priority_fee_eur) for r in n_exec)
        contrib = float(st.contributions_total)
        nw = float(st.net_worth_liquidable)
        metrics = {
            "contributions_eur": round(contrib, 4),
            "net_worth_liquidable_eur": round(nw, 4),
            "net_worth_mark_to_last_eur": round(float(st_last.net_worth_liquidable), 4),
            "net_worth_censored_zero_eur": round(float(st_last.net_worth_liquidable) - max(0.0, censored_value), 4),
            "objective_eur": round(nw - contrib, 4),
            "return_on_contributions": round(nw / contrib - 1, 4) if contrib else None,
            "signals": self.signals,
            "tokens_signalled": len(self.signalled),
            "buys": sum(1 for x in self.trades if x["side"] == "BUY"),
            "sells": sum(1 for x in self.trades if x["side"] == "SELL"),
            "closed_positions": len(self.closed),
            "open_positions_end": len(open_end),
            "censored_open_positions": sum(1 for x in open_end if x["censored"]),
            "win_rate_closed": round(sum(1 for m in mult if m > 1) / len(mult), 4) if mult else None,
            "median_multiple_closed": round(statistics.median(mult), 4) if mult else None,
            "mean_multiple_closed": round(statistics.fmean(mult), 4) if mult else None,
            "median_peak_multiple_closed": round(statistics.median(peaks), 4) if peaks else None,
            "peak_ge_x2": len(x2_peak),
            "peak_ge_x2_realised_ge_x2": sum(1 for p in x2_peak if p.multiple >= 2),
            "median_peak_capture": round(statistics.median(capture), 4) if capture else None,
            "cash_idle_share": round(self.idle_cash_eur_s / (self.idle_cash_eur_s + self.invested_eur_s), 4)
            if self.idle_cash_eur_s + self.invested_eur_s > 0 else None,
            "avg_idle_cash_eur": round(self.idle_cash_eur_s / dur, 4),
            "pool_fees_eur": round(pool_fees, 4),
            "network_fees_eur": round(net_fees, 4),
            "deposits_locked_end_eur": round(float(st.deposits), 4),
            "reconciliation_ok": rec.ok,
            "fee_schedule": self.cfg.fee_schedule,
            "latency_s": self.cfg.latency_s,
            "adverse_bps": self.cfg.adverse_bps,
            "ticket_eur": float(self.strategy.capital.ticket_eur),
            "recycling": self.strategy.capital.recycling,
            "investment_pacing": self.strategy.capital.investment_pacing,
        }
        with self.engine.connect() as c:
            bal = self.portfolio.ledger.balances(c)
        gains = [(p.realized - p.invested) for p in self.closed]
        wins = [g for g in gains if g > 0]
        losses = [-g for g in gains if g < 0]
        pnl_all = sorted(gains + [((x["last_multiple"] or 0.0) * x["invested"] + x["realized"] - x["invested"])
                                  for x in open_end], reverse=True)
        total_pnl = sum(pnl_all)
        bought = sum(self.funded.values())
        red_eur = sum(e for _, e in self.redeploy)
        nw_series = [e[1] - e[3] for e in self.equity]
        peak, mdd = -math.inf, 0.0
        for v in nw_series:
            peak = max(peak, v)
            mdd = max(mdd, peak - v)
        metrics.update({
            "funded_from_investment_eur": round(self.funded.get(str(Account.CASH_INVESTMENT), 0.0), 4),
            "funded_from_recycled_eur": round(self.funded.get(str(Account.CASH_RECYCLED), 0.0), 4),
            "capital_turnover": round(bought / contrib, 4) if contrib else None,
            "recycling_ratio": round(self.funded.get(str(Account.CASH_RECYCLED), 0.0) / bought, 4) if bought else None,
            "reinvestment_latency_h_mean": round(sum(lat * e for lat, e in self.redeploy) / red_eur / 3600, 3)
            if red_eur else None,
            "recycled_never_redeployed_eur": round(sum(x[1] for x in self._recycled_queue), 4),
            "end_cash_investment_eur": round(float(bal[Account.CASH_INVESTMENT]), 4),
            "end_cash_recycled_eur": round(float(bal[Account.CASH_RECYCLED]), 4),
            "end_reserve_profit_eur": round(float(bal[Account.CASH_RESERVE_PROFIT]), 4),
            "end_reserve_tax_eur": round(float(bal[Account.CASH_RESERVE_TAX]), 4),
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else None,
            "avg_win_eur": round(statistics.fmean(wins), 4) if wins else None,
            "avg_loss_eur": round(-statistics.fmean(losses), 4) if losses else None,
            "top5_share_of_gross_gains": round(sum(g for g in pnl_all[:5] if g > 0) / sum(g for g in pnl_all if g > 0), 4)
            if any(g > 0 for g in pnl_all) else None,
            "pnl_without_top1_eur": round(total_pnl - (pnl_all[0] if pnl_all else 0.0), 4),
            "pnl_without_top5_eur": round(total_pnl - sum(pnl_all[:5]), 4),
            "max_drawdown_objective_eur": round(mdd, 4),
        })
        if not rec.ok:
            self.warnings.append("reconciliation failed: " + str({k: v for k, v in rec.checks.items() if not v["ok"]}))
        start = self.cfg.start if self.cfg.start is not None else self.store.start
        return BacktestResult(self.cfg.label or f"{self.entry.name}+{self.exit.name}", self.store.dataset,
                              self.store.revision, self.cfg.fingerprint(),
                              (ts(start).isoformat(), ts(end).isoformat()), metrics, self.equity, self.closed, open_end,
                              self.trades, dict(self.rejections), self.warnings)


def run(store: HistoricalStore, cfg: BacktestConfig, entry: EntryPolicy, exit_policy: ExitPolicy,
        fx: EcbTable | None = None) -> BacktestResult:
    return Backtester(store, cfg, entry, exit_policy, fx).run()


def date_ts(iso: str) -> float:
    """ISO date/datetime -> epoch seconds (naive values are UTC)."""
    d = dt.datetime.fromisoformat(iso)
    return (d if d.tzinfo else d.replace(tzinfo=dt.UTC)).timestamp()
