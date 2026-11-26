"""Relational schema (SQLAlchemy Core). Portable across SQLite (local/tests) and PostgreSQL/Supabase.

Conventions:
- All timestamps are timezone-aware UTC (`UtcDateTime`).
- Money/quantities are exact decimals (`Dec`): NUMERIC in PostgreSQL, TEXT in SQLite (no float).
- `observed_at` = when AEGIS learned the fact; `event_time` = when it happened per the source.
- Ledger tables are append-only; corrections are new reversing transactions.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator

from aegis.core.clock import UTC


class Dec(TypeDecorator):
    """Exact decimal: NUMERIC (arbitrary precision) on PostgreSQL, canonical string on SQLite."""

    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(80))
        return dialect.type_descriptor(Numeric(asdecimal=True))

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float not allowed in Dec column")
        value = Decimal(value)
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return Decimal(value) if not isinstance(value, Decimal) else value


class UtcDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if value.tzinfo is None:  # SQLite returns naive
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _ts(name: str, nullable: bool = False, **kw) -> Column:  # type: ignore[no-untyped-def]
    return Column(name, UtcDateTime(), nullable=nullable, **kw)


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

PK = lambda: Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)  # noqa: E731

# --------------------------------------------------------------------------- reference / market data
networks = Table(
    "networks", metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("native_symbol", String(16), nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
)

tokens = Table(
    "tokens", metadata, PK(),
    Column("network", String(32), ForeignKey("networks.id"), nullable=False),
    Column("address", String(64), nullable=False),
    Column("symbol", String(64)),
    Column("name", String(200)),
    Column("decimals", Integer),
    Column("token_program", String(64)),
    Column("launchpad", String(64)),
    _ts("chain_created_at", nullable=True),
    _ts("first_seen_at"),
    _ts("last_seen_at"),
    Column("discovery_source", String(64)),
    Column("universes", JSON, nullable=False, default=list),
    Column("market_state", String(24), nullable=False, default="DISCOVERED"),
    _ts("market_state_since"),
    Column("ath_price_usd", Dec()),
    _ts("ath_at", nullable=True),
    Column("low_since_ath_usd", Dec()),
    Column("explosion_count", Integer, nullable=False, default=0),
    Column("entries", Integer, nullable=False, default=0),
    _ts("last_exit_at", nullable=True),
    Column("liquidity_at_exit_usd", Dec()),
    Column("meta", JSON, nullable=False, default=dict),
    UniqueConstraint("network", "address"),
)
Index("ix_tokens_market_state", tokens.c.market_state)

pools = Table(
    "pools", metadata, PK(),
    Column("network", String(32), ForeignKey("networks.id"), nullable=False),
    Column("address", String(64), nullable=False),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("token_side", String(8), nullable=False),  # 'base' | 'quote'
    Column("counter_asset", String(64)),
    Column("dex", String(64)),
    Column("pool_type", String(24), nullable=False, default="unknown"),  # amm|clmm|bonding_curve|unknown
    _ts("source_created_at", nullable=True),
    _ts("first_seen_at"),
    _ts("last_seen_at"),
    Column("is_primary", Boolean, nullable=False, default=False),
    UniqueConstraint("network", "address"),
    CheckConstraint("token_side in ('base','quote')", name="token_side"),
)
Index("ix_pools_token", pools.c.token_id)

exchange_rates = Table(
    "exchange_rates", metadata, PK(),
    Column("base", String(16), nullable=False),
    Column("quote", String(16), nullable=False),
    Column("rate", Dec(), nullable=False),  # 1 base = rate quote
    Column("source", String(64), nullable=False),
    _ts("rate_time"),
    _ts("fetched_at"),
    CheckConstraint("rate > 0", name="positive_rate"),
)
Index("ix_fx_pair_time", exchange_rates.c.base, exchange_rates.c.quote, exchange_rates.c.fetched_at)

_WINDOWS = ("m5", "h1", "h6", "h24")
market_snapshots = Table(
    "market_snapshots", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("pool_id", BigInteger, ForeignKey("pools.id"), nullable=False),
    Column("source", String(32), nullable=False),
    _ts("observed_at"),
    _ts("event_time", nullable=True),
    Column("price_usd", Dec()),
    Column("price_eur", Dec()),
    Column("eur_usd_rate_id", BigInteger, ForeignKey("exchange_rates.id")),
    Column("liquidity_usd", Dec()),
    Column("fdv_usd", Dec()),
    Column("market_cap_usd", Dec()),
    *[Column(f"volume_{w}_usd", Dec()) for w in _WINDOWS],
    *[Column(f"price_change_{w}", Dec()) for w in _WINDOWS],
    *[Column(f"buys_{w}", Integer) for w in _WINDOWS],
    *[Column(f"sells_{w}", Integer) for w in _WINDOWS],
    *[Column(f"buyers_{w}", Integer) for w in _WINDOWS],
    *[Column(f"sellers_{w}", Integer) for w in _WINDOWS],
    Column("quality_flags", JSON, nullable=False, default=list),
    Column("raw", JSON),
)
Index("ix_snap_token_time", market_snapshots.c.token_id, market_snapshots.c.observed_at)
Index("ix_snap_pool_time", market_snapshots.c.pool_id, market_snapshots.c.observed_at)

token_profiles = Table(
    "token_profiles", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("source", String(32), nullable=False),
    _ts("observed_at"),
    Column("holders_count", Integer),
    Column("top10_pct", Dec()),
    Column("dev_holding_pct", Dec()),
    Column("circulating_supply", Dec()),
    Column("total_supply", Dec()),
    Column("market_cap_usd", Dec()),
    Column("organic_score", Dec()),
    Column("mint_authority", String(64)),
    Column("freeze_authority", String(64)),
    Column("raw", JSON),
)
Index("ix_profile_token_time", token_profiles.c.token_id, token_profiles.c.observed_at)

candles = Table(
    "candles", metadata,
    Column("pool_id", BigInteger, ForeignKey("pools.id"), primary_key=True),
    Column("timeframe", String(8), primary_key=True),
    _ts("ts", primary_key=True),
    Column("open_usd", Dec(), nullable=False),
    Column("high_usd", Dec(), nullable=False),
    Column("low_usd", Dec(), nullable=False),
    Column("close_usd", Dec(), nullable=False),
    Column("volume_usd", Dec(), nullable=False),
    Column("source", String(32), nullable=False),
    _ts("fetched_at"),
)

trades = Table(
    "trades", metadata, PK(),
    Column("pool_id", BigInteger, ForeignKey("pools.id"), nullable=False),
    Column("tx_hash", String(100), nullable=False),
    Column("source_trade_id", String(200), nullable=False),
    _ts("block_time"),
    Column("kind", String(8), nullable=False),
    Column("wallet", String(64)),
    Column("token_amount", Dec()),
    Column("volume_usd", Dec()),
    Column("price_usd", Dec()),
    _ts("fetched_at"),
    UniqueConstraint("pool_id", "source_trade_id"),
)

liquidity_events = Table(
    "liquidity_events", metadata, PK(),
    Column("pool_id", BigInteger, ForeignKey("pools.id"), nullable=False),
    _ts("detected_at"),
    Column("kind", String(24), nullable=False),  # drop|add|migration
    Column("liquidity_before_usd", Dec()),
    Column("liquidity_after_usd", Dec()),
    Column("change_pct", Dec()),
)

wallets = Table(
    "wallets", metadata, PK(),
    Column("network", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("labels", JSON, nullable=False, default=list),
    _ts("first_seen_at"),
    UniqueConstraint("network", "address"),
)

wallet_relationships = Table(
    "wallet_relationships", metadata, PK(),
    Column("wallet_a", BigInteger, ForeignKey("wallets.id"), nullable=False),
    Column("wallet_b", BigInteger, ForeignKey("wallets.id"), nullable=False),
    Column("kind", String(32), nullable=False),  # common_funder|co_trading|creator_link
    Column("confidence", Dec(), nullable=False),  # inferred, never presented as fact
    Column("evidence", JSON, nullable=False, default=dict),
    _ts("inferred_at"),
)

# --------------------------------------------------------------------------- intelligence
security_assessments = Table(
    "security_assessments", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("assessed_at"),
    _ts("expires_at"),
    Column("verdict", String(12), nullable=False),  # PASS|RISK|BLOCK|UNKNOWN
    Column("engine_version", String(32), nullable=False),
    Column("findings", JSON, nullable=False),
    Column("inputs_hash", String(32), nullable=False),
)
Index("ix_secassess_token_time", security_assessments.c.token_id, security_assessments.c.assessed_at)

security_events = Table(
    "security_events", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("occurred_at"),
    Column("kind", String(48), nullable=False),
    Column("status", String(16), nullable=False),  # CONFIRMED|INDICATOR|UNKNOWN
    Column("details", JSON, nullable=False, default=dict),
)

token_states = Table(
    "token_states", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("valid_from"),
    Column("state", String(24), nullable=False),
    Column("previous_state", String(24)),
    Column("reason", Text, nullable=False),
    Column("evidence", JSON, nullable=False, default=dict),
    Column("engine_version", String(32), nullable=False),
)
Index("ix_token_states_token_time", token_states.c.token_id, token_states.c.valid_from)

features = Table(
    "features", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("as_of"),
    Column("feature_set_version", String(32), nullable=False),
    Column("values", JSON, nullable=False),
    Column("missing", JSON, nullable=False, default=dict),
)
Index("ix_features_token_time", features.c.token_id, features.c.as_of)

model_versions = Table(
    "model_versions", metadata, PK(),
    Column("name", String(64), nullable=False),
    Column("version", String(32), nullable=False),
    Column("kind", String(24), nullable=False),  # heuristic|logistic|lightgbm|survival|knn
    Column("status", String(16), nullable=False),  # champion|challenger|blocked|retired
    Column("is_calibrated_probability", Boolean, nullable=False, default=False),
    Column("params", JSON, nullable=False, default=dict),
    Column("metrics", JSON, nullable=False, default=dict),
    Column("data_hash", String(64)),
    _ts("created_at"),
    _ts("validated_at", nullable=True),
    UniqueConstraint("name", "version"),
)

predictions = Table(
    "predictions", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("model_version_id", BigInteger, ForeignKey("model_versions.id"), nullable=False),
    _ts("as_of"),
    Column("target", String(48), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("value", Dec(), nullable=False),
    Column("uncertainty", Dec()),
)
Index("ix_predictions_token_time", predictions.c.token_id, predictions.c.as_of)

explosion_events = Table(
    "explosion_events", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("started_at"),
    _ts("peak_at", nullable=True),
    _ts("ended_at", nullable=True),
    Column("wave_number", Integer, nullable=False),
    Column("base_price_usd", Dec(), nullable=False),
    Column("peak_price_usd", Dec()),
    Column("peak_multiple", Dec()),
    Column("peak_liquidity_usd", Dec()),
)

recovery_events = Table(
    "recovery_events", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    _ts("detected_at"),
    Column("drawdown_from_ath", Dec(), nullable=False),
    Column("rebound_multiple", Dec(), nullable=False),
    Column("classification", String(24)),  # disappearance|false_revival|cyclical|organic|structural
)

# --------------------------------------------------------------------------- decisions & execution
decisions = Table(
    "decisions", metadata, PK(),
    _ts("decided_at"),
    Column("token_id", BigInteger, ForeignKey("tokens.id")),
    Column("position_id", BigInteger, ForeignKey("positions.id")),
    Column("action", String(16), nullable=False),
    Column("bucket", String(16)),
    Column("fraction", Dec()),
    Column("reasons", JSON, nullable=False),
    Column("scores", JSON, nullable=False, default=dict),
    Column("inputs_hash", String(32), nullable=False),
    Column("strategy_hash", String(32), nullable=False),
    Column("engine_version", String(32), nullable=False),
    Column("mode", String(24), nullable=False),
    Column("outcome", String(16), nullable=False, default="pending"),
)
Index("ix_decisions_time", decisions.c.decided_at)
Index("ix_decisions_token_time", decisions.c.token_id, decisions.c.decided_at)

orders = Table(
    "orders", metadata, PK(),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("decision_id", BigInteger, ForeignKey("decisions.id")),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("side", String(4), nullable=False),
    Column("mode", String(24), nullable=False),
    _ts("requested_at"),
    Column("notional_eur", Dec()),
    Column("token_qty", Dec()),
    Column("quote", JSON),
    _ts("quote_expires_at", nullable=True),
    Column("status", String(12), nullable=False),  # PENDING|FILLED|REJECTED|EXPIRED|FAILED
    Column("reject_reason", Text),
    CheckConstraint("side in ('BUY','SELL')", name="side"),
)

executions = Table(
    "executions", metadata, PK(),
    Column("order_id", BigInteger, ForeignKey("orders.id"), nullable=False, unique=True),
    _ts("executed_at"),
    Column("method", String(24), nullable=False),  # jupiter_quote|cpmm_estimate|burn_close
    Column("token_qty", Dec(), nullable=False),
    Column("cash_eur", Dec(), nullable=False),  # +in (sell) / -out (buy), all-in
    Column("swap_amount_eur", Dec(), nullable=False),
    Column("price_eur_effective", Dec()),
    Column("reference_price_eur", Dec()),
    Column("price_impact_pct", Dec()),
    Column("pool_fee_eur", Dec(), nullable=False),
    Column("aggregator_fee_eur", Dec(), nullable=False),
    Column("network_fee_eur", Dec(), nullable=False),
    Column("priority_fee_eur", Dec(), nullable=False),
    Column("deposit_eur", Dec(), nullable=False),
    Column("sol_eur", Dec(), nullable=False),
    Column("latency_ms", Integer),
    Column("details", JSON, nullable=False, default=dict),
)

positions = Table(
    "positions", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("book", String(24), nullable=False, default="operational"),
    _ts("opened_at"),
    _ts("closed_at", nullable=True),
    Column("status", String(20), nullable=False),  # OPEN|CAPITAL_RECOVERED|RESIDUAL|CLOSED|DUST
    Column("bucket", String(16), nullable=False),
    Column("entry_number", Integer, nullable=False),
    Column("qty", Dec(), nullable=False),
    Column("cost_basis_eur", Dec(), nullable=False),
    Column("deposit_eur", Dec(), nullable=False),
    Column("deposit_lamports", BigInteger, nullable=False),
    Column("invested_eur_total", Dec(), nullable=False),
    Column("realized_proceeds_eur", Dec(), nullable=False),
    Column("realized_pnl_eur", Dec(), nullable=False),
    Column("peak_price_eur", Dec()),
    Column("peak_multiple", Dec()),
    Column("capital_recovered", Boolean, nullable=False, default=False),
    Column("partial_sells", Integer, nullable=False, default=0),
    Column("last_mark_eur", Dec()),
    Column("last_liquidation_eur", Dec()),
    _ts("last_valued_at", nullable=True),
    Column("valuation_method", String(24)),
    CheckConstraint("qty >= 0", name="qty_nonneg"),
)
# At most one non-closed position per token and book.
Index(
    "uq_positions_open_token", positions.c.token_id, positions.c.book, unique=True,
    sqlite_where=positions.c.closed_at.is_(None), postgresql_where=positions.c.closed_at.is_(None),
)

position_lots = Table(
    "position_lots", metadata, PK(),
    Column("position_id", BigInteger, ForeignKey("positions.id"), nullable=False),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("execution_id", BigInteger, ForeignKey("executions.id"), nullable=False),
    _ts("acquired_at"),
    Column("qty_initial", Dec(), nullable=False),
    Column("qty_remaining", Dec(), nullable=False),
    Column("cost_initial_eur", Dec(), nullable=False),
    Column("cost_remaining_eur", Dec(), nullable=False),
    _ts("closed_at", nullable=True),
)
Index("ix_lots_token_acq", position_lots.c.token_id, position_lots.c.acquired_at)

lot_disposals = Table(
    "lot_disposals", metadata, PK(),
    Column("lot_id", BigInteger, ForeignKey("position_lots.id"), nullable=False),
    Column("execution_id", BigInteger, ForeignKey("executions.id"), nullable=False),
    _ts("disposed_at"),
    Column("qty", Dec(), nullable=False),
    Column("cost_basis_eur", Dec(), nullable=False),
    Column("proceeds_eur", Dec(), nullable=False),
    Column("gain_eur", Dec(), nullable=False),
)

# --------------------------------------------------------------------------- accounting
ledger_transactions = Table(
    "ledger_transactions", metadata, PK(),
    _ts("occurred_at"),
    _ts("recorded_at"),
    Column("kind", String(32), nullable=False),
    Column("description", Text, nullable=False),
    Column("ref_type", String(32)),
    Column("ref_id", BigInteger),
    Column("idempotency_key", String(128), nullable=False, unique=True),
)

ledger_postings = Table(
    "ledger_postings", metadata, PK(),
    Column("transaction_id", BigInteger, ForeignKey("ledger_transactions.id"), nullable=False),
    Column("account", String(64), nullable=False),
    Column("amount_eur", Dec(), nullable=False),  # debit > 0, credit < 0
    Column("memo", Text),
)
Index("ix_postings_account", ledger_postings.c.account)
Index("ix_postings_tx", ledger_postings.c.transaction_id)

contributions = Table(
    "contributions", metadata, PK(),
    Column("period", String(7), nullable=False, unique=True),  # YYYY-MM
    _ts("occurred_at"),
    Column("amount_eur", Dec(), nullable=False),
    Column("ledger_tx_id", BigInteger, ForeignKey("ledger_transactions.id"), nullable=False),
)

portfolio_snapshots = Table(
    "portfolio_snapshots", metadata, PK(),
    _ts("as_of"),
    Column("book", String(24), nullable=False, default="operational"),
    Column("cash_investment_eur", Dec(), nullable=False),
    Column("cash_recycled_eur", Dec(), nullable=False),
    Column("cash_reserve_eur", Dec(), nullable=False),
    Column("deposits_eur", Dec(), nullable=False),
    Column("positions_cost_eur", Dec(), nullable=False),
    Column("positions_mark_eur", Dec(), nullable=False),
    Column("positions_liquidation_eur", Dec(), nullable=False),
    Column("open_positions", Integer, nullable=False),
    Column("stale_positions", Integer, nullable=False),
    Column("unvalued_positions", Integer, nullable=False),
    Column("contributions_total_eur", Dec(), nullable=False),
    Column("withdrawals_total_eur", Dec(), nullable=False),
    Column("external_flow_since_last_eur", Dec(), nullable=False),
    Column("realized_pnl_total_eur", Dec(), nullable=False),
    Column("costs_total_eur", Dec(), nullable=False),
    Column("est_tax_realized_ytd_eur", Dec(), nullable=False),
    Column("net_worth_liquidable_eur", Dec(), nullable=False),
    Column("objective_eur", Dec(), nullable=False),
    Column("reconciliation_ok", Boolean, nullable=False),
    Column("details", JSON, nullable=False, default=dict),
)
Index("ix_portfolio_snapshots_time", portfolio_snapshots.c.as_of)

shadow_positions = Table(
    "shadow_positions", metadata, PK(),
    Column("token_id", BigInteger, ForeignKey("tokens.id"), nullable=False),
    Column("cohort", String(32), nullable=False),  # rejected|not_selected|exploration_alt|...
    Column("reason", Text, nullable=False),
    _ts("opened_at"),
    Column("entry_price_eur", Dec(), nullable=False),
    Column("notional_eur", Dec(), nullable=False),
    Column("est_entry_cost_eur", Dec(), nullable=False),
    Column("qty", Dec(), nullable=False),
    Column("peak_price_eur", Dec(), nullable=False),
    _ts("peak_at"),
    Column("last_price_eur", Dec(), nullable=False),
    Column("last_liquidity_usd", Dec()),
    _ts("last_observed_at"),
    _ts("closed_at", nullable=True),
    Column("outcome", JSON, nullable=False, default=dict),
)
Index("ix_shadow_token_cohort", shadow_positions.c.token_id, shadow_positions.c.cohort)

# --------------------------------------------------------------------------- operations / audit
control_flags = Table(
    "control_flags", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", String(200), nullable=False),
    _ts("updated_at"),
    Column("updated_by", String(64), nullable=False),
    Column("reason", Text),
)

scheduler_queue = Table(
    "scheduler_queue", metadata,
    Column("token_id", BigInteger, ForeignKey("tokens.id"), primary_key=True),
    _ts("next_due_at"),
    Column("priority", Integer, nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("reason", String(64), nullable=False),
)
Index("ix_sched_due", scheduler_queue.c.next_due_at)

source_health = Table(
    "source_health", metadata,
    Column("source", String(32), primary_key=True),
    _ts("last_ok_at", nullable=True),
    _ts("last_error_at", nullable=True),
    Column("last_error", Text),
    Column("consecutive_errors", Integer, nullable=False, default=0),
    Column("calls_total", BigInteger, nullable=False, default=0),
    Column("errors_total", BigInteger, nullable=False, default=0),
    Column("rate_limited_total", BigInteger, nullable=False, default=0),
    Column("latency_ms_ewma", Dec()),
    _ts("updated_at"),
)

worker_heartbeats = Table(
    "worker_heartbeats", metadata,
    Column("worker_id", String(64), primary_key=True),
    _ts("started_at"),
    _ts("last_beat_at"),
    Column("cycles", BigInteger, nullable=False, default=0),
    Column("status", String(16), nullable=False),
    Column("version", String(32), nullable=False),
    Column("last_cycle", JSON, nullable=False, default=dict),
)

data_quality_events = Table(
    "data_quality_events", metadata, PK(),
    _ts("occurred_at"),
    Column("source", String(32), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("token_id", BigInteger, ForeignKey("tokens.id")),
    Column("details", JSON, nullable=False, default=dict),
)
Index("ix_dq_time", data_quality_events.c.occurred_at)

system_events = Table(
    "system_events", metadata, PK(),
    _ts("occurred_at"),
    Column("kind", String(48), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("message", Text, nullable=False),
    Column("details", JSON, nullable=False, default=dict),
)
Index("ix_sysevents_time", system_events.c.occurred_at)

risk_events = Table(
    "risk_events", metadata, PK(),
    _ts("occurred_at"),
    Column("kind", String(48), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("token_id", BigInteger, ForeignKey("tokens.id")),
    Column("details", JSON, nullable=False, default=dict),
    _ts("resolved_at", nullable=True),
)

configuration_versions = Table(
    "configuration_versions", metadata,
    Column("hash", String(32), primary_key=True),
    _ts("created_at"),
    Column("kind", String(24), nullable=False),  # strategy|risk_limits
    Column("label", String(64)),
    Column("config", JSON, nullable=False),
)

audit_logs = Table(
    "audit_logs", metadata, PK(),
    _ts("occurred_at"),
    Column("actor", String(64), nullable=False),
    Column("action", String(64), nullable=False),
    Column("details", JSON, nullable=False, default=dict),
)

backtest_runs = Table(
    "backtest_runs", metadata, PK(),
    _ts("created_at"),
    Column("name", String(64), nullable=False),
    Column("strategy_hash", String(32), nullable=False),
    Column("params", JSON, nullable=False),
    _ts("data_from", nullable=True),
    _ts("data_to", nullable=True),
    Column("status", String(16), nullable=False),
    Column("limitations", JSON, nullable=False, default=list),
)

backtest_results = Table(
    "backtest_results", metadata, PK(),
    Column("run_id", BigInteger, ForeignKey("backtest_runs.id"), nullable=False),
    Column("strategy", String(64), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("value", Dec()),
    Column("details", JSON, nullable=False, default=dict),
)
