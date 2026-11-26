"""Configuration.

Two layers, deliberately separate:

* `Settings` — environment/deployment (database URL, environment name, execution mode, secrets).
  Comes ONLY from environment variables / `.env` (never committed).
* `StrategyConfig` — economic & research parameters. Versioned by content hash and persisted in
  `configuration_versions` so every decision can be traced to the exact parameters used.
  Every threshold here is an EXPERIMENTAL default to be evaluated by backtesting, not a truth.

The learning system can propose new StrategyConfig values but can never modify `RiskLimits`
(hard safety limits) — those are validated separately and frozen at process start.
"""

from __future__ import annotations

import enum
import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.core.hashing import stable_hash


class Environment(enum.StrEnum):
    LOCAL = "local"
    DEV = "dev"
    SIMULATION = "simulation"  # permanent paper-trading deployment
    PRODUCTION = "production"  # controlled environment; real execution still disabled


class ExecutionMode(enum.StrEnum):
    HISTORICAL_BACKTEST = "HISTORICAL_BACKTEST"
    PAPER_TRADING = "PAPER_TRADING"
    SHADOW_TRADING = "SHADOW_TRADING"
    LIVE_DISABLED = "LIVE_DISABLED"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    env: Environment = Environment.LOCAL
    database_url: str = "sqlite:///var/aegis-local.sqlite"
    execution_mode: ExecutionMode = ExecutionMode.PAPER_TRADING
    strategy_file: Path | None = None
    log_level: str = "INFO"
    worker_id: str = "worker-1"
    cycle_interval_seconds: int = Field(default=300, ge=30)
    # Bearer token protecting mutating API endpoints (kill switch). Empty => mutations disabled.
    api_control_token: str = ""
    api_cors_origins: str = "http://localhost:3000"
    # Optional provider keys. Free tiers work without them (validated 2026-11-26).
    coingecko_api_key: str = ""
    helius_api_key: str = ""
    jupiter_api_key: str = ""
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    http_user_agent: str = "aegis-research/0.2"

    @model_validator(mode="after")
    def _guard_live(self) -> Settings:
        # There is intentionally no LIVE mode. LIVE_DISABLED exists to make the absence explicit.
        return self


class RiskLimits(BaseModel):
    """Hard limits. Not learnable. Changing them requires a code/config change by the owner."""

    model_config = ConfigDict(frozen=True)

    max_monthly_external_contribution_eur: Decimal = Decimal("100")
    max_ticket_eur: Decimal = Decimal("10")
    max_open_positions: int = 5000
    max_position_cost_fraction_of_equity: Decimal = Decimal("0.05")
    max_buys_per_cycle: int = 5
    max_data_staleness_minutes_for_buys: int = 30
    max_fx_staleness_hours: int = 96  # ECB reference rates skip weekends/holidays
    max_consecutive_source_failures: int = 5
    leverage_allowed: bool = False  # never True; kept explicit for audits

    @model_validator(mode="after")
    def _no_leverage(self) -> RiskLimits:
        if self.leverage_allowed:
            raise ValueError("Leverage is not permitted in AEGIS")
        return self


class BucketWeights(BaseModel):
    exploration: Decimal = Decimal("0.40")
    selection: Decimal = Decimal("0.45")
    second_chance: Decimal = Decimal("0.15")

    @model_validator(mode="after")
    def _sum_to_one(self) -> BucketWeights:
        total = self.exploration + self.selection + self.second_chance
        if total != Decimal(1):
            raise ValueError(f"bucket weights must sum to 1, got {total}")
        return self


class CapitalConfig(BaseModel):
    monthly_contribution_eur: Decimal = Decimal("100")
    ticket_eur: Decimal = Decimal("1")
    # All-in ticket: rent deposit + fees + swap input <= ticket. Respects the monthly budget strictly.
    ticket_includes_costs: bool = True
    buckets: BucketWeights = BucketWeights()
    # Pacing: daily budget = remaining_month_budget / remaining_days * pacing_multiplier.
    pacing_multiplier: Decimal = Decimal("1.5")
    # Recycled capital is reinvested at most this fraction of its balance per day.
    recycled_daily_fraction: Decimal = Decimal("0.10")
    # Profit reserve (strategy policy) as a fraction of each realised gain. Separate from tax reserve.
    profit_reserve_fraction: Decimal = Decimal("0.10")
    # Keep cash:reserve >= estimated tax on YTD realised gains * this factor.
    tax_reserve_coverage: Decimal = Decimal("1.0")
    buy_from_recycled_first: bool = True


class DiscoveryConfig(BaseModel):
    max_price_eur: Decimal | None = Decimal("0.001")
    min_market_cap_eur: Decimal = Decimal("25000")
    max_market_cap_eur: Decimal = Decimal("5000000")
    # If circulating market cap is unknown we do NOT substitute FDV silently. Instead we apply this
    # explicit FDV range (also experimental) and tag the candidate with `cap_unknown_used_fdv`.
    use_fdv_range_when_cap_unknown: bool = True
    min_fdv_eur: Decimal = Decimal("25000")
    max_fdv_eur: Decimal = Decimal("10000000")
    min_liquidity_eur: Decimal = Decimal("10000")
    min_volume_24h_eur: Decimal = Decimal("5000")
    min_token_age_hours: Decimal = Decimal("24")
    min_txns_24h: int = 100
    min_unique_traders_24h: int = 50
    allow_bonding_curve_pools: bool = False
    new_pools_pages: int = Field(default=2, ge=0, le=10)
    trending_pages: int = Field(default=1, ge=0, le=10)
    max_tracked_tokens: int = 20000


