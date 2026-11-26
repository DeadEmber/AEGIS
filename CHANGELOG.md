# Changelog

## 0.2.3 — 2026-11-26 — segunda auditoría independiente

### Corregido (reproducido antes de corregir)
- **Archivo**: el archivado por `max_id` podía omitir para siempre filas con id menor y timestamp
  posterior, o confirmadas tarde. Ahora los lotes son el prefijo contiguo de ids elegibles, la
  reconciliación por conjunto de ids cubre cualquier omisión y el borrado exige cobertura verificada id
  a id.
- **Backtest**: el comparador usaba pasos fijos de 30 min. Ahora `Worker.replay` comparte el bucle de
  planificación con `run_forever` (mismos ciclos), cuantiza a la resolución de los datos y marca la
  representatividad.

### Añadido
- Backup y restauración del archivo sin servicios de pago: `manifest.json`,
  `aegis archive --verify/--backup/--restore/--import-manifest`, `aegis maintenance` a prueba de fallos,
  unidades systemd, servicio compose y checklist de despliegue.
- Tablas financieras de solo inserción en el archivo (copia inmutable del libro).
- Contrafactual de unidades vendidas en ventas parciales y totales; métricas `forgone_upside` y
  `reinvestment_return` para E2.
- Hipótesis E3: se registra la reentrada bloqueada por un residual abierto, sin cambio de política.

## 0.2.2 — 2026-11-26 — correcciones de auditoría independiente

### Corregido (los tres P1 se reprodujeron antes de corregirlos)
- **Scheduler**: las posiciones no observadas quedaban postergadas para siempre (+600 s por ciclo) y
  perdían prioridad. Ahora siguen vencidas, escalan a prioridad 0 y tienen fuente de respaldo
  (DexScreener). La vía rápida hace efectivos los intervalos de 60, 120 y 180 s. El retraso y la
  antigüedad se registran.
- **Cartera sombra**: cerraba a 14 días con precios obsoletos. Ahora es multihorizonte (24 h a 365 d)
  con estados observado, desconocido o censurado, resultado bruto y liquidable, y límites diarios.
- **Retención**: borraba datos de investigación sin copia. Ahora hay un archivo Parquet verificado
  (manifiesto `archive_batches`, migración 0003), una poda condicionada a ese archivo y episodios
  protegidos a resolución completa.

### Añadido
- `aegis archive [--query SQL]` (DuckDB). Variantes del experimento E2 de monetización. RMST en
  `research.temporal`.

## 0.2.1 — 2026-11-26 — eficiencia temporal del capital (ampliación incremental)

### Añadido
- Migración `0002`: `executions.capital_available_at`, `positions.capital_recovered_at` y columnas de
  intervalo de incertidumbre en `predictions` (`unit`, `lower`, `upper`, `interval_level`, `status`).
  Lo demás se deriva de las entidades existentes, sin tablas nuevas.
- `accounting/efficiency.py`: Capital Turnover, Average Holding Period, Capital Recycling Ratio, tiempo
  hasta recuperar el capital, capital medio comprometido (al coste y liquidable), capital disponible,
  inversiones financiadas con reciclado y recuperación parcial. Separa aportaciones externas de la
  reinversión interna.
- Ejecución: tiempos decisión → ejecución → disponibilidad. Los cobros solo financian compras tras la
  liquidación (15 s). Límite de impacto del 15 % en salidas no críticas, con aplazamiento si el pool es
  demasiado estrecho.
- Indicadores temporales point-in-time, seguimiento completo de los episodios de explosión, etiquetas
  temporales retrospectivas con censura y una base Kaplan–Meier condicionada a disponer de datos.
- ECV (Expected Capital Velocity), experimental y desactivado por defecto. Hoy no está disponible
  porque no hay modelo validado. Variante `aegis-ecv` para el experimento E1.
- API `/capital/efficiency` y panel de eficiencia en el dashboard.

### Corregido
- Un timestamp futuro en un indicador se registraba como dato ausente; ahora lanza `LookAheadError`.

## 0.2.0 — 2026-11-26

Refactorización completa desde el MVP 0.1 (conservado en `legacy/mvp_0_1/`).

### Añadido
- Auditoría del MVP (`docs/AUDIT_MVP_0_1.md`) con validación en vivo de las APIs.
- Paquete modular `src/aegis` con configuración versionada (`StrategyConfig` con hash) y límites de
  riesgo inmutables (`RiskLimits`).
- Esquema relacional de 41 tablas portable SQLite/PostgreSQL y migración Alembic `0001`.
- Contabilidad de partida doble append-only en EUR: compartimentos de inversión, capital reciclado,
  reserva de beneficios y provisión fiscal; lotes FIFO; disposiciones; conciliación automática con 9
  invariantes; TWR, XIRR, MDD, NPR y benchmark DCA; estimación IRPF (base del ahorro, España) con
  compensación de pérdidas a 4 años.
- Motor de ejecución simulada: modelo de costes de Solana (comisión base, prioridad, depósito de renta
  recuperable, comisiones de pool y agregador, impacto CPMM), cotizaciones de Jupiter con caducidad,
  idempotencia, límites de coste e impacto, modo sombra y quema con cierre de cuenta para recuperar la
  renta de posiciones muertas.
- Adaptadores verificados contra respuestas reales: GeckoTerminal, DexScreener, Jupiter (cotizaciones y
  tokens v2), RPC de Solana, BCE (Frankfurter) y CoinGecko. Cliente HTTP con rate limiting, reintentos,
  backoff, caché y salud por fuente.
- Motor de seguridad con evidencia CONFIRMED / INDICATOR / UNKNOWN y bloqueo de lo desconocido en
  comprobaciones críticas: autoridades, extensiones Token-2022, ruta de venta, coste de ida y vuelta,
  concentración, creador y volumen orgánico.
- Biblioteca de 46 indicadores point-in-time con metadatos, 7 índices heurísticos (PRS, DQI, MRI,
  EPI_h, SSI, SWP_h, MCI) y máquina de estados de 15 estados.
- Asignación de capital (presupuesto mensual como tope, ritmo diario, buckets 40/45/15), motor de
  decisiones (recuperación de capital, residual, trailing por volatilidad, PRS, salida por deterioro,
  reentrada) y cartera sombra.
- Worker persistente con fases transaccionales, planificador adaptativo, kill switch y circuit breakers.
- API FastAPI y CLI. Política de retención de datos.
- Dashboard Next.js (`web/`).
- CI en GitHub Actions (lint, tests, PostgreSQL) y Docker / docker-compose.

### Corregido (respecto al MVP)
- Los datos ausentes ya no se convierten en 0. Precisión monetaria exacta. Unidades EUR/USD coherentes.
- Los tokens en el lado *quote* del par ya se reconocen. FDV etiquetado explícitamente cuando falta la
  capitalización.
- Fin del gasto de todo el presupuesto mensual en una hora gracias al ritmo diario.

### Encontrado y corregido durante la validación
- Residuos de 1e-39 al vender fracciones no exactas. Lo detectó una prueba basada en propiedades y se
  resolvió cuantizando el libro.
- Bucle de órdenes de polvo sobre posiciones residuales.
- Falta de salida en posiciones en deterioro estructural.
- Recuperación de capital bloqueada a multiplicadores altos (x40).
- Los motivos de rechazo ocultaban el filtro real.
- El volumen de almacenamiento de features (~55 MB/día) superaba el plan gratuito de Supabase.
