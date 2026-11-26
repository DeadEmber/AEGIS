"""Token Discovery Engine: universes and experimental eligibility filters.

Filters are explicit and every evaluation returns per-filter outcomes (pass/fail/unknown + value),
so rejections are explainable and can be replayed in the Research Lab with other thresholds.
Circulating market cap is never silently replaced by FDV: if cap is unknown and the config allows,
the FDV range is applied as a SEPARATE, explicitly-labelled filter.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from aegis.config import DiscoveryConfig
from aegis.marketdata.fx import usd_to_eur
from aegis.marketdata.models import PoolObservation, TokenProfile


@dataclass(frozen=True)
class FilterResult:
    name: str
    outcome: str  # pass|fail|unknown
    value: str | None = None
    threshold: str | None = None


@dataclass
class Eligibility:
    eligible: bool
    results: list[FilterResult] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [r.name for r in self.results if r.outcome != "pass"]


def _rng(name: str, v: Decimal | None, lo: Decimal | None, hi: Decimal | None) -> FilterResult:
    if v is None:
        return FilterResult(name, "unknown")
    ok = (lo is None or v >= lo) and (hi is None or v <= hi)
    return FilterResult(name, "pass" if ok else "fail", f"{v:.6g}", f"[{lo}, {hi}]")


def evaluate_eligibility(obs: PoolObservation, eur_usd: Decimal, now: dt.datetime, cfg: DiscoveryConfig,
                         profile: TokenProfile | None = None, token_created_at: dt.datetime | None = None) -> Eligibility:
    res: list[FilterResult] = []
    tags: list[str] = []
    price_eur = usd_to_eur(obs.price_usd, eur_usd)
    if cfg.max_price_eur is not None:
        res.append(_rng("price_eur", price_eur, None, cfg.max_price_eur))
    cap_usd = obs.market_cap_usd
    if cap_usd is None and profile is not None and profile.market_cap_usd is not None and \
            profile.circulating_supply is not None:
        cap_usd = profile.market_cap_usd
        tags.append("cap_from_profile_circulating_supply")
    cap_eur = usd_to_eur(cap_usd, eur_usd)
    if cap_eur is not None:
        res.append(_rng("market_cap_eur", cap_eur, cfg.min_market_cap_eur, cfg.max_market_cap_eur))
    elif cfg.use_fdv_range_when_cap_unknown:
        tags.append("cap_unknown_used_fdv")
        res.append(_rng("fdv_eur(cap_unknown)", usd_to_eur(obs.fdv_usd, eur_usd), cfg.min_fdv_eur, cfg.max_fdv_eur))
    else:
        res.append(FilterResult("market_cap_eur", "unknown"))
    res.append(_rng("liquidity_eur", usd_to_eur(obs.liquidity_usd, eur_usd), cfg.min_liquidity_eur, None))
    res.append(_rng("volume_24h_eur", usd_to_eur(obs.w("h24").volume_usd, eur_usd), cfg.min_volume_24h_eur, None))
    created = token_created_at or (profile.created_at if profile else None)
    if created is None and obs.pool_created_at is not None:
        created = obs.pool_created_at
        tags.append("age_from_pool_creation")
    age_h = None if created is None else Decimal((now - created).total_seconds()) / 3600
    res.append(_rng("token_age_hours", age_h, cfg.min_token_age_hours, None))
    w = obs.w("h24")
    txns = None if w.buys is None or w.sells is None else Decimal(w.buys + w.sells)
    res.append(_rng("txns_24h", txns, Decimal(cfg.min_txns_24h), None))
    traders = None if w.buyers is None or w.sellers is None else Decimal(w.buyers + w.sellers)
    res.append(_rng("traders_24h(addresses)", traders, Decimal(cfg.min_unique_traders_24h), None))
    if obs.pool_type == "bonding_curve" and not cfg.allow_bonding_curve_pools:
        res.append(FilterResult("pool_type", "fail", obs.pool_type, "amm|clmm"))
    else:
        res.append(FilterResult("pool_type", "pass", obs.pool_type))
    # universes (descriptive tags, not filters)
    if age_h is not None and age_h < 72:
        tags.append("universe:new_launch")
    if price_eur is not None and cfg.max_price_eur is not None and price_eur <= cfg.max_price_eur:
        tags.append("universe:low_unit_price")
    if (cap_eur or usd_to_eur(obs.fdv_usd, eur_usd) or Decimal(10**12)) < Decimal(1_000_000):
        tags.append("universe:microcap")
    ch = obs.w("h1").price_change_pct
    if ch is not None and ch > 20:
        tags.append("universe:accelerating")
    return Eligibility(all(r.outcome == "pass" for r in res), res, tags)


def primary_pool(observations: list[PoolObservation], allow_bonding_curve: bool) -> PoolObservation | None:
    """Most liquid tradable pool for the token (ties: most recent observation)."""
    cands = [o for o in observations if o.price_usd is not None and o.price_usd > 0 and
             (allow_bonding_curve or o.pool_type != "bonding_curve")]
    if not cands:
        cands = [o for o in observations if o.price_usd is not None and o.price_usd > 0]
    if not cands:
        return None
    return max(cands, key=lambda o: (o.liquidity_usd or Decimal(-1), o.observed_at))
