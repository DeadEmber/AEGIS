# AGENTS.md — instrucciones para agentes y colaboradores

Lee esto antes de modificar el repositorio. El estado vivo del proyecto está en `docs/STATUS.md`.
Actualízalo al terminar cada bloque de trabajo.

## Reglas no negociables

1. **Nunca** añadir firma de transacciones, claves privadas, seed phrases ni endpoints de envío de órdenes.
   La ejecución real requiere autorización expresa del propietario y una revisión independiente
   (ver `SECURITY.md`). `ExecutionMode.LIVE_DISABLED` debe seguir fallando de forma explícita.
2. **Dinero siempre en `Decimal`**, cuantizado a la escala del libro (`core.money.q_eur`, 1e-12 EUR).
   Los `float` están prohibidos en importes y el `Ledger` rechaza importes no cuantizados.
3. **Todo movimiento monetario pasa por `Ledger.post`**: partida doble, suma cero, idempotencia y sin
   saldos de caja negativos. Nunca se hace `UPDATE` ni `DELETE` sobre `ledger_*`. Las correcciones
   se registran como transacciones nuevas.
4. **Ausencia de dato ≠ cero ≠ seguro.** Usa `None` y registra el motivo. Un error de API produce
   `UNKNOWN`, nunca `UNTRADABLE` ni `PASS`.
5. **Sin look-ahead**: los indicadores reciben `as_of` y solo observaciones con `observed_at <= as_of`
   (`features.library.compute` lo verifica). Los backtests usan `ManualClock`.
6. **Scores heurísticos ≠ probabilidades.** Todo lo de `scoring.heuristics` es `kind="heuristic"`.
   Para presentar algo como probabilidad hace falta un modelo calibrado, registrado y validado
   (`docs/MODEL_REGISTRY.md`).
7. **El aprendizaje no puede modificar `RiskLimits`** ni los `control_flags`.
8. Las bases de datos con datos simulados y reales no se mezclan nunca (flag `data_mode`).
9. No contratar servicios de pago ni desplegar infraestructura con coste sin aprobación del propietario.
10. No declarar una integración como verificada si no se ha probado contra la fuente real. Las
    respuestas reales se guardan como fixtures con procedencia (`tests/fixtures/*/PROVENANCE.md`).

## Flujo de trabajo

- Python 3.11+, `pip install -e ".[dev,postgres]"`.
- Antes de hacer commit: `ruff check src tests` y `pytest`, ambos en verde.
- Los cambios de esquema van en una migración Alembic nueva (`src/aegis/storage/migrations/versions/`).
  Debe ser compatible con SQLite y PostgreSQL y pasar `tests/integration/test_postgres.py` con
  `AEGIS_TEST_POSTGRES_URL`.
- Cualquier umbral económico nuevo va en `StrategyConfig`, no codificado en el código.
- Documenta las decisiones en `docs/ARCHITECTURE.md` (sección ADR) y las limitaciones en `docs/STATUS.md`.

## Mapa rápido

| Área | Módulo |
|---|---|
| Configuración | `aegis/config.py` (`Settings`, `StrategyConfig`, `RiskLimits`) |
| Datos de mercado | `aegis/marketdata/*` (HTTP resiliente + adaptadores) |
| Seguridad | `aegis/security/engine.py` |
| Indicadores | `aegis/features/library.py`, `aegis/scoring/heuristics.py` |
| Estados | `aegis/state/machine.py` |
| Descubrimiento | `aegis/discovery/engine.py` |
| Asignación | `aegis/allocation/engine.py` |
| Decisiones | `aegis/decision/engine.py` |
| Ejecución simulada | `aegis/execution/*` |
| Contabilidad | `aegis/accounting/*` |
| Riesgo | `aegis/risk/controls.py` |
| Worker | `aegis/worker/*` |
| API | `aegis/api/app.py` |
| Dashboard | `web/` (Next.js) |
