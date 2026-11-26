# Estado del proyecto (registro persistente entre sesiones)

Última actualización: 2026-11-26 · rama `claude/aegis-foundation` · versión 0.2.3

## Resumen

El *vertical slice* completo funciona con datos reales: descubrimiento → seguridad → indicadores →
decisión → ejecución simulada con cotizaciones de Jupiter y costes de Solana → contabilidad de partida
doble → conciliación → API → dashboard. **La rentabilidad de la estrategia no está demostrada** y no
puede demostrarse todavía (ver `BACKTEST_METHODOLOGY.md`).

## Validación en vivo realizada (2026-11-26, entorno de desarrollo)

| Prueba | Resultado |
|---|---|
| `aegis probe` | 6/6 fuentes OK (GeckoTerminal 358 ms, DexScreener 313 ms, Jupiter 324 ms, RPC 385 ms, BCE 352 ms, CoinGecko 435 ms) |
| `aegis once` (ciclo 1) | 60 observaciones, 58 tokens nuevos, 4 chequeos de seguridad, 4 compras simuladas con cotización de Jupiter, 0 errores, conciliación OK, 28 s |
| `aegis once` (ciclo 2) | 76 observaciones, 4 posiciones valoradas (liquidable 3,51 EUR + depósitos 0,39 EUR frente a 4,00 EUR invertidos), rechazos explicados (FDV 32, precio 13, antigüedad 3), conciliación OK |
| PostgreSQL 16 local | migración Alembic + 24 ciclos simulados OK, decimales exactos, índice parcial único |
| Dashboard | `next build` OK (14 rutas estáticas), revisado con capturas en escritorio y móvil (390 px, sin scroll horizontal) en temas claro y oscuro, sin errores de consola contra la API sobre una base de demo |

Hallazgos con datos reales:

- `market_cap_usd` es `null` en GeckoTerminal para la mayoría de tokens nuevos. El filtro FDV explícito
  es el que más rechaza.
- Impacto de hasta el 2,4 % para 0,9 EUR en pools que anuncian más de 10.000 EUR de liquidez.
- Volumen orgánico del 0,07 % según Jupiter en un token activo.
- Pump.fun crea ahora mints **Token-2022**, cuya cuenta de token es mayor y tiene una renta más alta.
- La renta real de una cuenta de 165 B es de 1.488.440 lamports, no los 2.039.280 históricos.

## Errores encontrados y corregidos durante el desarrollo

1. Residuos de 1e-39 en la suma cero. Lo detectó Hypothesis y se resolvió con el libro a escala 1e-12.
2. Bucle de órdenes de polvo sobre posiciones residuales (36 órdenes rechazadas en la simulación).
3. Sin salida en posiciones en deterioro estructural: −93 % sin actuar.
4. Recuperación de capital bloqueada a x40 por el umbral de fracción mínima; ahora es por valor.
5. "Comprar y mantener" no era puro en la comparación (vendía por colapso).
6. Los motivos de rechazo ocultaban el filtro real tras "security:UNKNOWN".
7. Los features ocupaban ~55 MB/día y superaban el plan gratuito de Supabase; ahora hay persistencia
   condicional y retención.
8. Interpretación de `priceImpactPct` de Jupiter: es una fracción, contrastado con el endpoint Ultra.

## Ampliación 0.2.1: eficiencia temporal del capital

- Implementado: modelo de datos (migración 0002, probada sin deriva y aplicada sobre una base con datos
  existentes), métricas de eficiencia, tiempos de liquidación, límites de impacto en salidas,
  indicadores y etiquetas temporales con censura, base Kaplan–Meier condicionada y ECV experimental.
- **ECV no disponible** en la práctica: no hay modelo validado ni 30 posiciones cerradas. Está
  desactivado en las decisiones. Con ECV no disponible, la variante `aegis-ecv` da resultados
  idénticos a la base, verificado por una prueba.
