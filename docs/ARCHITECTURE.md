# Arquitectura

## Visión general

Monolito modular en Python con un **worker persistente** independiente, una **API FastAPI** de lectura y un
**dashboard Next.js**. No se usan microservicios ni infraestructura costosa. La base de datos es
SQLite en local y PostgreSQL/Supabase en simulación permanente, con el mismo esquema y la misma migración.

```
                ┌──────────── fuentes externas (validadas 2026-11-26) ────────────┐
                │ GeckoTerminal · DexScreener · Jupiter · Solana RPC · BCE · CG   │
                └───────────────┬───────────────────────────────────────────────┬─┘
                                │ ResilientHttp (rate limit, retry, cache, salud)│
                     ┌──────────▼──────────┐                                     │
                     │ MarketDataGateway    │  LiveGateway | ScriptedGateway(sim)│
                     └──────────┬──────────┘                                     │
   ┌────────────────────────────▼──────────────────────────────────────────┐     │
   │ Worker.run_cycle()  (fases atómicas, idempotentes, reiniciables)       │     │
   │  1 observar  → 2 persistir obs + aportación → 3 indicadores/estado     │     │
   │  4 seguridad (RPC+Jupiter) → 5 controles de riesgo → 6 decidir+ejecutar │     │
   │  7 valorar, snapshot, conciliar, planificar, salud                     │     │
   └───┬───────────┬──────────────┬─────────────┬──────────────┬──────────┘     │
       │           │              │             │              │                │
  discovery    security     features+scoring  decision     execution(sim)       │
  allocation   state        shadow            risk         accounting (ledger)  │
       └───────────┴──────────────┴──────┬──────┴──────────────┘                │
                                         ▼                                       │
                      SQLite (local) / PostgreSQL-Supabase (simulación)         │
                                         ▲                                       │
                               FastAPI (lectura + control auditado)              │
                                         ▲                                       │
                               Next.js dashboard (Vercel)                       │
```

## Motores (17) y su estado en v0.2

| # | Motor | Módulo | Estado |
|---|---|---|---|
| 1 | Market Data | `marketdata/*` | ✅ 6 fuentes verificadas en vivo; polling con caché. ⏳ streaming (WebSocket/Helius) pendiente |
| 2 | Token Discovery | `discovery/engine.py`, `worker` | ✅ new_pools + trending, filtros explicables, universos etiquetados |
| 3 | Security & Fraud | `security/engine.py` | ✅ autoridades, Token-2022, ruta de venta, round-trip, concentración, orgánico. ⏳ grafo de wallets |
| 4 | Feature Engineering | `features/library.py` | ✅ 46 indicadores point-in-time con registro. ⏳ evaluación de valor predictivo incremental |
| 5 | Explosion Detection | `state/machine.py`, `explosion_events` | 🟡 detección por reglas; ⏳ modelos |
| 6 | Momentum Continuation | `scoring.heuristics.EPI_h` | 🟡 heurístico |
| 7 | Peak Detection | `scoring.heuristics.PRS`, `decision` | 🟡 heurístico + trailing por volatilidad; ⏳ triple barrera / supervivencia |
| 8 | Second Wave | `decision.is_reentry_candidate`, `SWP_h` | 🟡 reglas; ⏳ modelo |
| 9 | Market Regime | `allocation.market_opportunity_index` | 🟡 MOI de amplitud; ⏳ régimen SOL/BTC |
| 10 | Intelligent Decision | `decision/engine.py` | ✅ determinista, auditable, reproducible |
| 11 | Capital Allocation | `allocation/engine.py` | ✅ presupuesto, ritmo, buckets, reciclaje |
| 12 | Execution | `execution/*` | ✅ simulado con cotizaciones Jupiter y costes reales; LIVE inexistente |
| 13 | Portfolio Accounting | `accounting/*` | ✅ partida doble, FIFO, reservas, fiscalidad estimada, conciliación |
| 14 | Historical Simulation | `research/` | 🟡 replay determinista sobre mercado simulado; ⏳ universo histórico real (ver BACKTEST_METHODOLOGY) |
| 15 | Continuous Learning | `learning/shadow.py`, `storage/archive.py`, `model_versions` | 🟡 cartera sombra multihorizonte, etiquetas censuradas y archivo de investigación; ⏳ entrenamiento |
| 16 | Risk Management | `risk/controls.py` | ✅ kill switch, circuit breakers, límites |
| 17 | Monitoring & Observability | `source_health`, `worker_heartbeats`, `system_events`, logs JSON | ✅ básico |

Leyenda: ✅ funcional y probado · 🟡 baseline heurístico funcional · ⏳ pendiente.

## Flujo de un ciclo y garantías

- **Sin llamadas de red dentro de transacciones de escritura.** Se consultan las cotizaciones antes de
  abrir la transacción de decisión y ejecución, y el motor de ejecución vuelve a validar su caducidad.
- **Atomicidad por fase.** Si el proceso muere entre fases, lo ya confirmado es coherente y el ciclo
  siguiente continúa. Las claves de idempotencia (`orders.idempotency_key`,
  `ledger_transactions.idempotency_key`) impiden los duplicados.
- **Separación de datos.** `control_flags.data_mode` (`simulated`/`live`) se fija en el primer
  arranque y el worker se niega a mezclar datos.
- **Reproducibilidad.** Cada decisión guarda `inputs_hash`, `strategy_hash` y `engine_version`. La
  exploración aleatoria usa una semilla derivada de `StrategyConfig.seed` y del id de ciclo.

## Decisiones de arquitectura (ADR)

