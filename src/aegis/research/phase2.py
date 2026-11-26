"""Phase 2 experiment registry and reproducible report (`aegis research phase2`).

Experiment IDs continue after E1 (ECV), E2 (monetisation) and E3 (residual re-entry):
  E4  empirical base: coverage, latency, reference strategies, production-engine replay (block A)
  E5  liquidity: recycling none / limited / full / contextual, pacing (block B)
  E6  selection: factor ablation and context normalisation (block C)
  E7  supervised baselines and calibration (block C)
  E8  continuation value and the decision agent vs the Champion (block D)
  E9  validation: walk-forward, block bootstrap, multiple comparisons, costs, tickets, portfolios A-I (block E)
  E10 recovery of losers and monetisation rules (stops, time stops, capital recovery)

Every experiment returns plain data (no raw dataset rows: Launch Tape forbids redistribution; derived
results are allowed). The report records dataset revisions, config fingerprints and code version.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aegis import __version__
from aegis.history import coverage
from aegis.history.registry import REGISTRY
from aegis.research import runner

CORPUS_START, CORPUS_END = "2026-06-19", "2026-07-15"
OWNER_REPORTED = {  # owner's external research (hypotheses; method details unknown)
    ("pumpfun_corpus", "basic+trailing"): 122.76,
    ("pumpfun_corpus", "strict+trailing"): 198.94,
    ("pumpfun_corpus", "strict+trailing+context_raw"): 210.72,
}
KEYS = ("contributions_eur", "net_worth_liquidable_eur", "net_worth_mark_to_last_eur", "net_worth_censored_zero_eur",
        "tokens_signalled", "buys", "sells", "win_rate_closed", "median_multiple_closed", "mean_multiple_closed",
        "peak_ge_x2", "peak_ge_x2_realised_ge_x2", "median_peak_capture", "cash_idle_share", "pool_fees_eur",
        "network_fees_eur", "censored_open_positions", "reconciliation_ok")


def _row(r: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    tickets = sum(p.invested for p in r.closed) + sum(p["invested"] for p in r.open_end)
    value = sum(p.realized for p in r.closed) + sum((p["last_multiple"] or 0.0) * p["invested"] for p in r.open_end)
    return {"label": r.label, **{k: r.metrics.get(k) for k in KEYS},
            "mean_return_per_ticket": round(value / tickets - 1, 4) if tickets else None,
            "fingerprint": r.fingerprint[:12], **(extra or {})}


def e4_empirical_base(base: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"datasets": {}, "reference": [], "latency": None, "engine_replay": {}, "cross_check": {}}
    pf = runner.load_store("pumpfun_corpus", str(base), CORPUS_START, CORPUS_END)
    lt = runner.load_store("launchtape", str(base), resolution_s=15.0)
    for st in (pf, lt):
        cov = coverage.coverage(st)
        out["datasets"][st.dataset] = {k: cov[k] for k in (
            "revision", "period_observations", "tokens", "tokens_with_tradable_observations", "tradable_observations",
            "non_tradable_observations", "outcomes_retrospective", "resolution_profile_s", "tracking_hours",
            "first_observation_since_graduation_s", "gaps", "quality")}
    lt5 = runner.load_store("launchtape", str(base), resolution_s=5.0)
    out["latency"] = coverage.latency_study(lt5)
    # independent check: reconstructed prices vs Launch Tape's own +60/+300/+600 s cap outcomes
    ratios: dict[str, list[float]] = {"60": [], "300": [], "600": []}
    for mint, oc in lt5.outcomes.items():
        tr = lt5.tokens.get(mint)
        if not tr or tr.graduated_at is None:
            continue
        for k, lst in ratios.items():
            cap = (oc.values.get("capAt") or {}).get(k)
            o = tr.last(tr.graduated_at + int(k))
            if cap and o is not None and o.price_sol:
                lst.append(o.price_sol * 1e9 / cap)
    out["cross_check"] = {k: {"n": len(v), "median_ratio": round(statistics.median(v), 4) if v else None}
                          for k, v in ratios.items()}
    for st in (pf, lt):
        for preset in ("basic+trailing", "strict+hold", "strict+trailing", "strict+trailing+context_raw",
                       "strict+trailing+context_rate", "strict+aegis_exit"):
            r = runner.run_preset(st, preset)
            out["reference"].append(_row(r, {"dataset": st.dataset, "allocation": "full reinvestment, no pacing",
                                             "owner_reported_eur": OWNER_REPORTED.get((st.dataset, preset))}))
        r = runner.run_preset(pf if st is pf else lt, "strict+trailing", label="strict+trailing (AEGIS 0.2 allocation)",
                              capital={"investment_pacing": True, "recycling": "limited"})
        out["reference"].append(_row(r, {"dataset": st.dataset, "allocation": "paced + recycled 10%/day (0.2 default)"}))
    out["engine_replay"] = _engine_replay(lt)
    return out


def _engine_replay(st: Any) -> dict[str, Any]:
    """The unchanged production worker on Launch Tape: default config, and default minus the 24 h age floor."""
    from decimal import Decimal

    from sqlalchemy import func, select

    from aegis.config import ExecutionMode, RiskLimits, Settings, StrategyConfig
    from aegis.core.clock import ManualClock
    from aegis.history.gateway import HistoricalGateway
    from aegis.storage import schema as s
    from aegis.storage.db import create_all, make_engine
    from aegis.worker.runner import Worker

    res: dict[str, Any] = {}
    for variant in ("default", "no_24h_age_floor"):
        cfg = StrategyConfig()
        if variant != "default":
            cfg.discovery.min_token_age_hours = Decimal(0)
        clock = ManualClock(dt.datetime.fromtimestamp(st.start, dt.UTC))
        eng = make_engine("sqlite://")
        create_all(eng)
        gw = HistoricalGateway(st, clock.now, launchpad_invariants=True)
        w = Worker(eng, Settings(execution_mode=ExecutionMode.HISTORICAL_BACKTEST), cfg, RiskLimits(), gw, clock)
        w.bootstrap()
        rep = w.replay(dt.datetime.fromtimestamp(st.end, dt.UTC), full_interval_s=300, data_resolution_s=15)
        with eng.connect() as c:
            snap = c.execute(select(s.portfolio_snapshots.c.net_worth_liquidable_eur)
                             .order_by(s.portfolio_snapshots.c.as_of.desc()).limit(1)).scalar()
            res[variant] = {
                "cycles": rep.cycles, "always_reconciled": rep.always_reconciled,
                "buys": c.execute(select(func.count()).select_from(s.orders).where(s.orders.c.side == "BUY",
                                                                                  s.orders.c.status == "FILLED")).scalar(),
                "sells": c.execute(select(func.count()).select_from(s.orders).where(s.orders.c.side == "SELL",
                                                                                   s.orders.c.status == "FILLED")).scalar(),
                "security_assessments": c.execute(select(func.count()).select_from(s.security_assessments)).scalar(),
                "net_worth_liquidable_eur": float(snap) if snap is not None else None,
            }
    return res


EXPERIMENTS: dict[str, tuple[str, Callable[[Path], dict[str, Any]]]] = {
    "E4": ("Base empírica: cobertura, latencia, estrategias de referencia y replay del motor", e4_empirical_base),
}


def run(ids: list[str], base: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for eid in ids:
        title, fn = EXPERIMENTS[eid]
        t0 = time.time()
        data = fn(base)
        doc = {"id": eid, "title": title, "aegis_version": __version__,
               "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
               "datasets": {k: {"repo": v.repo, "revision": v.revision} for k, v in REGISTRY.items()},
               "runtime_s": round(time.time() - t0, 1), "data": data}
        (out_dir / f"{eid}.json").write_text(json.dumps(doc, indent=1, default=_json_default))
        results[eid] = doc
    return results


def _json_default(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return str(x)


# ============================================================================ block B
def _pf(base: Path) -> Any:
    return runner.load_store("pumpfun_corpus", str(base), CORPUS_START, CORPUS_END)


def e5_liquidity(base: Path) -> dict[str, Any]:
    """Recycling none / limited / full / contextual x pacing, same selection and exits (single variable)."""
    pf = _pf(base)
    rows = []
    for pacing in (True, False):
        for recycling in ("none", "limited", "full", "contextual"):
            label = f"{'paced' if pacing else 'unpaced'}+{recycling}"
            r = runner.run_preset(pf, "strict+trailing", label=label,
                                  capital={"investment_pacing": pacing, "recycling": recycling})
            rows.append(_row(r, {k: r.metrics.get(k) for k in (
                "funded_from_investment_eur", "funded_from_recycled_eur", "capital_turnover", "recycling_ratio",
                "reinvestment_latency_h_mean", "recycled_never_redeployed_eur", "end_cash_investment_eur",
                "end_cash_recycled_eur", "end_reserve_profit_eur", "end_reserve_tax_eur", "avg_idle_cash_eur",
                "max_drawdown_objective_eur")}))
    return {"selection": "strict+trailing", "period": [CORPUS_START, CORPUS_END], "rows": rows}


# ============================================================================ block C (selection)
def e6_selection(base: Path) -> dict[str, Any]:
    """One-filter-at-a-time ablation around 'strict', add-one around 'basic', and context normalisation."""
    from decimal import Decimal

    from aegis.research import presets

    pf = _pf(base)
    strict, basic = presets.STRICT, presets.BASIC
    drops = {"liquidity>=20k->10k": {"min_liquidity_eur": Decimal(10000)},
             "no_mcap_range": {"min_market_cap_eur": None, "max_market_cap_eur": None},
             "no_volume_m5": {"min_volume_m5_usd": None}, "no_buy_sell_ratio": {"min_buy_sell_ratio_m5": None},
             "no_price_change_m5": {"min_price_change_m5_pct": None},
             "window_to_6h": {"max_seconds_since_graduation": 6 * 3600}}
    adds = {"basic+liquidity20k": {"min_liquidity_eur": Decimal(20000)},
            "basic+volume_m5": {"min_volume_m5_usd": Decimal(1500)},
            "basic+buy_sell_ratio": {"min_buy_sell_ratio_m5": Decimal("1.75")},
            "basic+price_change_m5": {"min_price_change_m5_pct": Decimal(0)}}
    rows = [_row(runner.run_preset(pf, "strict+trailing", label="strict (reference)"), {"family": "reference"}),
            _row(runner.run_preset(pf, "basic+trailing", label="basic (reference)"), {"family": "reference"})]
    for name, upd in drops.items():
        rows.append(_row(runner.run_preset(pf, "strict+trailing", entry=strict.model_copy(update=upd), label=f"strict-{name}"),
                         {"family": "drop_one"}))
    for name, upd in adds.items():
        rows.append(_row(runner.run_preset(pf, "basic+trailing", entry=basic.model_copy(update=upd), label=name),
                         {"family": "add_one"}))
    for n in (10, 20, 40):
        rows.append(_row(runner.run_preset(pf, "strict+trailing", label=f"context_raw>={n}",
                                           entry=strict.model_copy(update={"context_min_opportunities_24h": n})),
                         {"family": "context_raw"}))
    for rate in ("0.01", "0.02", "0.05", "0.10"):
        rows.append(_row(runner.run_preset(pf, "strict+trailing", label=f"context_rate>={rate}",
                                           entry=strict.model_copy(update={"context_min_opportunity_rate_24h": Decimal(rate)})),
                         {"family": "context_rate"}))
    return {"period": [CORPUS_START, CORPUS_END], "rows": rows, "coverage_vs_context": _coverage_context(pf)}


def _coverage_context(st: Any) -> dict[str, Any]:
    """Is the raw 'opportunities in 24 h' count driven by dataset coverage? Daily correlation with graduations."""
    from aegis.research.policies import PostGradEntry
    from aegis.research.presets import STRICT

    gate = PostGradEntry(STRICT)
    grads: dict[str, int] = {}
    opps: dict[str, int] = {}
    for tr in st.tokens.values():
        if tr.graduated_at is None:
            continue
        d = dt.datetime.fromtimestamp(tr.graduated_at, dt.UTC).date().isoformat()
        grads[d] = grads.get(d, 0) + 1
        eur = 1.16
        if any(gate.base_pass(tr, o, eur)[0] for o in tr.obs[:80]):
            opps[d] = opps.get(d, 0) + 1
    days = sorted(grads)
    g = [grads[d] for d in days]
    o = [opps.get(d, 0) for d in days]
    rate = [b / a if a else 0.0 for a, b in zip(g, o, strict=True)]
    corr = float(statistics.correlation(g, o)) if len(days) > 2 else None
    corr_rate = float(statistics.correlation(g, rate)) if len(days) > 2 else None
    return {"days": days, "graduations": g, "strict_opportunities": o, "opportunity_rate": [round(x, 4) for x in rate],
            "corr_graduations_vs_raw_count": round(corr, 4) if corr is not None else None,
            "corr_graduations_vs_rate": round(corr_rate, 4) if corr_rate is not None else None}


EXPERIMENTS.update({
    "E5": ("Liquidez: reinversión none/limited/full/contextual × pacing (variable única)", e5_liquidity),
    "E6": ("Selección: ablación de factores y normalización del contexto por cobertura", e6_selection),
})


# ============================================================================ blocks C/D/E (walk-forward)
_WF: dict[str, Any] = {}


def _context_fn(st: Any, gate: Any) -> Callable[[float], tuple[float | None, int | None]]:
    """Point-in-time 24 h opportunity count / coverage, identical to the simulator's Context."""
    import bisect

    from aegis.history.fx import EcbTable

    fx = EcbTable.bundled()
    first_obs, first_opp = [], []
    for tr in st.tokens.values():
        if not tr.obs:
            continue
        first_obs.append(tr.obs[0].t)
        for o in tr.obs[:120]:
            r = fx.eur_usd(o.t)
            if r and gate.base_pass(tr, o, float(r[0]))[0]:
                first_opp.append(o.t)
                break
    first_obs.sort()
    first_opp.sort()

    def ctx(t: float) -> tuple[float | None, int | None]:
        n_obs = bisect.bisect_right(first_obs, t) - bisect.bisect_left(first_obs, t - 86400)
        n_opp = bisect.bisect_right(first_opp, t) - bisect.bisect_left(first_opp, t - 86400)
        return (n_opp / n_obs if n_obs else None), n_obs

    return ctx


