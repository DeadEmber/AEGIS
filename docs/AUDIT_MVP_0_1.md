# Auditoría de AEGIS MVP 0.1 (FASE 0)

Fecha: 2026-11-26. Alcance: `aegis.py` (207 líneas), `test_aegis.py` (2 pruebas), `README.md`.
El código original se conserva sin cambios en `legacy/mvp_0_1/` y su prueba se ejecuta en CI como
prueba de caracterización (`tests/legacy/test_mvp_0_1_regression.py`).

## 1. Resultado de las pruebas originales

```
test_demo_and_cash ... ok
test_new_month_and_no_duplicate_mint ... ok
Ran 2 tests in 0.068s — OK
```

Las pruebas pasan, pero solo cubren dos escenarios felices con un único token ficticio. No verifican
conciliación, costes reales, errores de API, reinicio, look-ahead ni seguridad.

## 2. Validación de integraciones externas (realizada en esta fase)

Se llamó a las APIs reales desde el entorno de desarrollo el 2026-11-26 y se guardaron las respuestas
como fixtures identificados en `tests/fixtures/recorded_2026-11-26/` (ver `PROVENANCE.md`).

| Fuente | Endpoint probado | Resultado | Observaciones |
|---|---|---|---|
| GeckoTerminal | `/networks/solana/new_pools`, `trending_pools`, `pools/multi`, `tokens/{a}`, `tokens/{a}/info`, `pools/{p}/ohlcv`, `pools/{p}/trades`, `tokens/{a}/pools` | OK | HTTP 429 tras ~7 llamadas en 17 s. El MVP espaciaba 6,2 s (≈10/min): es el orden de magnitud correcto. `market_cap_usd` llega `null` con frecuencia. Incluye pools de bonding curve `pump-fun`, que no son AMM. `tokens/{a}/info` ofrece holders, top-10 %, `mint_authority`, `freeze_authority`, `is_honeypot` (a menudo `"unknown"`). |
| DexScreener | `token-pairs/v1`, `tokens/v1`, `token-profiles/latest/v1` | OK | Cabeceras `x-ratelimit-*`. Liquidez en USD y en unidades base/quote. `marketCap` puede igualar FDV. |
| Jupiter (lite) | `swap/v1/quote` compra y venta, `ultra/v1/order`, `tokens/v2/search` | OK | La cotización de venta permite verificar ruta de salida. Ultra cobra `feeBps: 10`. `tokens/v2` aporta `audit` (mint/freeze desactivados, `topHoldersPercentage`), `organicScore`, `circSupply` y volumen orgánico. |
| Solana RPC público | `getAccountInfo` (jsonParsed), `getMinimumBalanceForRentExemption`, `getRecentPrioritizationFees`, `getTokenLargestAccounts` | Parcial | `getTokenLargestAccounts` devuelve 429 específico del método. Los mints de pump.fun ya son **Token-2022**. Renta de una cuenta de 165 bytes: **1.488.440 lamports**, no los 2.039.280 históricos. Nunca debe codificarse fija. |
| Frankfurter (BCE) | `/v1/latest?base=EUR&symbols=USD` | OK | Tipo diario de referencia (1 EUR = 1,0935 USD el 2026-11-25). |
| CoinGecko | `/simple/price` | OK | SOL = 64,17 EUR. Sirve para convertir costes de red en SOL a EUR. |

El MVP afirmaba que la conexión real no estaba probada. Ahora sí lo está a nivel de esquema, pero la
calidad estadística de los datos (cobertura, sesgos) no queda validada por estas llamadas.

## 3. Errores y debilidades financieras

| # | Severidad | Hallazgo | Consecuencia |
|---|---|---|---|
| F1 | Alta | Dinero en `float`/`REAL` de SQLite. | Errores de redondeo acumulativos; imposible conciliar al céntimo. |
| F2 | Alta | Una sola caja (`available`, `reserve`, `contributed`). No hay libro de movimientos, lotes ni partida doble. | No se puede demostrar que no se genera dinero de la nada ni reconstruir el P&L. |
| F3 | Alta | Costes de Solana omitidos: depósito de renta de la cuenta de token (ATA), comisión base, prioridad y comisiones de agregador. Se usa un `fixed_fee=0.03` arbitrario. | Con SOL=64 EUR la renta de una ATA ronda 0,095 EUR, casi el 10 % de una microposición. Es recuperable al cerrar la cuenta, pero queda inmovilizada mientras la posición existe. Las tarifas de prioridad pueden superar el 5 % por operación en congestión. |
| F4 | Alta | Ritmo de compra: hasta 10 compras por ciclo de 5 min. | Los 100 EUR del mes se agotan en ~50 min de mercado. La diversificación es temporalmente nula: todas las entradas del mes caen en el mismo régimen horario. |
| F5 | Media | La reserva es un 10 % de cada venta con ganancia; las pérdidas no la liberan y no hay estimación fiscal. | La reserva no corresponde a ninguna obligación real y el patrimonio no descuenta impuestos estimados. |
| F6 | Media | El patrimonio excluye por completo las posiciones con datos > 30 min. | Subestimación silenciosa: con más de 200 tokens vigilados la mayoría queda *stale*. Es preferible valorar con descuento explícito y marcar el método. |
| F7 | Media | `multiple = price / entry_price` usa el precio de snapshot de entrada, no el coste efectivo por unidad (incluidas comisiones e impacto). | Sobrestima la rentabilidad real de cada posición. |
| F8 | Media | Filtros en USD contra parámetros definidos en EUR (liquidez 10.000, volumen 5.000). | Incoherencia de unidades de ~9 %. |
| F9 | Media | El filtro de "capitalización" usa FDV sin avisar. | Contradice el requisito de no sustituir capitalización circulante por FDV. |
| F10 | Media | Sin P&L no realizado, TWR, XIRR, drawdown ni comparación con BTC/SOL DCA o efectivo. | No hay forma de saber si la estrategia merece la pena. |
| F11 | Baja | `pnl` se calcula con coste medio proporcional, sin lotes FIFO. | Incompatible con la fiscalidad española de criptoactivos (FIFO por activo homogéneo). |
| F12 | Media | Ignora que en real cada compra con SOL es una permuta fiscal. | Toda simulación en EUR debe documentar esta simplificación. |

