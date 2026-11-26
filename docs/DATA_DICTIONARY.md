# Diccionario de datos

Convenciones:

- Todos los timestamps son UTC con zona horaria. `observed_at` indica cuándo lo supo AEGIS y
  `event_time` cuándo ocurrió según la fuente.
- Los importes y cantidades son decimales exactos (`Dec`): `NUMERIC` en PostgreSQL y texto canónico
  en SQLite. Los importes del libro están cuantizados a 1e-12 EUR.
- Un activo se identifica siempre por `(network, address)`; el ticker es metadato de presentación.
- Las tablas contables (`ledger_*`, `contributions`, `lot_disposals`, `orders`, `executions`) son
  append-only y nunca se podan.
- La retención del resto de tablas está en `storage/retention.py` y `COST_ANALYSIS.md` §3.
- Migraciones: `aegis db upgrade` (Alembic, `src/aegis/storage/migrations`).

## Tablas

<!-- AUTO:BEGIN -->
42 tablas (generado desde `aegis.storage.schema`).

### `archive_batches`



| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `table_name` | String | no |  |
| `path` | Text | no |  |
| `rows` | Integer | no |  |
| `min_id` | BigInteger | no |  |
| `max_id` | BigInteger | no |  |
| `min_ts` | UtcDateTime | sí |  |
| `max_ts` | UtcDateTime | sí |  |
| `sha256` | String | no |  |
| `schema_version` | String | no |  |
| `created_at` | UtcDateTime | no |  |
| `verified_at` | UtcDateTime | sí |  |
| `status` | String | no |  |

### `audit_logs`

Auditoría de acciones de control.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `occurred_at` | UtcDateTime | no |  |
| `actor` | String | no |  |
| `action` | String | no |  |
| `details` | JSON | no |  |

### `backtest_results`

Métricas por estrategia y ejecución.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `run_id` | BigInteger | no | FK→backtest_runs.id |
| `strategy` | String | no |  |
| `metric` | String | no |  |
| `value` | Dec | sí |  |
| `details` | JSON | no |  |

### `backtest_runs`

Ejecuciones de backtest con parámetros y limitaciones declaradas.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `created_at` | UtcDateTime | no |  |
| `name` | String | no |  |
| `strategy_hash` | String | no |  |
| `params` | JSON | no |  |
| `data_from` | UtcDateTime | sí |  |
| `data_to` | UtcDateTime | sí |  |
| `status` | String | no |  |
| `limitations` | JSON | no |  |

### `candles`

OHLCV por pool y timeframe (investigación histórica).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `pool_id` | BigInteger | no | PK |
| `timeframe` | String | no | PK |
| `ts` | UtcDateTime | no | PK |
| `open_usd` | Dec | no |  |
| `high_usd` | Dec | no |  |
| `low_usd` | Dec | no |  |
| `close_usd` | Dec | no |  |
| `volume_usd` | Dec | no |  |
| `source` | String | no |  |
| `fetched_at` | UtcDateTime | no |  |

### `configuration_versions`

