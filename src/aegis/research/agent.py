"""Block C/D policies built on fitted models, and the experimental portfolios A-I.

- `ModelEntry`: a base entry policy filtered by a supervised score (walk-forward: the model used at time t
  was trained only on labels known before t minus an embargo).
- `ContinuationAgentExit`: an optimal-stopping agent. For a held ticket it compares, on the SAME state, the
  value of HOLD (an ensemble estimate of P(liquidable value higher after the horizon)) with selling now
  (FULL / PARTIAL). When the ensemble disagrees (uncertainty) or inputs are missing it abstains and
  delegates to the Champion (reference) policy. Every estimate is logged for audit.
- Random / heuristic entries and `SecondChanceEntry` complete portfolios A-I.

Scores are model outputs, not validated probabilities: they are labelled `p_model` and never shown as
probabilities on the dashboard (rule 6).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from aegis.core.money import ONE
from aegis.features.library import FeatureContext, compute
from aegis.history.store import MarketView, Obs, TokenTrack, history_points, to_pool_observation, ts
from aegis.research.learning import ENTRY_FEATURES, POSITION_FEATURES, entry_features, position_features
from aegis.research.models import LogisticModel
from aegis.research.policies import Context, ExitAction, Held, PostGradEntry
from aegis.scoring.heuristics import score_all


@dataclass
class Period:
    start: float
    end: float
    models: list[LogisticModel]  # bootstrap ensemble (len 1 = single model)
    trained_on: int = 0
    fold: str = ""


def _pick(periods: list[Period], t: float) -> Period | None:
    return next((p for p in periods if p.start <= t < p.end), None)


def _ensemble(p: Period, x: np.ndarray) -> tuple[float, float]:
    preds = np.array([m.predict(x[None, :])[0] for m in p.models])
    return float(preds.mean()), float(preds.std())


class ModelEntry:
    def __init__(self, base: PostGradEntry, periods: list[Period], threshold: float, name: str = "model_entry"):
        self.base = base
        self.periods = periods
        self.threshold = threshold
        self.name = name
        self.log: list[tuple[float, str, float]] = []

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        return self.base.base_pass(tr, o, eur_usd)

    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None:
        why = self.base.signal(tr, o, eur_usd, ctx, entries)
        if not why:
            return None
        p = _pick(self.periods, o.t)
        if p is None:
            return None  # no model trained for this period: abstain (no entry), never a guess
        f = entry_features(tr, tr.index_at(o.t), ctx.opportunity_rate_24h, ctx.observed_24h)
        mean, _ = _ensemble(p, np.array([f[n] for n in ENTRY_FEATURES], dtype=float))
        self.log.append((o.t, tr.mint, mean))
        if mean < self.threshold:
            return None
        return [*why, f"p_model={mean:.3f} >= {self.threshold} ({p.fold})"]


@dataclass
class AgentDecision:
    t: float
    mint: str
    multiple: float
    p_hold: float | None
    sd: float | None
    action: str
    reason: str


class ContinuationAgentExit:
    name = "agent"

    def __init__(self, periods: list[Period], fallback: object, *, sell_below: float = 0.35, partial_below: float = 0.45,
                 max_sd: float = 0.12, partial_fraction: Decimal = Decimal("0.5"), min_sale_eur: Decimal = Decimal("0.10")):
        self.periods = periods
        self.fallback = fallback
        self.sell_below = sell_below
        self.partial_below = partial_below
        self.max_sd = max_sd
        self.partial_fraction = partial_fraction
        self.min_sale = min_sale_eur
        self.log: list[AgentDecision] = []

    def decide(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float,
               now: float) -> ExitAction | None:
        if liq_eur < self.min_sale:
            return None
        p = _pick(self.periods, now)
        tr = view.track(h.mint)
        j = tr.index_at(o.t)
        i = tr.index_at(h.opened_at)
        if p is None or j < 0 or i < 0:
            return self._delegate(h, o, multiple, liq_eur, view, eur_usd, now, "no model for this period")
        f = position_features(tr, j, max(0, i), multiple, max(h.peak_multiple, multiple))
        x = np.array([f[n] for n in POSITION_FEATURES], dtype=float)
        if not math.isfinite(x[0]):
            return self._delegate(h, o, multiple, liq_eur, view, eur_usd, now, "state incomplete")
        mean, sd = _ensemble(p, x)
        if sd > self.max_sd:
            return self._delegate(h, o, multiple, liq_eur, view, eur_usd, now, f"uncertain (sd {sd:.3f})", mean, sd)
        if mean < self.sell_below:
            act: ExitAction | None = ExitAction(ONE, f"agent: p_model(up) {mean:.2f}±{sd:.2f} < {self.sell_below}: sell")
        elif mean < self.partial_below and h.partials == 0 and h.status != "RESIDUAL":
            act = ExitAction(self.partial_fraction, f"agent: p_model(up) {mean:.2f}±{sd:.2f}: partial")
        else:
            act = None
        self.log.append(AgentDecision(now, h.mint, multiple, mean, sd, "SELL" if act and act.fraction >= ONE else
                                      ("PARTIAL" if act else "HOLD"), act.reason if act else "hold"))
        return act

    def _delegate(self, h: Held, o: Obs, multiple: float, liq_eur: Decimal, view: MarketView, eur_usd: float, now: float,
                  why: str, mean: float | None = None, sd: float | None = None) -> ExitAction | None:
        act = self.fallback.decide(h, o, multiple, liq_eur, view, eur_usd, now)  # type: ignore[attr-defined]
        self.log.append(AgentDecision(now, h.mint, multiple, mean, sd, "DELEGATED", f"{why}; champion: "
                                      f"{act.reason if act else 'hold'}"))
        return act


class RandomEntry:
    """Portfolios A/B: uniform random selection (seeded, deterministic per token) among candidates passing
    `gate`, with probability `rate` per token, at the token's first eligible observation."""

    def __init__(self, gate: PostGradEntry, rate: float, seed: int = 7, name: str = "random"):
        self.gate = gate
        self.rate = rate
        self.seed = seed
        self.name = name
        self._decided: dict[str, bool] = {}

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        return self.gate.base_pass(tr, o, eur_usd)

    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None:
        if entries >= 1 or not self.gate.base_pass(tr, o, eur_usd)[0]:
            return None
        if tr.mint not in self._decided:
            u = int(hashlib.sha256(f"{self.seed}:{tr.mint}".encode()).hexdigest()[:12], 16) / float(16**12)
            self._decided[tr.mint] = u < self.rate
        return ["random selection (unbiased sample)"] if self._decided[tr.mint] else None


