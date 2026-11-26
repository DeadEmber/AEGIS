"""Small, auditable supervised baselines (numpy only): L2 logistic regression, calibration, metrics.

Nothing here is a validated probability for operations. A model's output may only be presented as a
probability after calibration on held-out data AND registration (docs/MODEL_REGISTRY.md). Promotion is
economic (net liquidable worth in walk-forward), never AUC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Preprocessor:
    """Median imputation + missing indicators + standardisation, fitted on TRAINING rows only."""

    names: list[str]
    medians: np.ndarray | None = None
    mean: np.ndarray | None = None
    std: np.ndarray | None = None
    keep_missing: list[int] = field(default_factory=list)

    def fit(self, X: np.ndarray) -> Preprocessor:
        med = np.nanmedian(np.where(np.isfinite(X), X, np.nan), axis=0)
        self.medians = np.where(np.isfinite(med), med, 0.0)
        miss = ~np.isfinite(X)
        self.keep_missing = [j for j in range(X.shape[1]) if 0 < miss[:, j].mean() < 1]
        Z = self._impute(X)
        self.mean = Z.mean(axis=0)
        sd = Z.std(axis=0)
        self.std = np.where(sd > 1e-12, sd, 1.0)
        return self

    def _impute(self, X: np.ndarray) -> np.ndarray:
        assert self.medians is not None
        Z = np.where(np.isfinite(X), X, self.medians)
        if self.keep_missing:
            Z = np.hstack([Z, (~np.isfinite(X[:, self.keep_missing])).astype(float)])
        return Z

    def transform(self, X: np.ndarray) -> np.ndarray:
        assert self.mean is not None and self.std is not None
        return (self._impute(X) - self.mean) / self.std

    @property
    def out_names(self) -> list[str]:
        return self.names + [f"missing:{self.names[j]}" for j in self.keep_missing]


@dataclass
class LogisticModel:
    l2: float = 1.0
    coef: np.ndarray | None = None
    intercept: float = 0.0
    pre: Preprocessor | None = None
    version: str = "logreg-l2-0.1"

    def fit(self, X: np.ndarray, y: np.ndarray, names: list[str], sample_weight: np.ndarray | None = None,
            iters: int = 50) -> LogisticModel:
        self.pre = Preprocessor(names).fit(X)
        Z = self.pre.transform(X)
        n, d = Z.shape
        w = np.ones(n) if sample_weight is None else sample_weight / sample_weight.mean()
        A = np.hstack([np.ones((n, 1)), Z])
        beta = np.zeros(d + 1)
        reg = np.full(d + 1, self.l2)
        reg[0] = 0.0
        for _ in range(iters):  # Newton / IRLS
            p = _sigmoid(A @ beta)
            g = A.T @ (w * (p - y)) + reg * beta
            H = (A * (w * p * (1 - p))[:, None]).T @ A + np.diag(reg)
            step = np.linalg.solve(H + 1e-9 * np.eye(d + 1), g)
            beta -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        self.intercept, self.coef = float(beta[0]), beta[1:]
        return self

    def decision(self, X: np.ndarray) -> np.ndarray:
        assert self.pre is not None and self.coef is not None
        return self.pre.transform(X) @ self.coef + self.intercept

    def predict(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.decision(X))

    def importance(self) -> list[tuple[str, float]]:
        assert self.pre is not None and self.coef is not None
        return sorted(zip(self.pre.out_names, self.coef.tolist(), strict=True), key=lambda kv: -abs(kv[1]))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


@dataclass
class IsotonicCalibrator:
    """Pool-adjacent-violators isotonic map score -> frequency, fitted on a calibration split."""

    x: np.ndarray | None = None
    y: np.ndarray | None = None

    def fit(self, scores: np.ndarray, y: np.ndarray) -> IsotonicCalibrator:
        o = np.argsort(scores)
        s, t = scores[o], y[o].astype(float)
        blocks = [[t[i], 1.0, s[i], s[i]] for i in range(len(t))]  # [sum, n, lo, hi]
        out: list[list[float]] = []
        for b in blocks:
            out.append(b)
            while len(out) > 1 and out[-2][0] / out[-2][1] > out[-1][0] / out[-1][1]:
                b2 = out.pop()
                b1 = out.pop()
                out.append([b1[0] + b2[0], b1[1] + b2[1], b1[2], b2[3]])
        self.x = np.array([(b[2] + b[3]) / 2 for b in out])
        self.y = np.array([b[0] / b[1] for b in out])
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        assert self.x is not None and self.y is not None
        return np.interp(scores, self.x, self.y)


def auc(y: np.ndarray, s: np.ndarray) -> float | None:
    """Mann-Whitney AUC with tie correction."""
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return None
    ranks = _rank(s)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def _rank(s: np.ndarray) -> np.ndarray:
    o = np.argsort(s, kind="mergesort")
    r = np.empty(len(s))
    r[o] = np.arange(1, len(s) + 1)
    vals = s[o]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and vals[j + 1] == vals[i]:
            j += 1
        if j > i:
            r[o[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    return r


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 5) -> list[dict[str, float]]:
    """Calibration table on quantile bins: mean predicted vs observed frequency."""
    if len(p) == 0:
        return []
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for k in range(bins):
        m = (p >= edges[k]) & ((p <= edges[k + 1]) if k == bins - 1 else (p < edges[k + 1]))
        if m.sum():
            out.append({"bin": k, "n": int(m.sum()), "mean_pred": round(float(p[m].mean()), 4),
                        "observed": round(float(y[m].mean()), 4)})
    return out


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    rel = reliability(y, p, bins)
    n = sum(r["n"] for r in rel)
    return float(sum(r["n"] / n * abs(r["mean_pred"] - r["observed"]) for r in rel)) if n else math.nan
