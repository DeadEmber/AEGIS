# Changelog

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