class SecurityGate(PostGradEntry):
    """Basic historical security controls available in the datasets: standard pump.fun mint (mayhem-mode
    token program unknown), launch concentration known and <= max_top10, liquidity floor. Not on-chain proof."""

    def __init__(self, base_cfg, max_top10_pct: float = 50.0, name: str = "security_gate"):  # type: ignore[no-untyped-def]
        super().__init__(base_cfg, name)
        self.max_top10 = max_top10_pct

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        ok, why = super().base_pass(tr, o, eur_usd)
        st = tr.static_at(o.t)
        if st.get("is_mayhem_mode"):
            ok, why = False, [*why, "mayhem_token_program_unknown"]
        top10 = st.get("initial_top10_pct_corrected")
        if "initial_top10_pct_corrected" in tr.static and (top10 is None or float(top10) > self.max_top10):
            ok, why = False, [*why, "concentration_unknown_or_high"]
        return ok, why


class HeuristicEntry:
    """Portfolio D: the existing AEGIS heuristic quality rule (EPI >= 0.40 and MRI < 0.5, or EPI >= 0.55),
    computed by the production feature library and scores on the token's point-in-time history."""

    def __init__(self, gate: PostGradEntry, source: str, name: str = "heuristic"):
        self.gate = gate
        self.source = source
        self.name = name

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        return self.gate.base_pass(tr, o, eur_usd)

    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None:
        why = self.gate.signal(tr, o, eur_usd, ctx, entries)
        if not why:
            return None
        i = tr.index_at(o.t)
        hist = tr.obs[:i]
        fv = compute(FeatureContext(as_of=ts(o.t), obs=to_pool_observation(tr, o, self.source),
                                    history=history_points(hist), first_seen_at=ts(tr.obs[0].t)))
        sc = score_all(fv, data_age_minutes=0.0)
        epi, mri = sc.epi.value or 0.0, sc.mri.value if sc.mri.value is not None else 1.0
        if (epi >= 0.40 and mri < 0.5) or epi >= 0.55:
            return [*why, f"heuristic EPI {epi:.2f} MRI {mri:.2f}"]
        return None


@dataclass
class SecondChanceEntry:
    """Portfolio I: after a full exit, re-enter once if a second wave forms: price at least `rebound` x the
    post-exit low, buy/sell m5 ratio >= `min_bs`, liquidity >= the base floor, within `window_s`."""

    base: object
    rebound: float = 1.5
    min_bs: float = 1.5
    window_s: float = 24 * 3600
    max_entries: int = 2
    name: str = "second_chance"
    exits: dict[str, float] = field(default_factory=dict)

    def base_pass(self, tr: TokenTrack, o: Obs, eur_usd: float) -> tuple[bool, list[str]]:
        return self.base.base_pass(tr, o, eur_usd)  # type: ignore[attr-defined]

    def signal(self, tr: TokenTrack, o: Obs, eur_usd: float, ctx: Context, entries: int) -> list[str] | None:
        if entries == 0:
            return self.base.signal(tr, o, eur_usd, ctx, entries)  # type: ignore[attr-defined]
        last_exit = ctx.last_exit.get(tr.mint)
        if entries >= self.max_entries or last_exit is None or not (0 < o.t - last_exit <= self.window_s):
            return None
        i = tr.index_at(o.t)
        after = [x.price_usd for x in tr.obs[:i + 1] if x.t >= last_exit and x.price_usd]
        if len(after) < 3 or not o.price_usd:
            return None
        low = min(after)
        bs = o.buy_sell_ratio(0)
        liq = (o.liq_usd or 0) / eur_usd
        floor = float(self.base.cfg.min_liquidity_eur) if hasattr(self.base, "cfg") else 0.0  # type: ignore[attr-defined]
        if o.price_usd >= low * self.rebound and bs is not None and bs >= self.min_bs and liq >= floor:
            return [f"second chance: x{o.price_usd / low:.2f} from post-exit low, buy/sell {bs:.2f}"]
        return None