def _eur(t: float) -> float:
    from aegis.history.fx import EcbTable

    r = EcbTable.bundled().eur_usd(t)
    return float(r[0]) if r else 1.16


def walk_forward_models(base: Path) -> dict[str, Any]:
    """Train (once, cached) the walk-forward entry and continuation ensembles on the corpus."""
    if "pf" in _WF:
        return _WF
    import numpy as np

    from aegis.research import learning, presets, validation
    from aegis.research.agent import Period
    from aegis.research.policies import PostGradEntry

    pf = _pf(base)
    basic, strict = PostGradEntry(presets.BASIC), PostGradEntry(presets.STRICT)
    costs = learning.TicketCosts()
    ctx = _context_fn(pf, strict)
    ent = learning.entry_samples(pf, lambda tr, o: basic.base_pass(tr, o, _eur(o.t))[0], costs, ctx)
    cont = {h: learning.continuation_samples(pf, lambda tr, o: strict.base_pass(tr, o, _eur(o.t))[0], costs, horizon_s=h)
            for h in (1800.0, 3600.0)}
    t0 = dt.datetime.fromisoformat(CORPUS_START).replace(tzinfo=dt.UTC).timestamp()
    t1 = dt.datetime.fromisoformat(CORPUS_END).replace(tzinfo=dt.UTC).timestamp()
    folds = validation.walk_forward(t0, t1, first_train_days=7, test_days=4, embargo_s=2 * 86400)
    entry_periods, entry_eval = [], []
    cont_periods: dict[float, list[Period]] = {h: [] for h in cont}
    cont_eval: dict[float, list[dict[str, Any]]] = {h: [] for h in cont}
    for f in folds:
        m_tr = validation.train_mask(ent, f)
        models = validation.fit_ensemble(ent, m_tr, n_boot=5)
        if models:
            # threshold chosen on the most recent 30% of the TRAINING period (never on test)
            tr_t = ent.t[m_tr]
            cut = np.quantile(tr_t, 0.7) if len(tr_t) else f.train_end
            inner = validation.Fold(f.name + "-inner", cut, cut, f.train_end, 0)
            mi = validation.fit_ensemble(ent, (ent.t < cut) & (ent.t_label_end < cut), n_boot=1)
            sel = m_tr & (ent.t >= cut)
            tau = 0.5
            if mi and sel.sum() >= 20:
                p_in = mi[0].predict(ent.X[sel])
                best = max((float(np.mean(ent.value[sel][p_in >= q])) if (p_in >= q).sum() >= 5 else -1, q)
                           for q in (0.3, 0.4, 0.5, 0.6))
                tau = best[1]
            entry_periods.append((Period(f.test_start, f.test_end, models, int(m_tr.sum()), f.name), tau, inner.name))
            te = (ent.t >= f.test_start) & (ent.t < f.test_end)
            if te.sum():
                p = np.mean([m.predict(ent.X[te]) for m in models], axis=0)
                entry_eval.append({"fold": f.name, "train_n": int(m_tr.sum()), "tau": tau,
                                   **validation.evaluate_scores(ent.y[te], p),
                                   "mean_value_selected": round(float(ent.value[te][p >= tau].mean()), 4)
                                   if (p >= tau).any() else None,
                                   "mean_value_all": round(float(ent.value[te].mean()), 4)})
        for h, s in cont.items():
            mtr = validation.train_mask(s, f)
            ms = validation.fit_ensemble(s, mtr, n_boot=5)
            if not ms:
                continue
            cont_periods[h].append(Period(f.test_start, f.test_end, ms, int(mtr.sum()), f.name))
            te = (s.t >= f.test_start) & (s.t < f.test_end)
            if te.sum():
                p = np.mean([m.predict(s.X[te]) for m in ms], axis=0)
                cont_eval[h].append({"fold": f.name, "train_n": int(mtr.sum()), **validation.evaluate_scores(s.y[te], p)})
    imp = entry_periods[-1][0].models[0].importance()[:12] if entry_periods else []
    _WF.update({"pf": pf, "folds": folds, "entry": ent, "cont": cont, "entry_periods": entry_periods,
                "entry_eval": entry_eval, "cont_periods": cont_periods, "cont_eval": cont_eval,
                "entry_importance": [(n, round(v, 4)) for n, v in imp],
                "test_start": folds[0].test_start if folds else t0, "test_end": t1})
    return _WF


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).isoformat()


