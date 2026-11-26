"""Out-of-sample discipline for block E: walk-forward folds with purging/embargo, block bootstrap,
paired comparisons and multiple-comparison control."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from aegis.research.learning import Samples, token_weights
from aegis.research.models import IsotonicCalibrator, LogisticModel, auc, brier, ece, log_loss, reliability

DAY = 86400.0


@dataclass(frozen=True)
class Fold:
    name: str
    train_end: float  # samples decided before this...
    test_start: float  # ...and whose labels resolved before test_start - embargo
    test_end: float
    embargo_s: float


def walk_forward(t_min: float, t_max: float, *, first_train_days: float = 7, test_days: float = 4,
                 embargo_s: float = 2 * DAY) -> list[Fold]:
    """Expanding-window folds. Embargo >= the longest label horizon (48 h tracking) avoids leakage."""
    folds = []
    start = t_min + first_train_days * DAY
    k = 0
    while start < t_max:
        end = min(t_max, start + test_days * DAY)
        folds.append(Fold(f"wf{k}", start - embargo_s, start, end, embargo_s))
        start = end
        k += 1
    return folds


def train_mask(s: Samples, fold: Fold) -> np.ndarray:
    """Purged training set: decided before train_end AND label known before train_end (no overlap with test)."""
    return (s.t < fold.train_end) & (s.t_label_end < fold.train_end)


def fit_ensemble(s: Samples, mask: np.ndarray, *, n_boot: int = 5, l2: float = 3.0, seed: int = 0) -> list[LogisticModel]:
    """Bootstrap ensemble on the training mask, resampling TOKENS (observations of one token are dependent)."""
    idx = np.flatnonzero(mask)
    if len(idx) < 30 or len(set(s.y[idx])) < 2:
        return []
    rng = np.random.default_rng(seed)
    toks = sorted({s.group[i] for i in idx})
    by_tok: dict[str, list[int]] = {}
    for i in idx:
        by_tok.setdefault(s.group[i], []).append(i)
    models = []
    for b in range(n_boot):
        pick = rng.choice(len(toks), size=len(toks), replace=True) if b else np.arange(len(toks))
        rows = [i for k in pick for i in by_tok[toks[k]]]
        sub = s.mask(np.isin(np.arange(len(s.y)), rows))
        if len(set(sub.y)) < 2:
            continue
        models.append(LogisticModel(l2=l2).fit(sub.X, sub.y, list(s.names), sample_weight=token_weights(sub.group)))
    return models


def evaluate_scores(y: np.ndarray, p: np.ndarray) -> dict[str, object]:
    """Statistical diagnostics only: these are NOT evidence of profitability."""
    return {"n": len(y), "base_rate": round(float(y.mean()), 4) if len(y) else None,
            "auc": None if (a := auc(y, p)) is None else round(a, 4), "brier": round(brier(y, p), 4) if len(y) else None,
            "log_loss": round(log_loss(y, p), 4) if len(y) else None,
            "ece": round(ece(y, p), 4) if len(y) >= 10 else None, "reliability": reliability(y, p, 5)}


def calibrate(train_scores: np.ndarray, train_y: np.ndarray) -> IsotonicCalibrator:
    return IsotonicCalibrator().fit(train_scores, train_y)


def daily_increments(equity: list[tuple[str, float, float, float]]) -> list[float]:
    """Daily change of the objective (net worth - contributions) from an hourly equity curve."""
    by_day: dict[str, float] = {}
    for t, nw, _, contrib in equity:
        by_day[t[:10]] = nw - contrib
    vals = [v for _, v in sorted(by_day.items())]
    return [b - a for a, b in zip([0.0, *vals[:-1]], vals, strict=True)]


def block_bootstrap(x: list[float] | np.ndarray, *, block: int = 3, n: int = 5000, seed: int = 11) -> np.ndarray:
    """Circular block bootstrap of the SUM of a daily series (keeps short-range dependence)."""
    arr = np.asarray(x, dtype=float)
    m = len(arr)
    if m == 0:
        return np.zeros(n)
    rng = np.random.default_rng(seed)
    k = math.ceil(m / block)
    starts = rng.integers(0, m, size=(n, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % m
    return arr[idx.reshape(n, -1)[:, :m]].sum(axis=1)


def bootstrap_summary(x: list[float], **kw: object) -> dict[str, float | None]:
    b = block_bootstrap(x, **kw)  # type: ignore[arg-type]
    if not len(x):
        return {"sum": None, "ci90_lo": None, "ci90_hi": None, "p_le_0": None}
    return {"sum": round(float(np.sum(x)), 4), "ci90_lo": round(float(np.quantile(b, 0.05)), 4),
            "ci90_hi": round(float(np.quantile(b, 0.95)), 4), "p_le_0": round(float((b <= 0).mean()), 4)}


def paired(a: list[float], b: list[float], **kw: object) -> dict[str, float | None]:
    """Bootstrap of the daily DIFFERENCE challenger - champion (same days)."""
    m = min(len(a), len(b))
    return bootstrap_summary([x - y for x, y in zip(a[:m], b[:m], strict=True)], **kw)


def holm(pvalues: dict[str, float]) -> dict[str, dict[str, float | bool]]:
    """Holm-Bonferroni step-down at alpha = 0.05 over every variant tested."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, dict[str, float | bool]] = {}
    still = True
    for k, (name, p) in enumerate(items):
        thr = 0.05 / (m - k)
        still = still and p <= thr
        out[name] = {"p": p, "threshold": round(thr, 5), "significant": still}
    return out
