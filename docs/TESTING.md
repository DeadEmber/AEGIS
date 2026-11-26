# Pruebas

```bash
.venv/bin/pytest
```

```bash
AEGIS_TEST_POSTGRES_URL=postgresql+psycopg://user@host/db .venv/bin/pytest tests/integration/test_postgres.py
```

| Tipo | Ubicación |
|---|---|
| Unitarias | `tests/unit/` (contabilidad, ejecución, datos, inteligencia, decisión, documentación) |
| Basadas en propiedades (Hypothesis) | `test_accounting.py::test_property_reconciliation_holds`: secuencias aleatorias de compras y ventas nunca rompen la conciliación |
| Integración y extremo a extremo | `tests/integration/test_worker_cycle.py`: worker completo sobre el mercado simulado; `test_api.py`: API |
| Compatibilidad de base de datos | `tests/integration/test_postgres.py`: migración Alembic, decimales exactos e índice parcial en PostgreSQL |
| Datos reales | `tests/unit/test_marketdata.py` con respuestas **reales** grabadas (ver `PROVENANCE.md`) |
| Regresión del MVP | `tests/legacy/test_mvp_0_1_regression.py`: ejecuta la suite original sin cambios |
| Validación en vivo (manual) | `aegis probe`, `aegis once` (resultados en `docs/STATUS.md`) |

## Casos obligatorios (mandato §31)

✅ cubierto por una prueba automática · 🟡 parcial (se indica qué falta) · ⏳ pendiente

| # | Caso | Estado | Prueba(s) |
|---|---|---|---|
| 1 | Aportación mensual sin duplicación | ✅ | `test_monthly_contribution_idempotent`, `test_restart_without_state_loss` |
| 2 | Compra masiva respetando el presupuesto | ✅ | `test_budget_pacing_respects_month_and_reserves`, `test_entry_buckets_preserve_exploration_and_are_deterministic`, `test_full_lifecycle_on_simulated_market` |
| 3 | Compra rechazada por seguridad | ✅ | `test_dangerous_token2022_extensions_block`, `test_full_lifecycle_on_simulated_market` (freeze) |
| 4 | Venta parcial | ✅ | `test_partial_then_full_sale_fifo`, `test_partial_then_full_sell_with_quote` |
| 5 | Venta completa | ✅ | ídem, con recuperación del depósito |
| 6 | Reinversión de beneficios | ✅ | `test_recycled_capital_funds_new_buys_no_double_count` |
| 7 | Reentrada en antigua ganadora | ✅ | `test_reentry_into_former_winner` (nivel de decisión) |
| 8 | Token imposible de vender | ✅ | `test_no_sell_route_blocks_unsellable_token`, lifecycle (honeypot → UNTRADABLE) |
| 9 | Desaparición de liquidez | ✅ | `test_liquidity_disappearance_makes_sale_unviable_then_burn_close` |
| 10 | Error de API | ✅ | `test_http_retries_429_then_succeeds_and_caches`, `test_http_network_failure_raises_after_retries`, `test_api_outage_and_stale_data_suspend_buys` |
| 11 | Datos obsoletos | ✅ | `test_stale_market_data_blocks_order`, `test_api_outage_and_stale_data_suspend_buys` |
| 12 | Interrupción del worker | ✅ | `test_restart_without_state_loss` (se descarta el engine a mitad de la ejecución) |
| 13 | Reinicio sin pérdida de estado | ✅ | ídem |
| 14 | Prevención de órdenes duplicadas | ✅ | `test_duplicate_order_key_does_not_double_fill`, `test_ledger_idempotency_key_prevents_double_posting` |
| 15 | Conciliación de patrimonio | ✅ | `test_property_reconciliation_holds`; conciliación verificada en cada ciclo de integración |
| 16 | Valoración de posiciones ilíquidas | ✅ | `test_state_uses_liquidation_value_and_counts_unvalued` |
| 17 | Ausencia de look-ahead | ✅ | `test_no_lookahead_allowed` |
| 18 | Separación de carteras virtuales y operativas | ✅ | `test_shadow_mode_never_books` (unitaria e integración), lifecycle (las filas sombra no generan asientos) |
| 19 | Reproducción de decisiones históricas | ✅ | `test_decisions_are_reproducible` (dos ejecuciones independientes, mismos hashes) |
| 20 | Imposibilidad de órdenes reales en simulación | ✅ | `test_live_modes_cannot_execute`, `test_live_disabled_mode_refuses_to_start` |
| 21 | Costes superiores a una microposición | ✅ | `test_costs_above_microposition_rejected`, `test_ticket_smaller_than_fixed_costs_rejected` |
| 22 | Cotizaciones caducadas | ✅ | `test_expired_quote_rejected` |
| 23 | Datos históricos incompletos | 🟡 | `test_features_from_real_snapshot_and_explicit_missing` (ausencias explícitas). Falta: backtest con huecos (no hay `HistoricalGateway`) |
| 24 | Venta durante una explosión rápida | 🟡 | `test_fast_explosion_sale_decision_is_immediate` (decisión y prioridad). Falta: simulación de latencia con precio en movimiento entre cotización y fill |
| 25 | Detección de una segunda ola | ✅ | `test_second_wave_detected` |
| 26 | Comparación entre estrategias | ✅ | `test_strategy_comparison_same_conditions` (mismo worker, mercado y costes; **simulado**) |
| 27 | Bloqueo de modelos no validados | 🟡 | Por construcción no existe ningún camino que cargue modelos (`MODEL_REGISTRY.md`). Falta la prueba cuando exista el cargador |
| 28 | Protección de reservas fiscales | ✅ | `test_tax_reserve_not_spendable`, `test_tax_reserve_released_after_later_losses` |
| 29 | Verificación del cálculo de rentabilidad | ✅ | `test_performance_tax.py` (TWR, XIRR, MDD, NPR, DCA, IRPF) |
| 30 | Recuperación tras una caída del sistema | ✅ | `test_restart_without_state_loss`, `test_api_outage_and_stale_data_suspend_buys` |