def _bt_window(wf: dict[str, Any]) -> dict[str, float]:
    return {"start": wf["test_start"], "end": wf["test_end"]}


def e7_supervised(base: Path) -> dict[str, Any]:
    from aegis.research import presets
    from aegis.research.agent import HeuristicEntry, ModelEntry
    from aegis.research.policies import PostGradEntry

    wf = walk_forward_models(base)
    pf = wf["pf"]
    win = _bt_window(wf)
    periods = [p for p, _, _ in wf["entry_periods"]]
    rows = [_row(runner.run_preset(pf, "strict+trailing", label="champion strict+trailing", **win)),
            _row(runner.run_preset(pf, "basic+trailing", label="basic+trailing", **win)),
            _row(runner.run_preset(pf, "basic+trailing", label="heuristic EPI/MRI on basic (D)",
                                   entry_policy=HeuristicEntry(PostGradEntry(presets.BASIC), "pumpfun_corpus"), **win))]
    for tau in (0.4, 0.5, 0.6):
        me = ModelEntry(PostGradEntry(presets.BASIC), periods, tau, name=f"model>={tau}")
        rows.append(_row(runner.run_preset(pf, "basic+trailing", label=f"model(basic) p>={tau}", entry_policy=me, **win),
                         {"scored": len(me.log)}))
    me_s = ModelEntry(PostGradEntry(presets.STRICT), periods, 0.5, name="model_on_strict")
    rows.append(_row(runner.run_preset(pf, "strict+trailing", label="model(strict) p>=0.5", entry_policy=me_s, **win)))
    return {"test_window": [_iso(win["start"]), _iso(win["end"])], "folds": [f.__dict__ for f in wf["folds"]],
            "statistical_out_of_sample": wf["entry_eval"], "feature_weights_last_fold": wf["entry_importance"],
            "samples": len(wf["entry"].y), "label_base_rate": round(float(wf["entry"].y.mean()), 4),
            "economic": rows}


