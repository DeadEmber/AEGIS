# Datasets históricos (Fase 2, bloque A)

Registro canónico: `src/aegis/history/registry.py`. Cada dataset se identifica por repositorio, **revisión
fijada** (commit de HuggingFace) y SHA-256 de cada fichero. `aegis datasets fetch` descarga solo ficheros
gratuitos y públicos de esa revisión y rechaza cualquier fichero cuyo hash no coincida. `aegis datasets
verify` recalcula los hashes. `AEGIS_PROVENANCE.json` (junto a los datos, en `var/datasets/<id>/<rev>/`)
registra qué se descargó, de dónde y cuándo. **Los datos nunca se suben al repositorio.**

| Id | Fuente | Revisión | Licencia | ¿Redistribuible? | Periodo |
|---|---|---|---|---|---|
| `pumpfun_corpus` | `Slinky21/Pumpfun_Memecoin_Corpus` | `13bec2bf3089d56d50c35e602616d1b993bb37f5` | MIT (etiqueta) / CC BY 4.0 (README): atribución obligatoria | sí, con atribución | 2026-06-05 → 2026-07-14 |
| `launchtape` | `launchtape/solana-launch-tape` (muestra gratuita) | `791aeee832cb9a10f55361f10972b33272c857f8` | Licencia propia: uso personal y de investigación dentro del equipo; **prohibida la redistribución total o parcial** | **no** | 2026-10-10 (14–16 UTC), 2026-10-13, 2026-10-14 |

Los tres ficheros adjuntados por el propietario (`pregrad`, `postgard_outcomes` y `migrations`) son
idénticos byte a byte a los de esas revisiones (los SHA-256 coinciden).

Atribución exigida por el corpus: *Slink Dev (slink21taken). PumpFun Launch Corpus. 2026.*

Hay una prueba (`test_no_non_redistributable_dataset_file_is_committed`) que falla si algún fichero de
Launch Tape aparece en el repositorio. Las pruebas usan almacenes **sintéticos** con la misma forma
(`tests/history_synth.py`). Los resultados derivados (tablas, métricas, modelos) sí se pueden publicar.

## Qué es contemporáneo y qué es retrospectivo

| Dataset / tabla | Uso en AEGIS | Naturaleza |
|---|---|---|
| corpus `postgard_snapshots` | observaciones de mercado tras la graduación (precio, reservas, liquidez, volúmenes y compras/ventas por ventana) | contemporánea (sondeos con `snapshot_time`) |
| corpus `migrations` | instante de graduación (conocido en ese instante) | contemporánea |
| corpus `tokens` | atributos del lanzamiento (creador, compra del dev, concentración corregida). Se **excluyen** `graduated_at`, `peak_*`, `trade_count`, `is_zombie`, `data_quality_score`, `is_training_ready` y `entry_price_*` (indicadores de la etiqueta, KNOWN_ISSUES §7) | mixta: se usa solo lo de detección |
| corpus `postgard_outcomes` | etiquetas y picos | **retrospectiva**: `HistoricalStore.outcomes`, nunca entra en una decisión |
| Launch Tape `gradtrades` | estado del pool tras cada trade (reservas reales y virtuales, comisiones), primeros 30 min tras cada graduación | contemporánea, a nivel de trade |
| Launch Tape `pregrad` `record` | atributos en la graduación (creador, financiación, compradores del slot) | contemporánea (conocida en su `t`) |
| Launch Tape `pregrad` `outcome` | `capAt`, `maxGain`, `minDrop` y `crashed` a +10 min | **retrospectiva** |
| Launch Tape `padfeed` `qpx` | precio de SOL en USD cada 30 s | contemporánea |

## Estructuras de mercado

| Fuente | Estructura | Tratamiento |
|---|---|---|
| PumpSwap (`pumpswap`, `pump-amm`) | CPMM (en octubre de 2026 con una reserva virtual de quote que forma parte de la curva de precio) | ejecución exacta x·y=k sobre las reservas observadas |
| Curva de pump.fun tras graduar (`pumpfun`) | curva completada | no negociable: solo cobertura |
| Meteora, Raydium | ambiguas (DLMM/DAMM/DBC, AMMv4/CPMM/CLMM) | `UNKNOWN`: **ninguna ejecución simulada** |

## Cobertura y defectos conocidos (verificados en esta revisión)

- **Corpus**: 5.701 graduaciones, de las que 5.064 tienen un pool PumpSwap negociable. Hay 1,0 M de
  observaciones negociables y 390 k filas no negociables (curva completada o estructura desconocida).
  - Resolución: ~62 s entre 0 y 2 h tras graduar, ~5 min de 2 a 6 h, ~15 min de 6 a 24 h y ~30 min de
    24 a 48 h. **El seguimiento termina a las 48 h**: más allá, todo es censura.
  - Huecos sin ninguna graduación registrada: del 10 al 18 de junio (las graduaciones empiezan
    realmente el 19), el 3 de julio (corte documentado en KNOWN_ISSUES §5.5) y el **12 de julio, que no
    documenta KNOWN_ISSUES**.
  - El 25 % de `migrations.pool_address` son centinelas (`synthetic_graduation_queue`,
    `backfilled_from_pumpswap_trade`): nunca se usan como dirección.
  - No hay recuentos de transacciones ni de traders únicos en 24 h. Los filtros de descubrimiento de
    producción que los exigen fallan por «desconocido», que nunca cuenta como aprobado.
- **Launch Tape**: 105 graduaciones, 417.347 trades, 2 cortes breves del feed (registrados como huecos)
  y seguimiento de 30 min por token.
  - Los precios reconstruidos coinciden con su propio `capAt` a +60, +300 y +600 s (mediana del cociente:
    1,000).
  - Como el pool nace en la graduación y se registran todos los trades, los recuentos de 24 h son
    exactos.
- **Seguridad histórica**: ningún dataset contiene cuentas de mint históricas.
  - Por defecto la seguridad es `UNKNOWN`, y el motor de producción bloquea las compras.
  - Con `launchpad_invariants=True` se asumen los invariantes de pump.fun para mints estándar (SPL
    clásico, autoridades revocadas) y toda la evidencia queda limitada a `INDICATOR`.
  - Los mints en modo mayhem siguen como desconocidos.

## Fuentes complementarias revisadas y no incorporadas

MELT, `solana-memecoin-dataset`, `fingerprinter-dataset`, CryptoBot, `pump-fun-trades-sample` y el
registro de Zenodo citados por el propietario no se han descargado en este bloque. No aportan una
muestra post-graduación independiente con reservas ejecutables para el periodo, y su licencia y calidad
deben revisarse antes de usarlas. Queda pendiente en el ROADMAP.
