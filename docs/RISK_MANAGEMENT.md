# Gestión de riesgos

## Controles no negociables (implementados)

| Control | Implementación | Prueba |
|---|---|---|
| Sin apalancamiento ni deuda | `RiskLimits.leverage_allowed` debe ser False. El `Ledger` prohíbe saldos de caja negativos | `test_ledger_rejects_unbalanced_and_overdraft` |
| Aportación externa limitada | `max_monthly_external_contribution_eur` = 100; `StrategyConfig.check_against` | arranque del worker |
| Sin exposición a tokens con filtros críticos fallidos | `SecurityEngine` → BLOCK; el motor de decisiones rechaza | `test_dangerous_token2022_extensions_block`, `test_no_sell_route_blocks_unsellable_token` |
| Lo desconocido no es seguro | `unknown_critical_blocks=True` | `test_unknown_is_not_safe` |
| Sin órdenes duplicadas | `orders.idempotency_key` único; ledger idempotente | `test_duplicate_order_key_does_not_double_fill` |
| Suspensión por datos obsoletos | FX > 96 h o SOL/EUR > 30 min → sin compras (sin SOL/EUR, tampoco ventas); datos de token > 30 min → orden rechazada | `test_api_outage_and_stale_data_suspend_buys`, `test_stale_market_data_blocks_order` |
| Suspensión por fallos de fuente | ≥ 5 errores consecutivos → sin compras | `RiskControls.evaluate` |
| Suspensión por error de conciliación | dispara el kill switch persistente y un `risk_event` crítico | `Worker.run_cycle` |
| Kill switch | `control_flags.kill_switch=on` (CLI o API con token) → ninguna orden | `test_kill_switch_blocks_all_orders` |
| Límites operativos | máx. compras por ciclo, máx. posiciones abiertas, ticket máximo | `RiskLimits` |
| La IA no ejecuta ni modifica los controles | `RiskLimits` inmutable; decisiones deterministas | — |
| Sin ejecución real | `LIVE_DISABLED` falla de forma explícita | `test_live_modes_cannot_execute` |

## Riesgos de la estrategia (no eliminables, se miden)

- **Dependencia de pocos ganadores.** La hipótesis asimétrica implica que el resultado depende de la
  cola. Hay que medir la Top Winner Dependency antes de concluir nada.
- **Correlación oculta.** Miles de memecoins no son apuestas independientes: comparten régimen de
  mercado (SOL), creadores en serie (`creator_history`) y bots. El grafo de wallets está pendiente.
- **Liquidez ilusoria.** Se ha observado un impacto del 2,4 % en pools que anunciaban más de 10.000
  EUR de liquidez. Por eso se usan cotizaciones ejecutables y no la liquidez publicada.
- **Volumen artificial.** Jupiter clasifica como orgánico solo el ~0,07 % del volumen de compra de un
  token activo real. Los volúmenes publicados no miden demanda real.
- **Riesgo de modelo.** Los scores son heurísticos sin calibrar.
- **Riesgo operativo.** Interrupciones y rate limits. El worker no fabrica datos ni operaciones.

## Fiscalidad (España) — estimación, no liquidación

- Ganancias y pérdidas patrimoniales en la base del ahorro, con FIFO por token (`lot_disposals`).
- El valor de adquisición incluye los gastos de compra. El valor de transmisión descuenta los gastos
  de venta.
- Escala usada (2025+, Ley 7/2024): 19 % hasta 6.000 EUR, 21 % hasta 50.000 EUR, 23 % hasta 200.000
  EUR, 27 % hasta 300.000 EUR y 30 % por encima. **Debe verificarse cada año.**
- Las pérdidas netas se compensan hasta 4 años. Se ignora la compensación del 25 % con la base general
  (criterio conservador).
- **Limitaciones**: no se modela la permuta del lado SOL (en real, cada swap SOL↔token es una permuta);
  no se aplica la regla de los 2 meses (art. 33.5); se asume que AEGIS es la única renta del ahorro.
  Para revisión profesional existen `aegis export` y las tablas `lot_disposals` y `executions`.