def e8_agent(base: Path) -> dict[str, Any]:
    from decimal import Decimal

    from aegis.research import presets
    from aegis.research.agent import ContinuationAgentExit
    from aegis.research.policies import RulesExit

    wf = walk_forward_models(base)
    pf = wf["pf"]
    win = _bt_window(wf)
    champion_exit = RulesExit(presets.TRAILING)
    rows = [_row(runner.run_preset(pf, "strict+trailing", label="champion strict+trailing", **win)),
            _row(runner.run_preset(pf, "strict+hold", label="strict+hold", **win)),
            _row(runner.run_preset(pf, "strict+trailing", label="strict+recovery x2 + 10% residual + trailing",
                                   exit_cfg=presets.TRAILING.model_copy(update={"recovery_multiple": Decimal(2),
                                                                                 "residual_fraction": Decimal("0.1")}), **win))]
    logs = {}
    for h, label, kw in ((3600.0, "agent h=1h", {}), (3600.0, "agent h=1h no partial", {"partial_below": 0.0}),
                         (1800.0, "agent h=30m", {}), (3600.0, "agent h=1h strict thresholds",
                                                       {"sell_below": 0.25, "partial_below": 0.35})):
        ag = ContinuationAgentExit(wf["cont_periods"][h], champion_exit, **kw)
        r = runner.run_preset(pf, "strict+trailing", label=label, exit_override=ag, **win)
        acts: dict[str, int] = {}
        for d in ag.log:
            acts[d.action] = acts.get(d.action, 0) + 1
        rows.append(_row(r, {"agent_actions": acts}))
        logs[label] = acts
    return {"test_window": [_iso(win["start"]), _iso(win["end"])],
            "statistical_out_of_sample": {str(int(h)): v for h, v in wf["cont_eval"].items()},
            "samples": {str(int(h)): len(s.y) for h, s in wf["cont"].items()},
            "economic": rows, "counterfactual": _forgone_upside(pf, win)}