## Ampliación de eficiencia temporal del capital (2026-11-26)

| Funcionalidad | Prueba(s) |
|---|---|
| Decisión → ejecución → disponibilidad del capital | `test_decision_execution_and_capital_availability_are_recorded` |
| Cobros no reutilizables antes de la liquidación | `test_recycled_proceeds_not_spendable_before_settlement` |
| Límite de impacto en salidas (no críticas frente a críticas) | `test_exit_impact_limit_rejects_non_critical_but_allows_critical`, `test_decision_engine_caps_non_critical_exits_to_liquidity` |
| Métricas de eficiencia (turnover, AHP, reciclaje, recuperación, financiación reciclada) | `test_capital_efficiency_metrics` |
| Recuperación parcial frente a total | `test_partial_recovery_sets_recovery_time_only_when_crossed` |
| Indicadores temporales point-in-time y look-ahead | `test_temporal_features_point_in_time` |
| Kaplan–Meier con censura | `test_kaplan_meier_with_censoring` |
| Etiquetas temporales con censura | `test_temporal_labels_censor_unfinished_episodes` |
| Sin estimación temporal sin datos suficientes | `test_no_temporal_estimate_without_enough_events` |
| ECV: fórmula, no disponible y base liquidable | `test_ecv_formula_and_unavailability`, `test_position_ecv_uses_liquidable_value_not_cost` |
| ECV complementario e inerte si no está disponible | `test_ecv_is_complementary_and_inert_when_unavailable`, `test_ecv_variant_identical_while_ecv_unavailable` |
| Base empírica exige datos | `test_empirical_baseline_requires_enough_closed_positions` |
| Episodios de explosión y tiempos de capital en el worker | `test_explosion_episodes_tracked_and_capital_timing_recorded` |
| Migraciones sin deriva respecto al esquema | `test_migrations_match_metadata` |

## Auditoría independiente (0.2.2)