Configuraciones (estrategia y límites) por hash de contenido.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `hash` | String | no | PK |
| `created_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `label` | String | sí |  |
| `config` | JSON | no |  |

### `contributions`

Aportaciones externas mensuales (una por periodo YYYY-MM).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `period` | String | no |  |
| `occurred_at` | UtcDateTime | no |  |
| `amount_eur` | Dec | no |  |
| `ledger_tx_id` | BigInteger | no | FK→ledger_transactions.id |

### `control_flags`

Kill switch, suspensión de compras, data_mode.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `key` | String | no | PK |
| `value` | String | no |  |
| `updated_at` | UtcDateTime | no |  |
| `updated_by` | String | no |  |
| `reason` | Text | sí |  |

### `data_quality_events`

Incidencias de calidad de datos.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `occurred_at` | UtcDateTime | no |  |
| `source` | String | no |  |
| `kind` | String | no |  |
| `severity` | String | no |  |
| `token_id` | BigInteger | sí | FK→tokens.id |
| `details` | JSON | no |  |

### `decisions`

Toda decisión con acción, motivos, scores, hash de entradas y de estrategia.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `decided_at` | UtcDateTime | no |  |
| `token_id` | BigInteger | sí | FK→tokens.id |
| `position_id` | BigInteger | sí | FK→positions.id |
| `action` | String | no |  |
| `bucket` | String | sí |  |
| `fraction` | Dec | sí |  |
| `reasons` | JSON | no |  |
| `scores` | JSON | no |  |
| `inputs_hash` | String | no |  |
| `strategy_hash` | String | no |  |
| `engine_version` | String | no |  |
| `mode` | String | no |  |
| `outcome` | String | no |  |

### `exchange_rates`

Tipos de referencia con fuente y hora (EUR/USD BCE, SOL/EUR CoinGecko).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `base` | String | no |  |
| `quote` | String | no |  |
| `rate` | Dec | no |  |
| `source` | String | no |  |
| `rate_time` | UtcDateTime | no |  |
| `fetched_at` | UtcDateTime | no |  |

### `executions`

Fills simulados con descomposición completa de costes.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `order_id` | BigInteger | no | FK→orders.id |
| `executed_at` | UtcDateTime | no |  |
| `method` | String | no |  |
| `token_qty` | Dec | no |  |
| `cash_eur` | Dec | no |  |
| `swap_amount_eur` | Dec | no |  |
| `price_eur_effective` | Dec | sí |  |
| `reference_price_eur` | Dec | sí |  |
| `price_impact_pct` | Dec | sí |  |
| `pool_fee_eur` | Dec | no |  |
| `aggregator_fee_eur` | Dec | no |  |
| `network_fee_eur` | Dec | no |  |
| `priority_fee_eur` | Dec | no |  |
| `deposit_eur` | Dec | no |  |
| `sol_eur` | Dec | no |  |
| `latency_ms` | Integer | sí |  |
| `capital_available_at` | UtcDateTime | sí |  |
| `details` | JSON | no |  |

### `explosion_events`

Episodios de explosión detectados (número de ola, precio base y pico).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `started_at` | UtcDateTime | no |  |
| `peak_at` | UtcDateTime | sí |  |
| `ended_at` | UtcDateTime | sí |  |
| `wave_number` | Integer | no |  |
| `base_price_usd` | Dec | no |  |
| `peak_price_usd` | Dec | sí |  |
| `peak_multiple` | Dec | sí |  |
| `peak_liquidity_usd` | Dec | sí |  |

### `features`

Vectores de indicadores point-in-time (persistencia limitada, ver retención).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `as_of` | UtcDateTime | no |  |
| `feature_set_version` | String | no |  |
| `values` | JSON | no |  |
| `missing` | JSON | no |  |

### `ledger_postings`

Apuntes de partida doble en EUR (debe +, haber −; cada transacción suma 0).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `transaction_id` | BigInteger | no | FK→ledger_transactions.id |
| `account` | String | no |  |
| `amount_eur` | Dec | no |  |
| `memo` | Text | sí |  |

### `ledger_transactions`

Cabecera de transacción contable (append-only, idempotente).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `occurred_at` | UtcDateTime | no |  |
| `recorded_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `description` | Text | no |  |
| `ref_type` | String | sí |  |
| `ref_id` | BigInteger | sí |  |
| `idempotency_key` | String | no |  |

### `liquidity_events`

