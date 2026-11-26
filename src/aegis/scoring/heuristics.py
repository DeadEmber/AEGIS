"""AEGIS proprietary indices — BASELINE HEURISTICS (`heuristic-v0`).

IMPORTANT: these are transparent, hand-weighted scores in [0, 1]. They are NOT calibrated
probabilities and must never be presented as such. They exist (a) to operate the paper portfolio
before trained models pass validation and (b) as the baseline every learned model must beat.
Weights are deliberately simple and documented in docs/FEATURE_CATALOG.md; they are to be replaced
via the Champion-Challenger process, not tuned by hand on recent results.

Missing inputs reduce the Model Confidence Index (MCI) instead of being imputed as neutral values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.features.library import FeatureVector

VERSION = "heuristic-v0"


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _scale(x: float | None, lo: float, hi: float) -> float | None:
    if x is None:
        return None
    if hi == lo:
        return 0.0
    return _clip((x - lo) / (hi - lo))


@dataclass
class Score:
    name: str
    value: float | None
    components: dict[str, float | None] = field(default_factory=dict)
    kind: str = "heuristic"  # never 'probability' for this module
    version: str = VERSION

    @property
    def coverage(self) -> float:
        if not self.components:
            return 0.0
        return sum(1 for v in self.components.values() if v is not None) / len(self.components)


def _weighted(name: str, parts: dict[str, tuple[float | None, float]], min_coverage: float = 0.5) -> Score:
    num = den = 0.0
    comps: dict[str, float | None] = {}
    for k, (v, w) in parts.items():
        comps[k] = v
        if v is not None:
            num += v * w
            den += w
    total_w = sum(w for _, w in parts.values())
    if den == 0 or den / total_w < min_coverage:
        return Score(name, None, comps)
    return Score(name, num / den, comps)


def peak_risk(f: FeatureVector) -> Score:
    """PRS: risk that an up-move is exhausting. Higher = more exhaustion evidence."""
    acc = f.get("acceleration_short")
    ret_h6 = f.get("ret_h6")
    run_then_decel = None
    if acc is not None and ret_h6 is not None:
        run_then_decel = _clip(ret_h6 / 2.0) * _clip(-acc / 2.0) if ret_h6 > 0 else 0.0
    return _weighted("PRS", {
        "drawdown_from_peak": (_scale(f.get("drawdown_from_peak_h24"), 0.05, 0.40), 0.30),
        "buyer_deceleration": (_scale(-(f.get("buyer_growth_short") or 0) if f.get("buyer_growth_short") is not None
                                      else None, 0.0, 0.7), 0.20),
        "sell_pressure_m5": (_scale(-(f.get("trade_imbalance_m5") or 0) if f.get("trade_imbalance_m5") is not None
                                    else None, 0.0, 0.5), 0.15),
        "seller_share_h1": (_scale(1 - f.get("buyer_share_h1") if f.get("buyer_share_h1") is not None else None,
                                   0.45, 0.75), 0.10),
        "liquidity_drop_h1": (_scale(-(f.get("liquidity_change_h1") or 0) if f.get("liquidity_change_h1") is not None
                                     else None, 0.0, 0.4), 0.15),
        "run_then_decel": (run_then_decel, 0.10),
    })


def demand_quality(f: FeatureVector) -> Score:
    """DQI: is the move supported by broad, persistent, apparently organic demand?"""
    bs1, bs6, bs24 = f.get("buyer_share_h1"), f.get("buyer_share_h6"), f.get("buyer_share_h24")
    persistence = None
    if bs1 is not None and bs6 is not None and bs24 is not None:
        persistence = sum(1.0 for v in (bs1, bs6, bs24) if v > 0.5) / 3
    return _weighted("DQI", {
        "buyer_share_h1": (_scale(bs1, 0.45, 0.75), 0.25),
        "persistence": (persistence, 0.20),
        "breadth": (_scale(f.get("buyer_rate_h1"), 20, 1000), 0.20),
        "organic": (_scale(f.get("organic_score"), 5, 70), 0.20),
        "not_bot_heavy": (_scale(-(f.get("trades_per_address_h24") or 0) if f.get("trades_per_address_h24") is not None
                                 else None, -12, -2), 0.15),
    })


def manipulation_risk(f: FeatureVector) -> Score:
    """MRI: behavioural indicators of wash trading / coordinated activity."""
    return _weighted("MRI", {
        "bot_concentration": (_scale(f.get("trades_per_address_h24"), 3, 15), 0.30),
        "turnover": (_scale(f.get("volume_to_liquidity_h24"), 5, 60), 0.25),
        "low_organic": (_scale(-(f.get("organic_score") or 0) if f.get("organic_score") is not None else None,
                               -40, -5), 0.25),
        "concentration": (_scale(f.get("top10_pct"), 20, 60), 0.20),
    })


def explosion_propensity(f: FeatureVector, dqi: Score, mri: Score) -> Score:
    """EPI (heuristic rank, NOT a probability): early-expansion signature with quality demand."""
    return _weighted("EPI_h", {
        "acceleration": (_scale(f.get("acceleration_short"), 0.0, 3.0), 0.20),
        "buyer_growth": (_scale(f.get("buyer_growth_short"), 0.0, 1.5), 0.20),
        "momentum_h1": (_scale(f.get("ret_h1"), 0.0, 0.8), 0.15),
        "liquidity_growth": (_scale(f.get("liquidity_change_h1"), 0.0, 0.5), 0.10),
        "demand_quality": (dqi.value, 0.20),
        "clean": (None if mri.value is None else 1 - mri.value, 0.15),
    })


def structural_survival(f: FeatureVector) -> Score:
    """SSI: ability to keep a market after a crash."""
    return _weighted("SSI", {
        "liquidity_retention": (_scale(f.get("liquidity_change_h24"), -0.8, 0.0), 0.30),
        "activity": (_scale(f.get("buyer_rate_h24"), 2, 200), 0.25),
        "holders": (_scale(f.get("holders_count"), 200, 5000), 0.25),
        "buyer_share": (_scale(f.get("buyer_share_h24"), 0.35, 0.6), 0.20),
    })


def second_wave(f: FeatureVector, ssi: Score) -> Score:
    """SWP_h (heuristic): deep drawdown + rebound + rebuilding demand + surviving structure."""
    return _weighted("SWP_h", {
        "deep_drawdown": (_scale(f.get("drawdown_from_ath"), 0.6, 0.95), 0.20),
        "rebound": (_scale(f.get("multiple_from_low_since_ath"), 1.2, 3.0), 0.25),
        "demand_rebuild": (_scale(f.get("buyer_share_h1"), 0.5, 0.7), 0.20),
        "acceleration": (_scale(f.get("acceleration_short"), 0.0, 2.0), 0.15),
        "survival": (ssi.value, 0.20),
    })


def model_confidence(f: FeatureVector, data_age_minutes: float, sources_agree: bool | None) -> Score:
    """MCI: data quality & coverage. Similarity to known history is added once a model exists."""
    fresh = _clip(1 - data_age_minutes / 60.0)
    agree = None if sources_agree is None else (1.0 if sources_agree else 0.3)
    return _weighted("MCI", {"coverage": (f.coverage(), 0.5), "freshness": (fresh, 0.3), "agreement": (agree, 0.2)},
                     min_coverage=0.3)


@dataclass
class ScoreSet:
    prs: Score
    dqi: Score
    mri: Score
    epi: Score
    ssi: Score
    swp: Score
    mci: Score

    def as_dict(self) -> dict[str, float | None]:
        return {s.name: None if s.value is None else round(s.value, 4)
                for s in (self.prs, self.dqi, self.mri, self.epi, self.ssi, self.swp, self.mci)}


def score_all(f: FeatureVector, data_age_minutes: float = 0.0, sources_agree: bool | None = None) -> ScoreSet:
    dqi = demand_quality(f)
    mri = manipulation_risk(f)
    ssi = structural_survival(f)
    return ScoreSet(prs=peak_risk(f), dqi=dqi, mri=mri, epi=explosion_propensity(f, dqi, mri), ssi=ssi,
                    swp=second_wave(f, ssi), mci=model_confidence(f, data_age_minutes, sources_agree))