def _forgone_upside(pf: Any, win: dict[str, float]) -> dict[str, Any]:
    """Champion exits: best liquidable multiple reached AFTER the exit within tracking (retrospective, audit only)."""
    r = runner.run_preset(pf, "strict+trailing", label="champion (counterfactual audit)", **win)
    ratios, later_x2 = [], 0
    for p in r.closed:
        tr = pf.tokens[p.mint]
        after = [o.price_usd for o in tr.obs if o.t > p.closed_at and o.price_usd]
        at_exit = tr.last(p.closed_at)
        if not after or at_exit is None or not at_exit.price_usd:
            continue
        best = max(after) / at_exit.price_usd
        ratios.append(best)
        later_x2 += best >= 2
    return {"closed": len(r.closed), "with_post_exit_data": len(ratios),
            "median_best_after_exit_vs_exit_price": round(statistics.median(ratios), 4) if ratios else None,
            "share_doubling_after_exit": round(later_x2 / len(ratios), 4) if ratios else None,
            "note": "gross price path after the exit (no costs); retrospective, never a decision input"}


EXPERIMENTS.update({
    "E7": ("Selección supervisada walk-forward: diagnóstico estadístico y evaluación económica", e7_supervised),
    "E8": ("Valor de continuación y agente de decisión frente al Champion", e8_agent),
})


