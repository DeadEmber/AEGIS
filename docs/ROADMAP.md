# Roadmap

| Fase | Entregable | Estado |
|---|---|---|
| 0 | Auditoría del MVP y viabilidad técnica | ✅ `AUDIT_MVP_0_1.md` |
| 1 | Refactorización, arquitectura y base de datos | ✅ paquete modular, 41 tablas, Alembic, SQLite + PG |
| 2 | Integración verificable de datos reales | ✅ 6 fuentes verificadas, fixtures reales. ⏳ streaming |
| 3 | Descubrimiento, seguridad y cartera simulada | ✅ |
| 4 | Biblioteca de indicadores | ✅ 46 indicadores. ⏳ evaluación incremental |
| 5 | Laboratorio histórico de explosiones | ⏳ **bloqueado por la fuente histórica** (ver BACKTEST_METHODOLOGY) |
| 6 | Modelos de detección de máximos | ⏳ (depende de 5 y del dataset de paper) |
| 7 | Modelos de segundas explosiones | ⏳ |
| 8 | Motor de decisiones y asignación de capital | ✅ baseline determinista |
| 9 | Backtesting integral | 🟡 motor de replay listo (mismo `Worker` + `ManualClock`); falta `HistoricalGateway` |
| 10 | Shadow trading y aprendizaje controlado | 🟡 cartera sombra y `SHADOW_TRADING`; falta entrenamiento |
| 11 | Dashboard profesional | 🟡 primera versión (Overview, Portfolio, Discovery, Peak Radar, Second Wave, Decisions, Capital, Risk, System) |
| 12 | Despliegue permanente en paper trading | ⏳ requiere aprobación del propietario (coste) |
| 13 | Validación económica y operativa | ⏳ |
| 14 | Preparación técnica de un piloto real (deshabilitado) | ⏳ no iniciado; requisitos en `SECURITY.md` |

## Siguiente bloque ejecutable (por prioridad)

1. **Despliegue permanente de paper trading** (fase 12). Elegir VPS/Fly y Supabase. Sin esto no se
   acumula el dataset point-in-time real, que es la única evidencia sin look-ahead disponible hoy.
2. **`HistoricalGateway` + fuente histórica on-chain** (fases 5 y 9). Decidir el proveedor
   (Helius / Dune / BigQuery / Flipside) y el presupuesto.
3. **Etiquetado de resultados** desde `shadow_positions` y `explosion_events` (triple barrera), para
   crear el primer dataset de entrenamiento (fase 6).
4. Comparativa de **tamaño de ticket** (0,5 / 1 / 2 / 5 EUR) y de tarifas de prioridad en replay.
4b. Experimento **E1 (ECV)**, en cuanto haya dataset y modelos (ver abajo).
5. **Peak Capture Ratio** y **Top Winner Dependency** en `/overview`.
6. Grafo de creadores y wallets: financiación común y holders compartidos (Security §16).
7. Ingesta de `trades` (GeckoTerminal `/trades`, 300 por pool) para flujos por wallet en las
   posiciones calientes.

## Experimentos de investigación

### E1 — ¿Mejora la eficiencia temporal del capital (ECV) los resultados?

- **Hipótesis.** Añadir el ECV como factor complementario en la selección (y más adelante en el
  dimensionamiento de salidas) mejora el resultado sin dañar la captura de grandes ganadores.
- **Variantes.** `aegis-baseline` frente a `aegis-ecv` (`research.compare.with_ecv`), con el mismo
  worker, universo, calendario de aportaciones, costes, semillas y reloj.
- **Métricas.**
  - Patrimonio neto liquidable.
  - Rentabilidad neta fuera de muestra (walk-forward).
  - Probabilidad de beneficio (bootstrap por bloques).
  - Número de oportunidades financiadas.
  - Capital Turnover y Capital Recycling Ratio.
  - Costes de negociación.
  - Captura de grandes ganadores (Peak Capture Ratio, Top Winner Dependency).
  - Riesgo de ventas prematuras (cohorte sombra `after_exit`: pico posterior frente a precio de salida).
- **Modelos necesarios** (los ya previstos en MODEL_REGISTRY, con objetivos ampliados; sin duplicados):
  - Gradient boosting (LightGBM): probabilidades de resultado x2/x5/x10 y de pérdida, más la magnitud
    esperada.
  - Regresión cuantílica (LightGBM con pérdida quantile): P10/P50/P90 del valor liquidable futuro y
    de la duración.
  - Supervivencia: tiempo hasta agotamiento, colapso o recuperación de capital, con las etiquetas
    censuradas de `research/temporal.py`.
- **Requisitos previos.** Dataset point-in-time suficiente (paper trading permanente) y/o la fuente
  histórica on-chain (fase 5).
- **Criterio de adopción.** El ECV solo se activa por defecto si mejora el objetivo neto fuera de
  muestra con un IC 90 % > 0, sin empeorar de forma significativa la captura de ganadores ni la tasa
  de ventas prematuras, en al menos 3 ventanas walk-forward. Mientras tanto queda desactivado.
- **Estado.** Harness y prueba de invariancia implementados (`test_ecv_variant_identical_while_ecv_unavailable`).
  A la espera de datos y modelos.