class SecurityConfig(BaseModel):
    block_mint_authority: bool = True
    block_freeze_authority: bool = True
    block_permanent_delegate: bool = True
    block_transfer_hook: bool = True
    block_non_transferable: bool = True
    block_default_account_frozen: bool = True
    max_transfer_fee_bps: int = 0
    max_top10_holder_pct: Decimal = Decimal("50")
    max_dev_holding_pct: Decimal = Decimal("20")
    require_sell_route: bool = True
    max_roundtrip_loss_pct: Decimal = Decimal("15")
    # If a CRITICAL check cannot be evaluated (data unavailable) => block. Unknown is not safe.
    unknown_critical_blocks: bool = True
    assessment_ttl_minutes: int = 360


class ExecutionConfig(BaseModel):
    slippage_bps: int = 300
    quote_ttl_seconds: int = 20
    # Used only when no executable quote is available (CPMM estimate). Jupiter quotes embed pool fees.
    estimated_pool_fee_bps: int = 30
    aggregator_fee_bps: int = 0  # lite swap API: 0 observed; Ultra: 10 observed on 2026-11-26
    base_fee_lamports: int = 5000
    priority_fee_lamports: int = 100_000
    # Fallback when RPC rent query fails; conservative (historical 165-byte rent).
    fallback_ata_rent_lamports: int = 2_039_280
    max_cost_to_ticket_pct: Decimal = Decimal("15")
    max_price_impact_pct: Decimal = Decimal("3")
    simulated_latency_seconds: int = 2
    prefer_executable_quotes: bool = True


class ExitConfig(BaseModel):
    # Net liquidation multiple (liquidation value / remaining cost basis) to recover initial capital.
    capital_recovery_multiple: Decimal = Decimal("2.0")
    residual_min_fraction: Decimal = Decimal("0.10")
    prs_partial_threshold: Decimal = Decimal("0.55")
    prs_full_threshold: Decimal = Decimal("0.85")
    partial_sell_fraction: Decimal = Decimal("0.33")
    trailing_activate_multiple: Decimal = Decimal("3.0")
    trailing_vol_k: Decimal = Decimal("3.0")
    trailing_min_pct: Decimal = Decimal("0.25")
    trailing_max_pct: Decimal = Decimal("0.60")
    critical_liquidity_eur: Decimal = Decimal("1000")
    min_sale_eur: Decimal = Decimal("0.10")
    dust_close_after_days: int = 7
    reevaluate_minutes_when_hot: int = 2
    # Smallest fraction worth selling (avoids dust orders); residual positions only exit on critical events.
    min_sell_fraction: Decimal = Decimal("0.05")
    # Recover remaining capital from structurally deteriorating losers (collapse / liquidity drain + sellers).
    deterioration_exit: bool = True
    deterioration_liquidity_drop_h24: Decimal = Decimal("0.50")
    deterioration_max_buyer_share_h24: Decimal = Decimal("0.45")
    # Optional naive stop-loss on net liquidation multiple (DISABLED by default; for backtest comparison).
    stop_loss_multiple: Decimal | None = None


class ReentryConfig(BaseModel):
    min_hours_since_exit: Decimal = Decimal("24")
    min_drawdown_from_ath: Decimal = Decimal("0.80")
    min_rebound_from_low: Decimal = Decimal("1.5")
    min_buyer_share_h1: Decimal = Decimal("0.55")
    min_liquidity_retention: Decimal = Decimal("0.30")
    max_entries_per_token: int = 3


class ValuationConfig(BaseModel):
    stale_after_minutes: int = 30
    stale_haircut: Decimal = Decimal("0.50")
    unvalued_after_hours: int = 48


class MonitoringConfig(BaseModel):
    """Adaptive refresh intervals (seconds) by priority class."""

    critical_risk: int = 60
    explosion: int = 120
    position_hot: int = 180
    position_stable: int = 600
    accumulation: int = 900
    former_winner: int = 1800
    watchlist: int = 3600
    cold: int = 6 * 3600
    max_pool_refreshes_per_cycle: int = 300
    security_checks_per_cycle: int = 6


class TaxConfigES(BaseModel):
    """Spanish IRPF savings base (base del ahorro). VERIFY with a tax professional each year."""

    jurisdiction: str = "ES"
    # (upper bound EUR or None for infinity, rate). 2025+ scale per Ley 7/2024 — verify.
    brackets: list[tuple[Decimal | None, Decimal]] = [
        (Decimal("6000"), Decimal("0.19")),
        (Decimal("50000"), Decimal("0.21")),
        (Decimal("200000"), Decimal("0.23")),
        (Decimal("300000"), Decimal("0.27")),
        (None, Decimal("0.30")),
    ]
    lot_method: str = "FIFO"


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "aegis-baseline"
    version: str = "0.2.0"
    seed: int = 20240101
    capital: CapitalConfig = CapitalConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    security: SecurityConfig = SecurityConfig()
    execution: ExecutionConfig = ExecutionConfig()
    exits: ExitConfig = ExitConfig()
    reentry: ReentryConfig = ReentryConfig()
    valuation: ValuationConfig = ValuationConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    tax: TaxConfigES = TaxConfigES()

    @property
    def config_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))

    @classmethod
    def load(cls, path: Path | None) -> StrategyConfig:
        if path is None:
            return cls()
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    def check_against(self, limits: RiskLimits) -> None:
        if self.capital.monthly_contribution_eur > limits.max_monthly_external_contribution_eur:
            raise ValueError("monthly contribution exceeds hard limit")
        if self.capital.ticket_eur > limits.max_ticket_eur:
            raise ValueError("ticket exceeds hard limit")
        if self.capital.ticket_eur <= 0:
            raise ValueError("ticket must be positive")
