# Roadmap

| Fase | Entregable | Estado |
|---|---|---|
| 0 | Auditoría del MVP y viabilidad técnica | ✅ `AUDIT_MVP_0_1.md` |
| 1 | Refactorización, arquitectura y base de datos | ✅ paquete modular, 41 tablas, Alembic, SQLite + PG |
| 2 | Integración verificable de datos reales | ✅ 6 fuentes verificadas, fixtures reales. ⏳ streaming |
| 3 | Descubrimiento, seguridad y cartera simulada | ✅ |
| 4 | Biblioteca de indicadores | ✅ 46 indicadores. ⏳ evaluación incremental |
| 5 | Laboratorio histórico de explosiones | 🟡 fase 2: corpus post-graduación (junio–julio de 2026) y Launch Tape (octubre) con adaptadores point-in-time. ⏳ 2024–hoy y universo maduro (≥ 24 h) |
| 6 | Modelos de detección de máximos | 🟡 referencias de valor de continuación (walk-forward); sin promoción |
| 7 | Modelos de segundas explosiones | ⏳ |
| 8 | Motor de decisiones y asignación de capital | ✅ baseline determinista |
| 9 | Backtesting integral | 🟡 `HistoricalGateway` + `Worker.replay` y simulador rápido con el libro de producción; walk-forward, bootstrap y Holm. Falta una muestra independiente |
| 10 | Shadow trading y aprendizaje controlado | 🟡 cartera sombra, `SHADOW_TRADING` y Champion–Challenger de investigación con preregistro |
| 11 | Dashboard profesional | 🟡 primera versión (Overview, Portfolio, Discovery, Peak Radar, Second Wave, Decisions, Capital, Risk, System) |
| 12 | Despliegue permanente en paper trading | ⏳ requiere aprobación del propietario (coste) |
| 13 | Validación económica y operativa | ⏳ |
| 14 | Preparación técnica de un piloto real (deshabilitado) | ⏳ no iniciado; requisitos en `SECURITY.md` |

## Siguiente bloque ejecutable (por prioridad, tras la fase 2)

0. **Muestra independiente para los challengers preregistrados** (`docs/research/PHASE2_SUMMARY.md` §5):
   paper trading permanente del universo post-graduación (punto 1) o días de Launch Tape comprados con
   autorización. No se debe probar ninguna hipótesis nueva sobre junio, julio o el 10 de octubre.
0b. **Universo de la política de producción (≥ 24 h)**: buscar un dataset point-in-time que siga los
   tokens más allá de 48 h, con recuentos de 24 h. Sin él, la política operativa sigue sin evaluar.
1. **Despliegue permanente de paper trading** (fase 12). Checklist en `DEPLOYMENT.md`: unidades systemd,
   `aegis maintenance` diario con backup verificado, archivo y backup en discos distintos. Elegir VPS/Fly y Supabase. Sin esto no se
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

Fase 2 (E4–E10): resultados en `docs/research/PHASE2_RESULTS.md` y conclusiones en `PHASE2_SUMMARY.md`.

| Id | Pregunta | Estado y resultado |
|---|---|---|
| E4 | ¿Se reproducen las estrategias de referencia con datos reales point-in-time y costes completos? | Hecho. El orden coincide (contexto > estricta + trailing > básica), pero todas pierden: 166,6 / 144,3 / 47,8 EUR sobre 200 en el corpus. En Launch Tape pierden todas |
| E5 | ¿Reinversión completa frente a limitada? | Hecho. Con expectativa negativa, reciclar más pierde más: 190,6 (ritmo diario, sin reciclaje) > 169,4 (limitado) > 144–152 (completo) |
| E6 | Ablación de factores y normalización del contexto | Hecho. Solo importan la liquidez ≥ 20.000 EUR y la ratio compras/ventas ≥ 1,75. El contexto bruto está contaminado por la cobertura (ρ = 0,83) |
| E7 | Selección supervisada walk-forward | Hecho. AUC 0,48–0,56 fuera de muestra, sin valor económico |
| E8 | Valor de continuación y agente | Hecho. AUC 0,60–0,69 (30 min), pero el agente pierde frente al Champion en todas las variantes |
| E9 | Carteras A–I, bootstrap, Holm, costes, tickets, frecuencia | Hecho. Ninguna cartera supera al efectivo; el Champion empata con la selección aleatoria tras seguridad; nada es significativo tras Holm |
| E10 | Recuperación de perdedoras y monetización | Hecho, dentro de muestra. Trailing 20 % y stop ×0,7 mejoran; los residuales destruyen valor |

### E3 — Reentrada durante una segunda explosión con residual abierto (segunda auditoría, P2)

- **Hipótesis.** Excluir los tokens en cartera impide invertir en una segunda explosión cuando se
  conserva un residual. Una reentrada deliberada podría capturar la segunda ola mejor que el residual
  solo.
- **Taxonomía.**
  - **REENTRY**: nueva posición tras una salida total (existe).
  - **REENTRY_ON_RESIDUAL**: compra deliberada con presupuesto `second_chance`, sobre un token con
    residual abierto y con señal de segunda ola (**no existe**).
  - **Compra duplicada**: misma intención repetida (impedida por la exclusión y la idempotencia).
  - **INCREASE**: aumento de una posición viva (no lo genera la política).
- **Datos que ya se recogen.** Decisiones WATCH `reentry_blocked_by_open_residual` y cohorte sombra
  `reentry_blocked_residual`, con horizontes de 24 h a 365 d.
- **Regla.** No se modifica la política ni se aumenta la exposición automáticamente hasta que E3 muestre
  valor fuera de muestra frente a mantener solo el residual.

### E2 — Política de monetización y grandes ganadores (auditoría, P2)

- **Pregunta.** ¿Cuánto beneficio se sacrifica en los grandes ganadores al recuperar capital pronto,
  frente a lo que aporta reinvertir ese capital reciclado?
- **Variantes** (`research.compare.monetisation_grid`, mismas condiciones económicas):
  - Recuperación del capital inicial a x2, x5, x10, x20 o sin recuperación anticipada.
  - Residual del 5 %, 10 % o 25 %.
  - Trailing activado a x3, a x10 o desactivado.
- **Métricas.**
  - Objetivo neto y patrimonio liquidable.
  - Capital reciclado y su reinversión (`purchases_from_recycled`).
  - Inversiones financiadas con reciclado y Capital Turnover.
  - Beneficio sacrificado en ganadores: valor liquidable posterior de las **unidades vendidas** (parciales
    y totales) frente al importe neto obtenido (`research.monetisation.forgone_upside`), comparado con el
    P&L atribuido al capital reciclado reinvertido (`reinvestment_return`).
  - Top Winner Dependency y probabilidad de beneficio fuera de muestra.
- **Regla.** La política operativa por defecto (recuperación a x2, residual del 10 %, trailing desde
  x3) **no se modifica** sin evidencia fuera de muestra con IC 90 % > 0.
- **Observación preliminar (simulada, no evidencia):** en la trayectoria `rocket`, no recuperar a x2
  dejó vender más arriba con el trailing (4,61 EUR frente a 3,37 EUR reciclados).

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
