"""Phase 2 blocks C/D/E: models, calibration, point-in-time samples, agent abstention, validation tools."""

from __future__ import annotations

import dataclasses
import itertools
import math
from decimal import Decimal

import numpy as np
import pytest

from aegis.research import learning, presets, validation
from aegis.research.agent import ContinuationAgentExit, ModelEntry, Period, RandomEntry, SecondChanceEntry
from aegis.research.models import IsotonicCalibrator, LogisticModel, Preprocessor, auc, ece, reliability
from aegis.research.policies import Context, ExitAction, Held, PostGradEntry
from tests.history_synth import T0, make_store

DAY = 86400.0


def test_logistic_model_learns_a_signal_and_reports_weights():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 3))
    y = (X[:, 0] + 0.3 * rng.normal(size=600) > 0).astype(float)
    m = LogisticModel(l2=1.0).fit(X, y, ["a", "b", "c"])
    assert auc(y, m.predict(X)) > 0.9
    assert m.importance()[0][0] == "a"


def test_preprocessor_uses_training_statistics_only():
    tr = np.array([[1.0, np.nan], [3.0, 2.0], [5.0, 4.0]])
    pre = Preprocessor(["x", "y"]).fit(tr)
    te = np.array([[100.0, np.nan]])
    z = pre.transform(te)
    assert pre.medians.tolist() == [3.0, 3.0]  # test values never enter the fit
    assert z.shape == (1, 3) and pre.out_names[-1] == "missing:y"


def test_auc_matches_brute_force_with_ties():
    y = np.array([0, 1, 0, 1, 1, 0])
    s = np.array([0.1, 0.4, 0.4, 0.8, 0.2, 0.9])
    pairs = [(i, j) for i in range(6) for j in range(6) if y[i] == 1 and y[j] == 0]
    brute = sum(1.0 if s[i] > s[j] else 0.5 if s[i] == s[j] else 0.0 for i, j in pairs) / len(pairs)
    assert auc(y, s) == pytest.approx(brute)
    assert auc(np.ones(3), np.array([0.1, 0.2, 0.3])) is None


def test_isotonic_calibration_is_monotone_and_reliability_is_tabulated():
    rng = np.random.default_rng(1)
    s = rng.uniform(size=400)
    y = (rng.uniform(size=400) < s**2).astype(float)
    cal = IsotonicCalibrator().fit(s, y)
    grid = np.linspace(0, 1, 50)
    out = cal.predict(grid)
    assert np.all(np.diff(out) >= -1e-12)
    rel = reliability(y, s, 5)
    assert len(rel) == 5 and sum(r["n"] for r in rel) == 400
    assert 0 <= ece(y, s) <= 1


def test_walk_forward_folds_are_ordered_and_purged():
    folds = validation.walk_forward(0, 20 * DAY, first_train_days=7, test_days=4, embargo_s=2 * DAY)
    assert [f.name for f in folds] == ["wf0", "wf1", "wf2", "wf3"]
    for a, b in itertools.pairwise(folds):
        assert a.test_end == b.test_start
    f = folds[0]
    s = learning.Samples(np.zeros((3, 1)), np.array([0.0, 1.0, 1.0]), np.ones(3),
                         t=np.array([1 * DAY, 4.5 * DAY, 6.5 * DAY]), t_label_end=np.array([2 * DAY, 5.5 * DAY, 6.6 * DAY]),
                         group=["a", "b", "c"], creator=[None] * 3, names=("x",))
    m = validation.train_mask(s, f)
    assert m.tolist() == [True, False, False]  # label of 'b' resolves inside the embargo: purged


def test_block_bootstrap_and_holm():
    b = validation.block_bootstrap([1.0] * 10, block=3, n=200)
    assert np.allclose(b, 10.0)
    assert validation.bootstrap_summary([])["sum"] is None
    h = validation.holm({"x": 0.001, "y": 0.04, "z": 0.2})
    assert h["x"]["significant"] and not h["y"]["significant"] and not h["z"]["significant"]
    assert validation.daily_increments([("2026-07-01T00", 100.0, 0, 100.0), ("2026-07-01T05", 103.0, 0, 100.0),
                                        ("2026-07-02T00", 101.0, 0, 100.0)]) == [3.0, -2.0]