| Hallazgo | Prueba(s) |
|---|---|
| Starvation de posiciones no observadas | `test_unobserved_position_stays_due_escalates_and_counts_misses` |
| Fuente de respaldo para posiciones | `test_fallback_source_observes_position_missing_from_primary` |
| Frecuencia configurada frente a efectiva | `test_effective_frequency_fast_lane_vs_full_cycle_only` (sin vía rápida: 300 s; con vía rápida: ≤ 180 s) |
| Prioridad bajo límite de refrescos | `test_due_order_preserves_position_priority_under_refresh_limit` |
| Retraso medido contra el momento programado | `test_lateness_is_measured_against_scheduled_time` |
| Despertar del worker | `test_next_wake_respects_floor_and_full_cycle` |
| Shadow sin precios obsoletos | `test_stale_price_never_used_as_outcome`, `test_observation_outside_tolerance_window_is_not_used`, `test_legacy_rows_are_not_evaluated_with_stale_prices` |
| Shadow multihorizonte | `test_fresh_observation_in_window_is_recorded_gross_and_liquidable`, `test_shadow_closes_only_when_every_horizon_resolved`, `test_summary_distinguishes_observed_unknown_censored`, `test_checkpoint_tokens_when_window_open`, `test_daily_caps_per_cohort`, `test_shadow_horizons_and_checkpoints_in_worker` |
| Archivo antes de borrar | `test_prune_deletes_nothing_without_verified_archive`, `test_archive_is_verified_complete_and_queryable`, `test_prune_after_archive_keeps_research_data_in_archive`, `test_tampered_or_missing_archive_blocks_deletion`, `test_late_row_inside_archived_range_is_not_pruned`, `test_episode_windows_keep_full_resolution` |
| E2 y ECV | `test_monetisation_grid_runs_under_identical_conditions`, `test_rmst_is_an_expectation_not_the_median` |

## Segunda auditoría (0.2.3)

| Hallazgo | Prueba(s) |
|---|---|
| Archivado: ids menores nunca omitidos | `test_incremental_batches_never_skip_lower_ids` |
| Archivado: confirmación tardía protegida y reconciliada | `test_late_row_inside_archived_range_is_protected_then_reconciled` |
| Tablas financieras archivadas, nunca podadas, el libro archivado suma 0 | `test_financial_tables_are_archived_and_never_pruned` |
| Backup, verificación y restauración sin base de datos | `test_backup_verify_restore_without_database`, `test_manifest_reimport_after_database_loss` |
| Mantenimiento a prueba de fallos | `test_maintenance_is_fail_safe` |
| Replay igual al worker operativo | `test_replay_uses_exactly_the_operational_schedule` |
| Resolución de datos y representatividad | `test_replay_respects_data_resolution_and_flags_fast_dynamics`, `test_fixed_cadence_is_flagged_not_equivalent` |
| Contrafactual de ventas parciales | `test_partial_sales_are_tracked_as_units_sold_counterfactuals`, `test_units_sold_shadow_measures_forgone_upside` |
| Reentrada con residual (E3) | `test_residual_reentry_signal_recorded_not_bought` |

## Fase 2: base empírica, liquidez, inteligencia y validación

Las pruebas usan almacenes **sintéticos** (`tests/history_synth.py`): la licencia de Launch Tape prohíbe
subir sus datos, y el corpus es grande. Los adaptadores se prueban con ficheros Parquet sintéticos que
tienen el esquema real.

