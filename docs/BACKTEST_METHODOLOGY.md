# Metodología de backtesting e investigación

## Estado honesto

**No existe todavía un backtest de mercado real desde el 1 de enero de 2024.** Lo que existe:

1. **Replay determinista** del worker completo sobre un mercado simulado (`research/simmarket.py`).
   Valida la mecánica: que no hay look-ahead, que la contabilidad concilia, que los costes se pagan,
   que los estados cambian y que las decisiones son reproducibles. **No dice nada sobre rentabilidad.**
2. **Paper trading con datos reales** (`aegis run`), que acumula un dataset point-in-time sin sesgo de
   look-ahead por construcción (`observed_at`).
3. **Cartera sombra multihorizonte** (24 h, 7, 30, 90, 180 y 365 días): resultados brutos y liquidables
   con estado observado, desconocido o censurado. Nunca usa precios obsoletos. Cubre candidatos
   rechazados, no seleccionados, reentradas descartadas y posiciones tras la salida.
4. **Archivo de investigación** (`aegis archive`, Parquet + DuckDB): todas las observaciones,
   indicadores y decisiones point-in-time, con procedencia, a resolución completa. Es el dataset sobre el
   que se entrenarán y validarán los modelos (walk-forward).

## Por qué no se ha hecho aún el backtest 2024–hoy

| Requisito | Bloqueo verificado |
|---|---|
| Universo representativo, incluidas las monedas desaparecidas | GeckoTerminal y DexScreener solo devuelven pools **indexados actualmente**. Los tokens muertos y deslistados no se pueden enumerar con las APIs gratuitas → **sesgo de supervivencia** |
| OHLCV intradía desde 2024 | GeckoTerminal OHLCV por pool existente, ≤ 1.000 velas por llamada y ~9 llamadas/min: ~10.000 tokens × años de velas de 5 min es inviable en el plan gratuito |
| Liquidez y compradores históricos | No disponibles en OHLCV; hacen falta datos on-chain (Helius / BigQuery / Dune / Flipside) |
| Cotizaciones ejecutables históricas | No existen; hay que modelar la ejecución con reservas reconstruidas |

**Ruta propuesta** (requiere decisión y posible coste del propietario):

- (a) Reconstruir el universo a partir de eventos de creación de pools on-chain (Raydium, PumpSwap,
  Meteora) con un proveedor de históricos.
- (b) Reconstruir reservas y swaps por bloque.
- (c) Simular con la misma clase `Worker` mediante un `HistoricalGateway` y `ManualClock`.

## Reglas obligatorias para cualquier backtest

- **Point-in-time.** El gateway histórico solo devuelve datos con `observed_at ≤ t`. La función
  `features.compute` verifica que no hay look-ahead.
- **Universo construido con información de t** (pools creados hasta t), incluidos los que después mueren.
- **Ejecución conservadora.** Si una vela no permite saber si se tocó primero el máximo o el mínimo,
  se asume el peor orden para la estrategia (venta al mínimo y compra al máximo de la vela) o se
  declara la ambigüedad. Nunca se vende al máximo de una vela.
- **Costes completos.** Se aplican los mismos `CostModel` y `SimulatedExecutionEngine` que en paper,
  con escenarios de tarifa de prioridad ×1, ×10 y ×100.
- **Validación temporal.** Walk-forward con ventanas de entrenamiento y prueba disjuntas y un hueco
  (embargo) ≥ al horizonte de la etiqueta; bootstrap por bloques para los intervalos de confianza.
- **Comparación justa.** Todas las estrategias reciben el mismo calendario de aportaciones, los mismos
  costes y el mismo universo: compra aleatoria y mantenimiento, compra filtrada y mantenimiento, compra
  masiva con reciclaje, take-profit escalonado, stop-loss (`exits.stop_loss_multiple`), trailing, Peak
  Engine, Second Wave, AEGIS integral, BTC DCA y SOL DCA (`accounting.performance.dca_benchmark`) y efectivo.
- **Escenarios adversos.** Comisiones ×10, liquidez −50 %, retraso de 1, 5 y 30 min, caída de
  mercado, eliminación de los k mejores ganadores (Top Winner Dependency) y errores de datos.
- **Sobreoptimización.** Se fija el número de configuraciones probadas y se reporta el mejor resultado
  junto con la distribución de todos, no solo el mejor.

## Laboratorio de explosiones (preguntas de investigación)

Las preguntas de la sección 23 del mandato se responderán sobre: (a) el dataset de paper trading
acumulado, sin sesgo de look-ahead pero corto y con sesgo de muestreo de GeckoTerminal, y (b) el
universo histórico on-chain cuando se apruebe la fuente. Las tablas `explosion_events`,
`recovery_events` y `shadow_positions` ya registran los episodios detectados en vivo. **El objetivo de
10.000 tokens no se rellenará con datos fabricados.**