# ============================================================================ block E (validation)
def _portfolio_specs(wf: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Portfolios A-I. A-G share the production allocation (paced, recycled 10%/day) so that differences
    come from selection/exits; H and I add compounding (unpaced, full reinvestment) and second chances."""
    from decimal import Decimal

    from aegis.research import presets
    from aegis.research.agent import (
        ContinuationAgentExit,
        HeuristicEntry,
        ModelEntry,
        RandomEntry,
        SecondChanceEntry,
        SecurityGate,
    )
    from aegis.research.policies import PostGradEntry, RulesExit

    periods = [p for p, _, _ in wf["entry_periods"]]
    tradable = presets.BASIC.model_copy(update={"min_liquidity_eur": Decimal(0), "min_market_cap_eur": None,
                                                "max_market_cap_eur": None})
    secure = tradable.model_copy(update={"min_liquidity_eur": Decimal(5000)})
    pf = wf["pf"]
    n_strict = sum(1 for tr in pf.tokens.values() if any(PostGradEntry(presets.STRICT).base_pass(tr, o, 1.16)[0]
                                                        for o in tr.obs[:80]))
    n_trad = sum(1 for tr in pf.tokens.values() if any(PostGradEntry(tradable).base_pass(tr, o, 1.16)[0] for o in tr.obs[:80]))
    rate = min(1.0, n_strict / n_trad) if n_trad else 1.0
    paced = {"investment_pacing": True, "recycling": "limited"}
    compound = {"investment_pacing": False, "recycling": "full"}
    trailing = RulesExit(presets.TRAILING)
    contextual = presets.TRAILING.model_copy(update={"recovery_multiple": Decimal(2), "residual_fraction": Decimal("0.1")})

    def agent() -> ContinuationAgentExit:
        return ContinuationAgentExit(wf["cont_periods"][3600.0], RulesExit(presets.TRAILING))

    smart = ModelEntry(PostGradEntry(presets.BASIC), periods, 0.5, name="supervised")
    return [
        ("A random tradable", "basic+trailing", {"entry_policy": RandomEntry(PostGradEntry(tradable), rate, name="A"),
                                                 "exit_override": trailing, "capital": paced}),
        ("B random + basic security", "basic+trailing", {"entry_policy": RandomEntry(SecurityGate(secure), rate, name="B"),
                                                         "exit_override": trailing, "capital": paced}),
        ("C economic + security", "basic+trailing", {"entry_policy": SecurityGate(presets.BASIC, name="C"),
                                                     "exit_override": trailing, "capital": paced}),
        ("D existing heuristic", "basic+trailing", {"entry_policy": HeuristicEntry(SecurityGate(presets.BASIC), "pumpfun"),
                                                    "exit_override": trailing, "capital": paced}),
        ("E supervised selection", "basic+trailing", {"entry_policy": smart, "exit_override": trailing, "capital": paced}),
        ("F supervised + contextual monetisation", "basic+trailing",
         {"entry_policy": ModelEntry(PostGradEntry(presets.BASIC), periods, 0.5), "exit_cfg": contextual, "capital": paced}),
        ("G supervised + decision agent", "basic+trailing",
         {"entry_policy": ModelEntry(PostGradEntry(presets.BASIC), periods, 0.5), "exit_override": agent(), "capital": paced}),
        ("H agent + compounding", "basic+trailing",
         {"entry_policy": ModelEntry(PostGradEntry(presets.BASIC), periods, 0.5), "exit_override": agent(),
          "capital": compound}),
        ("I agent + compounding + second chances", "basic+trailing",
         {"entry_policy": SecondChanceEntry(ModelEntry(PostGradEntry(presets.BASIC), periods, 0.5)),
          "exit_override": agent(), "capital": compound}),
        ("Champion strict+trailing (paced)", "strict+trailing", {"capital": paced}),
        ("Champion strict+trailing (compounding)", "strict+trailing", {"capital": compound}),
    ]


def e9_validation(base: Path) -> dict[str, Any]:
    from aegis.research import validation

    wf = walk_forward_models(base)
    pf = wf["pf"]
    win = _bt_window(wf)
    rows, daily = [], {}
    for label, preset, kw in _portfolio_specs(wf):
        r = runner.run_preset(pf, preset, label=label, **kw, **win)
        inc = validation.daily_increments(r.equity)
        daily[label] = inc
        rows.append(_row(r, {"bootstrap_objective": validation.bootstrap_summary(inc),
                             "pnl_without_top1_eur": r.metrics.get("pnl_without_top1_eur"),
                             "pnl_without_top5_eur": r.metrics.get("pnl_without_top5_eur"),
                             "top5_share_of_gross_gains": r.metrics.get("top5_share_of_gross_gains"),
                             "profit_factor": r.metrics.get("profit_factor"),
                             "max_drawdown_objective_eur": r.metrics.get("max_drawdown_objective_eur")}))
    champ = "Champion strict+trailing (paced)"
    pvals = {}
    for label, inc in daily.items():
        if label == champ:
            continue
        pr = validation.paired(inc, daily[champ])
        pvals[label] = pr["p_le_0"] if pr["p_le_0"] is not None else 1.0
        next(x for x in rows if x["label"] == label)["paired_vs_champion"] = pr
    tested = len(pvals) + 40  # + every variant run in E4-E8 (registered in the report): conservative family size
    holm = validation.holm({**pvals, **{f"_other_{k}": 1.0 for k in range(tested - len(pvals))}})
    sens = _sensitivity(pf, win)
    return {"test_window": [_iso(win["start"]), _iso(win["end"])], "portfolios": rows,
            "holm": {k: v for k, v in holm.items() if not k.startswith("_other_")}, "family_size": tested,
            "sensitivity": sens, "launchtape_out_of_time": _launchtape_frozen(base)}


def _sensitivity(pf: Any, win: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {"fees": [], "adverse": [], "tickets": []}
    for fee in ("flat_30bps_legacy_assumption", "pumpswap_measured_2026_10", "flat_125bps_conservative"):
        out["fees"].append(_row(runner.run_preset(pf, "strict+trailing", label=f"fee {fee}", fee_schedule=fee, **win)))
    for adv in (0, 100, 300, 600):
        out["adverse"].append(_row(runner.run_preset(pf, "strict+trailing", label=f"adverse {adv} bps", adverse_bps=adv,
                                                     **win)))
    for t in ("0.5", "1", "2", "5"):
        out["tickets"].append(_row(runner.run_preset(pf, "strict+trailing", label=f"ticket {t} EUR",
                                                     capital={"ticket_eur": t}, **win)))
    return out


def _launchtape_frozen(base: Path) -> dict[str, Any]:
    """Out-of-time check on Launch Tape (2026-10-10, already explored by the owner: NOT independent):
    frozen rules only (no refit), monitoring frequency 5 / 15 / 60 s and latency 2 / 10 / 30 s."""
    rows = []
    for res in (5.0, 15.0, 60.0):
        st = runner.load_store("launchtape", str(base), resolution_s=res)
        for preset in ("strict+trailing", "basic+trailing"):
            rows.append(_row(runner.run_preset(st, preset, label=f"{preset} @{int(res)}s"), {"resolution_s": res}))
    st = runner.load_store("launchtape", str(base), resolution_s=15.0)
    for lat in (2, 10, 30):
        rows.append(_row(runner.run_preset(st, "strict+trailing", label=f"strict+trailing latency {lat}s", latency_s=lat),
                         {"latency_s": lat}))
    return {"rows": rows}


EXPERIMENTS.update({
    "E9": ("Validación: carteras A–I walk-forward, bootstrap por bloques, Holm, costes, tickets y frecuencia", e9_validation),
})


# ============================================================================ report
def render(results_dir: Path, out: Path) -> Path:
    """Markdown report from the JSON results (Spanish; numbers only, no raw dataset rows)."""
    docs = {p.stem: json.loads(p.read_text()) for p in sorted(results_dir.glob("E*.json"), key=lambda p: int(p.stem[1:]))}
    L: list[str] = ["# AEGIS — Fase 2: resultados empíricos reproducibles", "",
                    "Generado por `aegis research phase2`. Datos: revisiones fijadas en `aegis.history.registry`. "
                    "Ninguna cifra es una promesa de rentabilidad: son resultados históricos con los supuestos indicados.", ""]
    cols = ("label", "net_worth_liquidable_eur", "contributions_eur", "buys", "sells", "mean_return_per_ticket",
            "win_rate_closed", "peak_ge_x2", "peak_ge_x2_realised_ge_x2", "median_peak_capture", "cash_idle_share")

    def table(rows: list[dict[str, Any]], cs: tuple[str, ...] = cols) -> None:
        L.append("| " + " | ".join(cs) + " |")
        L.append("|" + "---|" * len(cs))
        for r in rows:
            L.append("| " + " | ".join(_fmt(r.get(c)) for c in cs) + " |")
        L.append("")

    for eid, d in docs.items():
        L += [f"## {eid} — {d['title']}", "", f"Versión {d['aegis_version']}, generado {d['generated_at']}, "
              f"{d['runtime_s']} s.", ""]
        data = d["data"]
        for key in ("reference", "rows", "economic", "portfolios"):
            if key in data:
                extra = ("dataset", "owner_reported_eur") if key == "reference" else ()
                table(data[key], (*cols, *extra))
        if "sensitivity" in data:
            for k, rows in data["sensitivity"].items():
                L.append(f"### Sensibilidad: {k}")
                table(rows)
        if "launchtape_out_of_time" in data:
            L.append("### Launch Tape (fuera de periodo, ya explorado: no independiente)")
            table(data["launchtape_out_of_time"]["rows"])
        for key in ("statistical_out_of_sample", "latency", "cross_check", "engine_replay", "holm", "counterfactual",
                    "coverage_vs_context", "datasets"):
            if key in data:
                L += [f"### {key}", "", "```json", json.dumps(data[key], indent=1, default=str)[:6000], "```", ""]
    out.write_text("\n".join(L))
    return out


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


# ============================================================================ E10 (recovery of losers)
def e10_recovery(base: Path) -> dict[str, Any]:
    """Recovering capital from losers: stops, time stops and capital recovery, around the Champion exit."""
    from decimal import Decimal

    from aegis.research import presets

    pf = _pf(base)
    t = presets.TRAILING
    variants = {
        "champion trailing 30%": t,
        "trailing 20%": t.model_copy(update={"trailing_pct": Decimal("0.20")}),
        "trailing 45%": t.model_copy(update={"trailing_pct": Decimal("0.45")}),
        "trailing activates at x1.5": t.model_copy(update={"trailing_activate_multiple": Decimal("1.5")}),
        "+ stop at x0.5": t.model_copy(update={"stop_loss_multiple": Decimal("0.5")}),
        "+ stop at x0.7": t.model_copy(update={"stop_loss_multiple": Decimal("0.7")}),
        "+ time stop 6 h": t.model_copy(update={"max_hold_seconds": 6 * 3600}),
        "+ time stop 24 h": t.model_copy(update={"max_hold_seconds": 24 * 3600}),
        "+ recovery at x2, keep 10%": t.model_copy(update={"recovery_multiple": Decimal(2),
                                                           "residual_fraction": Decimal("0.1")}),
        "+ recovery at x1.5, keep 25%": t.model_copy(update={"recovery_multiple": Decimal("1.5"),
                                                             "residual_fraction": Decimal("0.25")}),
    }
    rows = [_row(runner.run_preset(pf, "strict+trailing", exit_cfg=x, label=name))
            for name, x in variants.items()]
    return {"period": [CORPUS_START, CORPUS_END], "rows": rows,
            "note": "single variable: exit rule only (strict entries, full reinvestment, measured fees)"}


EXPERIMENTS["E10"] = ("Recuperación de perdedoras y monetización: trailing, stops, salidas por tiempo, recuperación",
                      e10_recovery)