| Requisito | Prueba(s) |
|---|---|
| CPMM exacto, reserva virtual, venta acotada por la reserva real | `test_cpmm_swap_formula_fee_and_virtual_reserve`, `test_sell_never_extracts_more_than_the_real_reserve` |
| Sin ejecución CPMM para DLMM/CLMM/DAMM/curva/desconocido | `test_no_cpmm_fill_for_other_structures`, `test_dex_classification_never_assumes_cpmm_for_ambiguous_dexes`, `test_unsupported_market_is_never_filled` |
| Comisión medida por tramos; capitalización desconocida → tramo más caro | `test_measured_fee_schedule_tiers_and_unknown_cap_takes_worst_tier` |
| Point-in-time: vista sin futuro, estáticos con `known_at`, sin acceso a resultados | `test_market_view_refuses_future_observations_and_hides_unknown_static`, `test_entry_features_are_point_in_time` |
| Desconocido ≠ cero ≠ aprobado | `test_pool_observation_mapping_keeps_unknowns_as_none`, `test_entry_filters_treat_unknown_as_failure` |
| Tipo BCE conocido solo tras su publicación | `test_ecb_rate_is_only_known_after_publication`, `test_bundled_ecb_table_covers_the_research_periods` |
| Revisiones y hashes fijados; descarga verificada; fichero alterado rechazado | `test_registry_pins_revisions_and_hashes`, `test_verify_detects_missing_and_tampered_files`, `test_fetch_rejects_a_changed_upstream_file` |
| Licencia: ningún fichero de Launch Tape en el repositorio | `test_no_non_redistributable_dataset_file_is_committed` |
| Adaptador del corpus: centinelas, curva no negociable, concentración sospechosa → desconocida, columnas retrospectivas excluidas, huecos | `test_pumpfun_adapter_separates_markets_outcomes_and_flags` |
| Adaptador de Launch Tape: ventanas solo con trades ya impresos, reserva virtual arrastrada, recuentos de 24 h exactos, huecos del feed, censura | `test_launchtape_adapter_point_in_time_windows_and_exact_24h_counts` |
| Simulador: todo por el libro, conciliado, determinista, censura informada | `test_backtest_books_everything_through_the_ledger_and_reconciles`, `test_backtest_is_deterministic`, `test_hold_exit_never_sells_and_reports_censoring` |
| Reciclaje `none` frente a `full` tras la liquidación diferida | `test_recycling_none_leaves_proceeds_idle_full_recycles_after_settlement` |
| **Defecto corregido**: el reciclado pagaba compras por encima de su tope | `test_buy_draws_only_the_budgeted_allowance_per_cash_account` |
| **Defecto corregido**: tope de presupuesto con más de 12 decimales → asiento no cuantizado | `test_allowance_with_excess_precision_is_rounded_down_to_the_ledger_scale` |
| Contexto bruto y normalizado por cobertura | `test_context_filters_raw_and_coverage_normalised` |
| Worker de producción sobre histórico: seguridad desconocida bloquea, evidencia limitada, sin mezcla de datos, gateway point-in-time | `tests/integration/test_historical_replay.py` |
| Modelos: señal, preprocesado solo con entrenamiento, AUC con empates, isotónica monótona, fiabilidad | `tests/unit/test_learning.py` |
| Walk-forward ordenado, purga y embargo; bootstrap por bloques; Holm | `test_walk_forward_folds_are_ordered_and_purged`, `test_block_bootstrap_and_holm` |
| Etiquetas con costes y censura | `test_samples_labels_use_costs_and_mark_censoring` |
| El agente se abstiene y delega en el Champion sin modelo o con incertidumbre | `test_agent_abstains_and_delegates_without_a_model_or_when_uncertain`, `test_model_entry_abstains_outside_trained_periods` |
| Carteras aleatorias deterministas; segunda oportunidad solo tras salida | `test_random_entry_is_deterministic_and_second_chance_needs_an_exit` |

Los experimentos con los datos reales (`aegis research phase2`) no se ejecutan en CI, porque los datos
no están en el repositorio. Sus resultados (solo cifras derivadas) quedan versionados en
`docs/research/`.

## Pendientes de prueba

- Pruebas de rendimiento: ciclos con más de 5.000 tokens y tiempo por fase.
- Pruebas de seguridad automatizadas: escaneo de secretos y `pip-audit` en CI.
- Pruebas de regresión de modelos: se añadirán con el primer modelo entrenado.
- E2E del dashboard: Playwright contra la API con la base de datos de demo.