- Las posiciones creadas antes de la migración 0002 no tienen `capital_recovered_at` ni
  `capital_available_at` (datos históricos sin ese dato, no se reconstruyen).

## Auditoría independiente sobre el commit 45240ba (correcciones en 0.2.2)

Cada hallazgo se reprodujo **antes** de corregirlo (script de reproducción contra 45240ba). Resultado:

| Hallazgo | ¿Reproducido? | Evidencia | Corrección | Pruebas |
|---|---|---|---|---|
| P1 Retención destruye histórico | **Sí** | `prune` borró 128 snapshots, 117 features y 117 decisiones sin copia | Archivo Parquet (zstd) con manifiesto y SHA-256 reverificado; `prune` solo borra filas presentes en un lote verificado; ventanas de episodio ±48 h a resolución completa | `tests/integration/test_archive.py` (6) |
| P1 Frecuencia efectiva / `position_unobserved` | **Sí, más grave** | La posición no observada se reprogramaba +600 s en cada ciclo y **nunca volvía a estar vencida** (starvation permanente), además perdía prioridad (→2). Con ciclo de 300 s, el hueco efectivo era 300 s frente a los 180 s configurados | Los fallidos siguen vencidos, cuentan fallos y escalan a prioridad 0; próximo refresco anclado en la observación real; vía rápida (mín. 60 s) para prioridades ≤ 3; respaldo DexScreener; retraso y antigüedad registrados (`/system`) | `tests/integration/test_scheduling.py` (6) |
| P1 Shadow 14 días | **Sí** | Cerró en el día 15 con multiplicador x2 tomado de un precio del día 1 | Horizontes 24 h/7/30/90/180/365 d; `observed`/`unknown`/`censored`; bruto frente a liquidable; nunca precios obsoletos; límites diarios; cohortes `reentry_discarded` y `after_exit` | `tests/unit/test_shadow.py` (8) + integración |
| P2 Monetización y grandes ganadores | n/a (investigación) | — | Experimento E2 en el ROADMAP y variantes en `research.compare`; la política por defecto no cambia | `test_monetisation_grid_runs_under_identical_conditions` |
| P2 ECV: mediana frente a esperanza | **Sí** (documental) | La base empírica usaba la mediana KM como "duración esperada" | Documentado en código y especificación; RMST(τ) = E[min(T, τ)] disponible como alternativa coherente con la censura; ECV sigue desactivado | `test_rmst_is_an_expectation_not_the_median` |

Hallazgo adicional durante la verificación de E2: en la trayectoria simulada, **no** recuperar el capital
a x2 dejó que el trailing vendiera más tarde y más arriba (4,61 EUR frente a 3,37 EUR de reciclado).
No se cambia la política: es justo la pregunta que E2 debe responder con datos reales.

## Segunda auditoría sobre 9cfa615 (correcciones en 0.2.3)

