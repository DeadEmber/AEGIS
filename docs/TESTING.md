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

## Pendientes de prueba

- Pruebas de rendimiento: ciclos con más de 5.000 tokens y tiempo por fase.
- Pruebas de seguridad automatizadas: escaneo de secretos y `pip-audit` en CI.
- Pruebas de regresión de modelos: se añadirán con el primer modelo entrenado.
- E2E del dashboard: Playwright contra la API con la base de datos de demo.