## 4. Errores técnicos y de datos

| # | Severidad | Hallazgo |
|---|---|---|
| T1 | Alta | **Datos ausentes convertidos en 0** (`num()` devuelve 0.0 ante `null`). Una liquidez `null` se interpreta como desplome del 100 % y dispara la lógica de venta crítica. |
| T2 | Alta | Solo se reconoce el token *base* del pool. Los pares donde el token nuevo es *quote* quedan mapeados a SOL y se descartan. |
| T3 | Alta | Sin verificación de seguridad: autoridad mint/freeze, extensiones Token-2022 (transfer fee, transfer hook, permanent delegate, non-transferable), ruta de venta ni concentración. Todas las compras se marcan como "security NOT verified". |
| T4 | Alta | Descubrimiento solo vía `new_pools`, con pools de minutos de antigüedad que nunca superan el filtro de 24 h en el momento del descubrimiento. Solo se compran si reaparecen en el refresco, limitado a 200 pools. La watchlist crece sin límite y la mayoría nunca se reevalúa. |
| T5 | Media | Cualquier error HTTP, incluido un 429, aborta el ciclo entero. No hay reintentos, backoff, caché ni distinción entre errores transitorios y definitivos. |
| T6 | Media | La presión vendedora se mide con número de transacciones h1, trivialmente manipulable con microtransacciones, mientras el volumen es h24. Horizontes mezclados. |
| T7 | Media | La edad usa `pool_created_at`, no la creación del token. Un pool nuevo de un token antiguo parece "nuevo". |
| T8 | Media | Decisiones `HOLD` registradas cada ciclo y por posición, sin política de retención. |
| T9 | Media | Esquema sin migraciones, sin claves foráneas, sin índices y sin separación de entornos más allá del flag `mode`. |
| T10 | Baja | Archivo monolítico sin tipos, sin inyección de dependencias de red y sin logging estructurado. |
| T11 | Baja | El reloj se inyecta en `cycle(t)`, lo que es bueno para replays. `status()` usa `now()` en modo live. |

## 5. Funcionalidades reutilizables (y cómo se reutilizan)

| Funcionalidad del MVP | Valoración | Destino en la nueva arquitectura |
|---|---|---|
| Aportación mensual idempotente por clave `YYYY-MM` | Correcta | `accounting.portfolio.PortfolioService.contribute_month` (clave única + partida doble) |
| Ciclo transaccional (`with self.db`) | Correcto | Cada ciclo del worker es atómico. Además, las órdenes tienen clave de idempotencia. |
| Deduplicación por mint y elección del pool más líquido | Correcta pero incompleta | `discovery`: identidad `(network, mint)`; pool primario por liquidez, conservando todos los pools. |
| Impacto de mercado con producto constante | Aproximación válida solo en CPMM | `execution.costs.cpmm_impact`, marcado como estimación. Se prefieren cotizaciones de Jupiter. |
| Venta parcial que conserva una posición residual | Concepto correcto | `decision.exits`, con recuperación de capital y residual mínimo configurable. |
| Ausencia de datos ≠ token muerto | Concepto correcto, implementación inconsistente (T1) | Valores `None` explícitos y estado `UNKNOWN`, nunca `UNTRADABLE` por error de API. |
| Reentrada tras caída desde máximos | Experimental | `decision.second_wave` como heurística versionada, con presupuesto propio (15 %). |
| Exportación CSV | Útil | `aegis export` |
| Errores de feed sin operaciones fabricadas | Correcto | Se conserva. Si los datos están obsoletos, se suspenden las compras. |

## 6. Comportamiento original conservado como regresión

`legacy/mvp_0_1/test_aegis.py` sigue ejecutándose sin cambios. Se considera **económicamente correcto**,
y por tanto se reproduce en el nuevo motor, lo siguiente:

1. Una aportación por mes, sin duplicar.
2. Sin compras duplicadas del mismo mint en el mismo ciclo ni mientras exista posición abierta.
3. Las ventas por debajo del mínimo no fabrican ingresos.

**No** se reproduce el subtotal de 110,41 EUR de la demo. Ese valor depende de `fixed_fee=0.03`, de
ignorar la renta de las ATA y del ritmo de compra sin límite, que son errores F3 y F4.

## 7. Limitaciones reales que persisten tras esta fase

- No existe todavía un universo histórico representativo desde 2024 que incluya tokens desaparecidos.
  GeckoTerminal solo sirve OHLCV de pools aún indexados, lo que introduce sesgo de supervivencia. Ver
  `docs/BACKTEST_METHODOLOGY.md`.
- Los límites gratuitos (~10 llamadas/min en GeckoTerminal) impiden monitorizar miles de tokens con
  frecuencia de minutos. El escalado requiere streaming (Helius/Yellowstone, de pago) o
  aceptar frecuencias adaptativas bajas. Ver `docs/COST_ANALYSIS.md`.
- Sin modelos entrenados. Todos los scores son heurísticos y están etiquetados como tales.