Cambios relevantes de liquidez (pendiente).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `pool_id` | BigInteger | no | FK→pools.id |
| `detected_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `liquidity_before_usd` | Dec | sí |  |
| `liquidity_after_usd` | Dec | sí |  |
| `change_pct` | Dec | sí |  |

### `lot_disposals`

Asignación FIFO de cada venta a lotes: coste, importe y ganancia.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `lot_id` | BigInteger | no | FK→position_lots.id |
| `execution_id` | BigInteger | no | FK→executions.id |
| `disposed_at` | UtcDateTime | no |  |
| `qty` | Dec | no |  |
| `cost_basis_eur` | Dec | no |  |
| `proceeds_eur` | Dec | no |  |
| `gain_eur` | Dec | no |  |

### `market_snapshots`

Observaciones point-in-time por pool (`observed_at` = cuándo lo supo AEGIS).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `pool_id` | BigInteger | no | FK→pools.id |
| `source` | String | no |  |
| `observed_at` | UtcDateTime | no |  |
| `event_time` | UtcDateTime | sí |  |
| `price_usd` | Dec | sí |  |
| `price_eur` | Dec | sí |  |
| `eur_usd_rate_id` | BigInteger | sí | FK→exchange_rates.id |
| `liquidity_usd` | Dec | sí |  |
| `fdv_usd` | Dec | sí |  |
| `market_cap_usd` | Dec | sí |  |
| `volume_m5_usd` | Dec | sí |  |
| `volume_h1_usd` | Dec | sí |  |
| `volume_h6_usd` | Dec | sí |  |
| `volume_h24_usd` | Dec | sí |  |
| `price_change_m5` | Dec | sí |  |
| `price_change_h1` | Dec | sí |  |
| `price_change_h6` | Dec | sí |  |
| `price_change_h24` | Dec | sí |  |
| `buys_m5` | Integer | sí |  |
| `buys_h1` | Integer | sí |  |
| `buys_h6` | Integer | sí |  |
| `buys_h24` | Integer | sí |  |
| `sells_m5` | Integer | sí |  |
| `sells_h1` | Integer | sí |  |
| `sells_h6` | Integer | sí |  |
| `sells_h24` | Integer | sí |  |
| `buyers_m5` | Integer | sí |  |
| `buyers_h1` | Integer | sí |  |
| `buyers_h6` | Integer | sí |  |
| `buyers_h24` | Integer | sí |  |
| `sellers_m5` | Integer | sí |  |
| `sellers_h1` | Integer | sí |  |
| `sellers_h6` | Integer | sí |  |
| `sellers_h24` | Integer | sí |  |
| `quality_flags` | JSON | no |  |
| `raw` | JSON | sí |  |

### `model_versions`

Registro de modelos (heurísticos y entrenados) con estado champion/challenger.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `name` | String | no |  |
| `version` | String | no |  |
| `kind` | String | no |  |
| `status` | String | no |  |
| `is_calibrated_probability` | Boolean | no |  |
| `params` | JSON | no |  |
| `metrics` | JSON | no |  |
| `data_hash` | String | sí |  |
| `created_at` | UtcDateTime | no |  |
| `validated_at` | UtcDateTime | sí |  |

### `networks`

Redes soportadas (solo `solana` habilitada).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | String | no | PK |
| `name` | String | no |  |
| `native_symbol` | String | no |  |
| `enabled` | Boolean | no |  |

### `orders`

Órdenes simuladas con clave de idempotencia única y cotización usada.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `idempotency_key` | String | no |  |
| `decision_id` | BigInteger | sí | FK→decisions.id |
| `token_id` | BigInteger | no | FK→tokens.id |
| `side` | String | no |  |
| `mode` | String | no |  |
| `requested_at` | UtcDateTime | no |  |
| `notional_eur` | Dec | sí |  |
| `token_qty` | Dec | sí |  |
| `quote` | JSON | sí |  |
| `quote_expires_at` | UtcDateTime | sí |  |
| `status` | String | no |  |
| `reject_reason` | Text | sí |  |

### `pools`

Pools de cada token; `token_side` indica si el token es base o quote; `is_primary` = pool de referencia.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `network` | String | no | FK→networks.id |
| `address` | String | no |  |
| `token_id` | BigInteger | no | FK→tokens.id |
| `token_side` | String | no |  |
| `counter_asset` | String | sí |  |
| `dex` | String | sí |  |
| `pool_type` | String | no |  |
| `source_created_at` | UtcDateTime | sí |  |
| `first_seen_at` | UtcDateTime | no |  |
| `last_seen_at` | UtcDateTime | no |  |
| `is_primary` | Boolean | no |  |

### `portfolio_snapshots`

Patrimonio liquidable, compartimentos, KPI base y resultado de conciliación por ciclo.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `as_of` | UtcDateTime | no |  |
| `book` | String | no |  |
| `cash_investment_eur` | Dec | no |  |
| `cash_recycled_eur` | Dec | no |  |
| `cash_reserve_eur` | Dec | no |  |
| `deposits_eur` | Dec | no |  |
| `positions_cost_eur` | Dec | no |  |
| `positions_mark_eur` | Dec | no |  |
| `positions_liquidation_eur` | Dec | no |  |
| `open_positions` | Integer | no |  |
| `stale_positions` | Integer | no |  |
| `unvalued_positions` | Integer | no |  |
| `contributions_total_eur` | Dec | no |  |
| `withdrawals_total_eur` | Dec | no |  |
| `external_flow_since_last_eur` | Dec | no |  |
| `realized_pnl_total_eur` | Dec | no |  |
| `costs_total_eur` | Dec | no |  |
| `est_tax_realized_ytd_eur` | Dec | no |  |
| `net_worth_liquidable_eur` | Dec | no |  |
| `objective_eur` | Dec | no |  |
| `reconciliation_ok` | Boolean | no |  |
| `details` | JSON | no |  |

### `position_lots`

Lotes de adquisición (coste incluye gastos de adquisición).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `position_id` | BigInteger | no | FK→positions.id |
| `token_id` | BigInteger | no | FK→tokens.id |
| `execution_id` | BigInteger | no | FK→executions.id |
| `acquired_at` | UtcDateTime | no |  |
| `qty_initial` | Dec | no |  |
| `qty_remaining` | Dec | no |  |
| `cost_initial_eur` | Dec | no |  |
| `cost_remaining_eur` | Dec | no |  |
| `closed_at` | UtcDateTime | sí |  |

### `positions`

Posiciones (libro operativo); como máximo una abierta por token (índice parcial único).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `book` | String | no |  |
| `opened_at` | UtcDateTime | no |  |
| `closed_at` | UtcDateTime | sí |  |
| `status` | String | no |  |
| `bucket` | String | no |  |
| `entry_number` | Integer | no |  |
| `qty` | Dec | no |  |
| `cost_basis_eur` | Dec | no |  |
| `deposit_eur` | Dec | no |  |
| `deposit_lamports` | BigInteger | no |  |
| `invested_eur_total` | Dec | no |  |
| `realized_proceeds_eur` | Dec | no |  |
| `realized_pnl_eur` | Dec | no |  |
| `peak_price_eur` | Dec | sí |  |
| `peak_multiple` | Dec | sí |  |
| `capital_recovered` | Boolean | no |  |
| `capital_recovered_at` | UtcDateTime | sí |  |
| `partial_sells` | Integer | no |  |
| `last_mark_eur` | Dec | sí |  |
| `last_liquidation_eur` | Dec | sí |  |
| `last_valued_at` | UtcDateTime | sí |  |
| `valuation_method` | String | sí |  |

### `predictions`

Predicciones versionadas por modelo.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `model_version_id` | BigInteger | no | FK→model_versions.id |
| `as_of` | UtcDateTime | no |  |
| `target` | String | no |  |
| `horizon` | String | no |  |
| `value` | Dec | no |  |
| `uncertainty` | Dec | sí |  |
| `unit` | String | sí |  |
| `lower` | Dec | sí |  |
| `upper` | Dec | sí |  |
| `interval_level` | Dec | sí |  |
| `status` | String | sí |  |

### `recovery_events`

Recuperaciones tras desplomes y su clasificación.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `detected_at` | UtcDateTime | no |  |
| `drawdown_from_ath` | Dec | no |  |
| `rebound_multiple` | Dec | no |  |
| `classification` | String | sí |  |

### `risk_events`

Eventos de riesgo (kill switch, conciliación).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `occurred_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `severity` | String | no |  |
| `token_id` | BigInteger | sí | FK→tokens.id |
| `details` | JSON | no |  |
| `resolved_at` | UtcDateTime | sí |  |

