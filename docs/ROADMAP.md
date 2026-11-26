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
5. **Peak Capture Ratio** y **Top Winner Dependency** en `/overview`.
6. Grafo de creadores y wallets: financiación común y holders compartidos (Security §16).
7. Ingesta de `trades` (GeckoTerminal `/trades`, 300 por pool) para flujos por wallet en las
   posiciones calientes.