| Hallazgo | ¿Reproducido? | Evidencia | Corrección | Pruebas |
|---|---|---|---|---|
| P1 Archivado incremental por `max_id` | **Sí (2 vías)** | (a) id 1 con timestamp más reciente que id 2: se archivó id 2 y el 1 quedó fuera para siempre; (b) id 11 confirmado tarde dentro de un rango ya archivado [10,12]: nunca se archivaba y quedaba protegido contra el borrado indefinidamente | Lotes = prefijo **contiguo** de ids elegibles; `reconcile` archiva todo id no cubierto (con evento de calidad de datos); el borrado usa **cobertura por conjunto de ids** verificada | `test_incremental_batches_never_skip_lower_ids`, `test_late_row_inside_archived_range_is_protected_then_reconciled` |
| P1 Backup y restauración | n/a (diseño) | No existía | `manifest.json` autosuficiente, `archive --verify/--backup/--restore/--import-manifest`, `aegis maintenance` a prueba de fallos, unidades systemd y servicio compose; tablas financieras de solo inserción archivadas | `test_backup_verify_restore_without_database`, `test_manifest_reimport_after_database_loss`, `test_maintenance_is_fail_safe`, `test_financial_tables_are_archived_and_never_pruned` |
| P1 Equivalencia temporal del backtest | **Sí** | El comparador usaba pasos fijos de 30 min y 0 ciclos rápidos | `Worker.step/plan_wake/replay` compartidos con `run_forever`; cuantización a la resolución de los datos; banderas de representatividad en cada resultado | `test_replay_uses_exactly_the_operational_schedule` (40 ciclos idénticos), `test_replay_respects_data_resolution_and_flags_fast_dynamics`, `test_fixed_cadence_is_flagged_not_equivalent` |
| P2 Ventas parciales | **Sí** | `after_exit` solo al cierre total | Cada venta abre un contrafactual de las **unidades vendidas** (cantidad, importe neto y bruto, costes, ganancia realizada); métrica de beneficio sacrificado frente a reinversión | `test_partial_sales_are_tracked_as_units_sold_counterfactuals`, `test_units_sold_shadow_measures_forgone_upside` |
| P2 Reentrada con residual | **Sí** | Los tokens en cartera se excluyen de entradas | Hipótesis E3: se registra WATCH `reentry_blocked_by_open_residual` y la cohorte sombra correspondiente; **sin cambio de política ni más exposición** | `test_residual_reentry_signal_recorded_not_bought` |

## Limitaciones vigentes

- Sin universo histórico representativo ni backtest 2024–hoy (sesgo de supervivencia de las APIs
  gratuitas).
- Scores 100 % heurísticos y sin calibrar. No hay modelos entrenados.
- Datos por polling con límites gratuitos (~9 llamadas/min en GeckoTerminal). Sin streaming.
- Sin grafo de wallets ni ingesta de trades individuales.
- El worker no está desplegado de forma permanente (requiere aprobación y coste).
- La fiscalidad es una estimación: no incluye la permuta del lado SOL ni la regla de los 2 meses.
- La vía rápida está limitada por la cuota gratuita de GeckoTerminal (~9 llamadas/min): hasta 90 tokens
  por ciclo rápido (3 llamadas). Con cientos de posiciones calientes simultáneas los 60–180 s no se
  garantizan; el retraso queda medido en `/system`.
- El archivo y su backup viven en discos del servidor (`AEGIS_ARCHIVE_DIR`, `AEGIS_BACKUP_DIR`). La
  tercera copia fuera del servidor es decisión del propietario: AEGIS no contrata servicios.
- La cobertura del archivo se calcula leyendo la columna `id` de los Parquet en memoria. Es adecuado
  para millones de filas; para volúmenes mayores convendrá un anti-join en DuckDB.
- El replay histórico real necesita un `HistoricalGateway` (fuente on-chain pendiente). La lógica de
  cadencia y resolución ya está integrada y probada con datos simulados en rejilla.
- Las filas de la cartera sombra anteriores a 0.2.2 se marcan `legacy` y se reevalúan con la nueva
  lógica. Su cierre a 14 días con precios obsoletos no se reconstruye.
- El valor liquidable de posiciones usa una estimación CPMM (no se pide cotización de venta para cada
  posición en cada ciclo, para no agotar el rate limit). Para CLMM/DLMM es una aproximación.

## Decisiones que requieren al propietario

1. **Despliegue permanente** (VPS/Fly + Supabase): coste estimado de ~5 EUR/mes sin verificar, el 5 %
   de las aportaciones.
2. **Fuente histórica on-chain** para el backtest 2024–hoy (Helius / Dune / BigQuery / Flipside): coste
   por determinar.
3. ¿Mantener el ticket de 1 EUR? Con tarifas de prioridad altas no es viable (`COST_ANALYSIS.md`).

## Siguiente bloque ejecutable

Ver `ROADMAP.md` → "Siguiente bloque ejecutable".