| ADR | Decisión | Alternativas | Motivo |
|---|---|---|---|
| 1 | Monolito modular + worker | microservicios | Coste y complejidad; los límites de las fuentes, y no la CPU, son el cuello de botella |
| 2 | SQLAlchemy Core + Alembic | ORM completo, SQL a mano | Portabilidad SQLite/PG, control explícito de transacciones |
| 3 | Decimal como TEXT en SQLite y NUMERIC en PG | float, enteros | Exactitud sin perder legibilidad; enteros complicarían precios de 1e-9 |
| 4 | Escala de libro 1e-12 EUR | contexto de 40 dígitos sin cuantizar | Una prueba basada en propiedades demostró residuos de 1e-39 que rompían la suma cero |
| 5 | Partida doble en EUR | caja única (MVP) | Conciliación demostrable y ningún dinero creado por errores de reinversión |
| 6 | Costes de adquisición capitalizados y depósito como activo | todo como gasto | Coherente con el IRPF (valor de adquisición) y con el carácter recuperable de la renta |
| 7 | Cliente HTTP síncrono con espaciado | asyncio | Las fuentes gratuitas permiten ~10 llamadas/min; la concurrencia no aporta. Se reevaluará con streaming |
| 8 | Jupiter `lite-api` para cotizaciones | `api.jup.ag` (clave) | Gratuito y verificado; se puede cambiar con `AEGIS_JUPITER_API_KEY` y la URL |
| 9 | Heurísticas versionadas antes que ML | entrenar ya | No existe aún un dataset representativo sin sesgo de supervivencia (ver BACKTEST_METHODOLOGY) |
| 10 | API propia entre el dashboard y la DB | dashboard → Supabase directo | Una sola capa de autorización y el formato decimal exacto controlado |
| 11 | Dos niveles de almacenamiento: DB operativa + archivo Parquet (zstd) consultado con DuckDB | Data warehouse, object storage gestionado | Sin infraestructura nueva; compresión medida de 6–21x; el archivo es el dataset de investigación de por vida |
| 12 | Borrar solo lo archivado y verificado (SHA-256 canónico + comprobación de ids) | Retención por antigüedad | La auditoría demostró pérdida irreversible de datos de entrenamiento |
| 13 | Vía rápida del worker (despertar al primer vencimiento de prioridad ≤ 3, mínimo 60 s) | Bajar el ciclo completo a 60 s | El descubrimiento y la seguridad cuestan cuota; solo lo urgente necesita alta frecuencia |
| 14 | Cartera sombra multihorizonte con ventanas de frescura | Horizonte fijo de 14 días | Las etiquetas de largo plazo (30–365 d) y la censura son imprescindibles para supervivencia y E1/E2 |
| 15 | Archivado por prefijo contiguo de ids + reconciliación por conjunto de ids | Marca de agua `max_id` | La auditoría demostró omisiones permanentes por corte temporal y confirmaciones tardías |
| 16 | Backup por ficheros inmutables + `manifest.json` autosuficiente | Snapshots de volumen, servicio gestionado | Coste cero, verificable sin base de datos, incremental y restaurable lote a lote |
| 17 | Replay con el mismo `step`/`plan_wake` que el worker + resolución de datos explícita | Pasos fijos | La equivalencia temporal es condición para que un backtest sea comparable con la operativa |
| 18 | Dos niveles históricos: `Worker.replay` + `HistoricalGateway` (fidelidad: el worker de producción sin cambios) y un simulador rápido por eventos (`research.backtest`) que usa el **mismo** motor de ejecución simulada y el **mismo** libro (en una SQLite en memoria) | Solo el worker completo (horas por variante); un simulador independiente con su propia contabilidad | Las variantes de investigación necesitan segundos por ejecución, sin duplicar contabilidad ni costes; el worker completo valida la integración |
| 19 | Observaciones contemporáneas y resultados retrospectivos en contenedores distintos. Las políticas solo reciben `MarketView` (lanza `LookAheadError`) | Un único DataFrame con columnas futuras | Imposibilita por construcción usar `peak_*`, etiquetas o precios futuros en una decisión |
| 20 | Ejecución por estructura de mercado: CPMM exacto con reservas observadas (y reserva virtual de PumpSwap). DLMM, CLMM, DAMM y desconocidos: sin ejecución | Aplicar CPMM a todo | Un CPMM sobre un pool concentrado inventa liquidez; el mandato prohíbe la simulación engañosa |
| 21 | Comisión de PumpSwap medida por tramos de capitalización (Launch Tape, 417 k trades: ~1,0–1,26 % por lado) en lugar de 30 pb | Supuesto fijo | Es el coste dominante de un ticket de 1 EUR; la sensibilidad a 30 y 125 pb queda documentada |
| 22 | La financiación de cada compra respeta el presupuesto **por cuenta** (`allowance`): el reciclado solo paga hasta su tope | Reciclado primero sin límite (0.2) | Defecto encontrado en la fase 2: el tope diario de reciclado no limitaba la cuenta que pagaba, y la contabilidad por categorías de efectivo era incorrecta |
| 23 | Modelos de la fase 2 en numpy (regresión logística L2, isotónica, conjuntos bootstrap por token) | scikit-learn / LightGBM | Auditables, sin dependencias nuevas y suficientes para una línea base. Se usan solo en investigación y modo sombra |

## Entornos

| Entorno | DB | Modo | Datos |
|---|---|---|---|
| local | SQLite `var/*.sqlite` | PAPER / demo | live o simulated (nunca mezclados) |
| dev | PostgreSQL local o Supabase dev | PAPER | live |
| simulation | Supabase/PG dedicado | PAPER permanente | live |
| production | PG dedicado | SHADOW / LIVE_DISABLED | live; la ejecución real no existe |

Cada entorno usa su propia `AEGIS_DATABASE_URL`, sus propias credenciales y su propio `.env`.