### `scheduler_queue`

Cola de prioridad de monitorización adaptativa.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `token_id` | BigInteger | no | PK |
| `next_due_at` | UtcDateTime | no |  |
| `priority` | Integer | no |  |
| `interval_seconds` | Integer | no |  |
| `reason` | String | no |  |
| `last_observed_at` | UtcDateTime | sí |  |
| `last_lateness_seconds` | Integer | sí |  |
| `consecutive_misses` | Integer | no |  |

### `security_assessments`

Veredicto del motor de seguridad con hallazgos y caducidad.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `assessed_at` | UtcDateTime | no |  |
| `expires_at` | UtcDateTime | no |  |
| `verdict` | String | no |  |
| `engine_version` | String | no |  |
| `findings` | JSON | no |  |
| `inputs_hash` | String | no |  |

### `security_events`

Hallazgos críticos individuales (CONFIRMED/INDICATOR/UNKNOWN).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `occurred_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `status` | String | no |  |
| `details` | JSON | no |  |

### `shadow_positions`

Cartera sombra contrafactual (nunca forma parte del patrimonio).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `cohort` | String | no |  |
| `reason` | Text | no |  |
| `opened_at` | UtcDateTime | no |  |
| `entry_price_eur` | Dec | no |  |
| `notional_eur` | Dec | no |  |
| `est_entry_cost_eur` | Dec | no |  |
| `qty` | Dec | no |  |
| `peak_price_eur` | Dec | no |  |
| `peak_at` | UtcDateTime | no |  |
| `last_price_eur` | Dec | no |  |
| `last_liquidity_usd` | Dec | sí |  |
| `last_observed_at` | UtcDateTime | no |  |
| `closed_at` | UtcDateTime | sí |  |
| `outcome` | JSON | no |  |

### `source_health`

Salud por fuente de datos: llamadas, errores, 429, latencia.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `source` | String | no | PK |
| `last_ok_at` | UtcDateTime | sí |  |
| `last_error_at` | UtcDateTime | sí |  |
| `last_error` | Text | sí |  |
| `consecutive_errors` | Integer | no |  |
| `calls_total` | BigInteger | no |  |
| `errors_total` | BigInteger | no |  |
| `rate_limited_total` | BigInteger | no |  |
| `latency_ms_ewma` | Dec | sí |  |
| `updated_at` | UtcDateTime | no |  |

### `system_events`

Eventos de sistema (ciclos, fallos).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `occurred_at` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `severity` | String | no |  |
| `message` | Text | no |  |
| `details` | JSON | no |  |

### `token_profiles`

Holders, concentración, organic score, supply (Jupiter/GeckoTerminal).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `source` | String | no |  |
| `observed_at` | UtcDateTime | no |  |
| `holders_count` | Integer | sí |  |
| `top10_pct` | Dec | sí |  |
| `dev_holding_pct` | Dec | sí |  |
| `circulating_supply` | Dec | sí |  |
| `total_supply` | Dec | sí |  |
| `market_cap_usd` | Dec | sí |  |
| `organic_score` | Dec | sí |  |
| `mint_authority` | String | sí |  |
| `freeze_authority` | String | sí |  |
| `raw` | JSON | sí |  |

### `token_states`

Historial de transiciones de la máquina de estados con motivo y evidencia.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `token_id` | BigInteger | no | FK→tokens.id |
| `valid_from` | UtcDateTime | no |  |
| `state` | String | no |  |
| `previous_state` | String | sí |  |
| `reason` | Text | no |  |
| `evidence` | JSON | no |  |
| `engine_version` | String | no |  |

### `tokens`

Activo identificado por (network, address). Estado de mercado actual, ATH observado, contadores.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `network` | String | no | FK→networks.id |
| `address` | String | no |  |
| `symbol` | String | sí |  |
| `name` | String | sí |  |
| `decimals` | Integer | sí |  |
| `token_program` | String | sí |  |
| `launchpad` | String | sí |  |
| `chain_created_at` | UtcDateTime | sí |  |
| `first_seen_at` | UtcDateTime | no |  |
| `last_seen_at` | UtcDateTime | no |  |
| `discovery_source` | String | sí |  |
| `universes` | JSON | no |  |
| `market_state` | String | no |  |
| `market_state_since` | UtcDateTime | no |  |
| `ath_price_usd` | Dec | sí |  |
| `ath_at` | UtcDateTime | sí |  |
| `low_since_ath_usd` | Dec | sí |  |
| `explosion_count` | Integer | no |  |
| `entries` | Integer | no |  |
| `last_exit_at` | UtcDateTime | sí |  |
| `liquidity_at_exit_usd` | Dec | sí |  |
| `meta` | JSON | no |  |

### `trades`

Operaciones individuales (pendiente de ingesta continua).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `pool_id` | BigInteger | no | FK→pools.id |
| `tx_hash` | String | no |  |
| `source_trade_id` | String | no |  |
| `block_time` | UtcDateTime | no |  |
| `kind` | String | no |  |
| `wallet` | String | sí |  |
| `token_amount` | Dec | sí |  |
| `volume_usd` | Dec | sí |  |
| `price_usd` | Dec | sí |  |
| `fetched_at` | UtcDateTime | no |  |

### `wallet_relationships`

Relaciones INFERIDAS con nivel de confianza; nunca hechos confirmados.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `wallet_a` | BigInteger | no | FK→wallets.id |
| `wallet_b` | BigInteger | no | FK→wallets.id |
| `kind` | String | no |  |
| `confidence` | Dec | no |  |
| `evidence` | JSON | no |  |
| `inferred_at` | UtcDateTime | no |  |

### `wallets`

Direcciones observadas (grafo de relaciones; pendiente).

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `id` | BigInteger | no | PK |
| `network` | String | no |  |
| `address` | String | no |  |
| `labels` | JSON | no |  |
| `first_seen_at` | UtcDateTime | no |  |

### `worker_heartbeats`

Latido del worker y resumen del último ciclo.

| Columna | Tipo | Nulo | Clave |
|---|---|---|---|
| `worker_id` | String | no | PK |
| `started_at` | UtcDateTime | no |  |
| `last_beat_at` | UtcDateTime | no |  |
| `cycles` | BigInteger | no |  |
| `status` | String | no |  |
| `version` | String | no |  |
| `last_cycle` | JSON | no |  |
<!-- AUTO:END -->
