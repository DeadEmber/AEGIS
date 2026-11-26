# Metodología de backtesting e investigación

## Estado honesto

**Fase 2 (2026-11-26):** ya existe un backtest con **datos de mercado reales point-in-time**, pero de
periodos cortos y ya explorados por el propietario:

- Pumpfun Corpus: 19/06–15/07/2026, post-graduación a ~60 s, seguimiento de 48 h.
- Launch Tape: 10/10/2026, 14–16 UTC, a nivel de trade, 30 min por token.

Ver «Backtest histórico (fase 2)» más abajo y `docs/research/PHASE2_RESULTS.md`. Sigue sin existir un
backtest 2024–hoy y **no hay ninguna muestra histórica independiente sin explorar**.

Antes de la fase 2 existía:

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

## Equivalencia temporal con el worker operativo

- `Worker.replay(until, cadence="adaptive", data_resolution_s=r)` usa **la misma** lógica que
  `run_forever`: `step` y `plan_wake` sobre `next_wake`, con ciclo completo de 300 s y vía rápida
  ≥ 60 s. Una prueba verifica que ambos producen la misma secuencia de ciclos.
- Con datos históricos de resolución `r`, el replay despierta solo en la rejilla de datos (redondeo
  hacia arriba). El gateway sirve la observación del último punto ≤ t, sin look-ahead.
- Cada resultado (`research.compare.run_variant(...)["replay"]`) declara:
  - `operational_equivalence`: falso con `cadence="fixed"`.
  - `fast_dynamics_representative`: falso si el muestreo efectivo supera el intervalo más rápido
    configurado (60 s), es decir, siempre con velas de 5 min o pasos de 30 min.
  - `notes`, con la explicación.
- **No se presentarán como representativos** los resultados sobre explosiones rápidas obtenidos con
  `fast_dynamics_representative=false`.
- `cadence="fixed"` se mantiene solo para pruebas mecánicas baratas (invariancias de políticas).

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


## Backtest histórico (fase 2)

### Dos niveles (ADR-18)

1. **Fidelidad**: `Worker.replay` + `HistoricalGateway`. Es el worker de producción sin cambios
   (descubrimiento, seguridad, indicadores, estados, decisiones, ejecución, libro, conciliación y
   programador) sobre el almacén histórico, con el reloj manual y la resolución real de los datos.
   - La base queda marcada `data_mode=historical` y nunca se mezcla con datos vivos ni simulados.
   - Seguridad: ningún dataset trae cuentas de mint históricas. Por defecto es UNKNOWN y bloquea las
     compras.
   - Con `launchpad_invariants=True` se usan los invariantes de pump.fun para mints estándar, siempre
     con evidencia `INDICATOR` y nunca `CONFIRMED`.
2. **Investigación**: `research.backtest`. Es un simulador por eventos que recorre cada observación en
   orden temporal.
   - Las órdenes pasan por el **mismo** `SimulatedExecutionEngine` y se contabilizan con el **mismo**
     `PortfolioService`/`Ledger` (en SQLite en memoria): partida doble, lotes FIFO, depósitos,
     liquidación diferida de 15 s, reservas y conciliación al final de cada ejecución.
   - El presupuesto por cuenta (`allowance`) es el mismo que en el worker.

### Reglas propias del histórico

- **Point-in-time**: las políticas solo reciben `MarketView`, que lanza `LookAheadError` ante cualquier
  observación posterior a `now`. Los resultados retrospectivos (`postgard_outcomes`, `pregrad outcome`)
  viven en `HistoricalStore.outcomes` y solo los usan la evaluación y el etiquetado.
- **Ejecución**:
  - CPMM exacto sobre las reservas observadas, con reserva virtual de PumpSwap cuando la fuente la da.
  - En Launch Tape, la orden se ejecuta con el estado del pool **en el instante de decisión + latencia**
    (nivel de trade).
  - En el corpus se ejecuta con la última observación (60 s). Es optimista (ver latencia, abajo) y se
    trata con sensibilidad de deslizamiento adverso de 0, 100, 300 y 600 pb.
- **Costes**:
  - Comisión de PumpSwap por tramos medida en Launch Tape (ADR-21).
  - Comisión base y de prioridad de Solana.
  - Depósito de renta recuperable.
  - Deslizamiento adverso del simulador de papel (100 pb por lado por defecto).
- **Censura**: el corpus deja de observar cada token a las 48 h. Se informan tres patrimonios:
  - liquidable con las reglas de valoración (los datos obsoletos llevan descuento y pasan a no
    valorados);
  - valorado a la última observación;
  - con las posiciones censuradas a cero.
- **Monetización imposible**: si el valor liquidable es menor que la venta mínima (0,10 EUR), se mantiene
  la posición; el depósito sigue siendo recuperable mediante quema y cierre.
- **Sin muestra independiente**: los dos datasets ya los exploró el propietario.
  - La validación fuera de muestra es **walk-forward dentro del corpus**, con pliegues de 4 días,
    entrenamiento expansivo y embargo de 48 h (igual al horizonte máximo de la etiqueta). La purga
    elimina las etiquetas que se resuelven dentro del embargo.
  - Launch Tape se usa como control fuera de periodo **con reglas congeladas**, sin reajustar nada, y
    tampoco es independiente.
  - La muestra nueva será el **paper trading permanente** desde ahora, con la política preregistrada
    (STATUS).
- **Comparaciones múltiples**: cada variante probada en E4–E9 cuenta para la familia de Holm–Bonferroni
  sobre los p-valores del bootstrap por bloques (3 días) de la diferencia diaria frente al Champion.

### Latencia medida (Launch Tape, 30 s–30 min tras graduar)

| Retraso | Deriva media | Movimiento absoluto p50 | Movimiento absoluto p90 |
|---|---|---|---|
| 2 s | −28 pb | 0 pb | 256 pb |
| 10 s | −140 pb | 39 pb | 776 pb |
| 60 s | −866 pb | 256 pb | 2.630 pb |

Una observación de 60 s puede estar desfasada hasta una cadencia entera. El 1 % de deslizamiento por
defecto **solo cubre latencias de pocos segundos**. Por eso los resultados del corpus son una cota
optimista para las salidas que se disparan en mercados que caen.
