"""Reference strategies (block A) and experimental portfolios, expressed as configuration only.

'basic' / 'strict' / '+trailing' / '+context' reproduce the owner's external research as closely as
its description allows; every threshold is a HYPOTHESIS under test, not a validated policy.
"""

from __future__ import annotations

from decimal import Decimal

from aegis.config import PostGradEntryConfig, PostGradExitConfig, StrategyConfig

STRICT = PostGradEntryConfig()
BASIC = PostGradEntryConfig(min_liquidity_eur=Decimal("10000"), min_volume_m5_usd=None, min_buy_sell_ratio_m5=None,
                            min_price_change_m5_pct=None)
HOLD = PostGradExitConfig(policy="hold")
TRAILING = PostGradExitConfig(policy="rules", trailing_activate_multiple=Decimal("1.0"), trailing_pct=Decimal("0.30"))

REFERENCE: dict[str, tuple[PostGradEntryConfig, PostGradExitConfig]] = {
    "basic+hold": (BASIC, HOLD),
    "basic+trailing": (BASIC, TRAILING),
    "strict+hold": (STRICT, HOLD),
    "strict+trailing": (STRICT, TRAILING),
    "strict+trailing+context_raw": (STRICT.model_copy(update={"context_min_opportunities_24h": 20}), TRAILING),
    "strict+trailing+context_rate": (STRICT.model_copy(update={"context_min_opportunity_rate_24h": Decimal("0.05")}),
                                     TRAILING),
    "strict+aegis_exit": (STRICT, PostGradExitConfig(policy="aegis")),
}


def strategy(entry: PostGradEntryConfig, exit_cfg: PostGradExitConfig, base: StrategyConfig | None = None,
             **capital: object) -> StrategyConfig:
    b = base or StrategyConfig()
    cap = b.capital.model_copy(update=capital) if capital else b.capital
    return b.model_copy(update={"postgrad_entry": entry, "postgrad_exit": exit_cfg, "capital": cap})