def test_entry_features_are_point_in_time():
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    f1 = learning.entry_features(tr, 10, 0.1, 50)
    tr2 = dataclasses.replace(tr, obs=list(tr.obs))
    for k in range(11, len(tr2.obs)):  # rewrite the future
        tr2.obs[k] = dataclasses.replace(tr2.obs[k], price_usd=1e9, liq_usd=1.0)
    tr2.finalize()
    f2 = learning.entry_features(tr2, 10, 0.1, 50)
    assert all((math.isnan(a) and math.isnan(b)) or a == b for a, b in zip(f1.values(), f2.values(), strict=True))


def test_samples_labels_use_costs_and_mark_censoring():
    st = make_store(2)
    s = learning.entry_samples(st, lambda tr, o: o.since_grad_s is not None and o.since_grad_s >= 60,
                               learning.TicketCosts(), lambda t: (0.05, 100))
    assert len(s.y) == 6 and set(s.y) <= {0.0, 1.0}
    assert np.all(s.t_label_end >= s.t)
    pumps = [v for v, g in zip(s.value, s.group, strict=True) if g.startswith("pump")]
    dumps = [v for v, g in zip(s.value, s.group, strict=True) if g.startswith("dump")]
    assert min(pumps) > max(dumps)
    c = learning.continuation_samples(st, lambda tr, o: o.since_grad_s is not None and o.since_grad_s >= 60,
                                      learning.TicketCosts(), horizon_s=1800)
    assert len(c.y) > 0 and np.all(c.t_label_end >= c.t + 1800 - 1e-6)


class _Fallback:
    def __init__(self):
        self.calls = 0

    def decide(self, *a):
        self.calls += 1
        return ExitAction(Decimal(1), "champion")


def _held(tr, t):
    return Held(1, 1, tr.mint, t, Decimal(1), Decimal(0), Decimal(100), Decimal(100), False, "OPEN", Decimal("0.09"), 60.0,
                peak_multiple=1.2)


def test_agent_abstains_and_delegates_without_a_model_or_when_uncertain():
    from aegis.history.store import MarketView
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    v = MarketView(st)
    o = tr.obs[20]
    v.advance(o.t)
    fb = _Fallback()
    ag = ContinuationAgentExit([], fb)
    assert ag.decide(_held(tr, tr.obs[1].t), o, 1.1, Decimal("1.1"), v, 1.16, o.t).reason == "champion"
    assert ag.log[-1].action == "DELEGATED"

    class Noisy:  # two models that disagree strongly
        def __init__(self, p):
            self.p = p

        def predict(self, X):
            return np.full(len(X), self.p)

    ag2 = ContinuationAgentExit([Period(0, 1e12, [Noisy(0.05), Noisy(0.95)])], fb, max_sd=0.1)
    ag2.decide(_held(tr, tr.obs[1].t), o, 1.1, Decimal("1.1"), v, 1.16, o.t)
    assert ag2.log[-1].action == "DELEGATED" and fb.calls == 2
    ag3 = ContinuationAgentExit([Period(0, 1e12, [Noisy(0.1), Noisy(0.12)])], fb)
    act = ag3.decide(_held(tr, tr.obs[1].t), o, 1.1, Decimal("1.1"), v, 1.16, o.t)
    assert act is not None and act.fraction == 1 and ag3.log[-1].action == "SELL"


def test_model_entry_abstains_outside_trained_periods():
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    e = presets.STRICT.model_copy(update={"min_liquidity_eur": Decimal(1000), "min_price_change_m5_pct": None})
    me = ModelEntry(PostGradEntry(e), [], 0.5)
    assert me.signal(tr, tr.obs[2], 1.16, Context(), 0) is None


def test_random_entry_is_deterministic_and_second_chance_needs_an_exit():
    st = make_store(3)
    gate = PostGradEntry(presets.STRICT.model_copy(update={"min_liquidity_eur": Decimal(1000),
                                                           "min_price_change_m5_pct": None}))
    picks = []
    for _ in range(2):
        r = RandomEntry(gate, 0.5, seed=3)
        picks.append([bool(r.signal(tr, tr.obs[2], 1.16, Context(), 0)) for tr in st.tokens.values()])
    assert picks[0] == picks[1]
    tr = next(iter(st.tokens.values()))
    sc = SecondChanceEntry(gate)
    assert sc.signal(tr, tr.obs[30], 1.16, Context(), 1) is None  # held once, never exited: no second chance
    assert T0 > 0
