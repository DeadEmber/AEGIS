# AEGIS — Autonomous Asymmetric Crypto Investment Intelligence

Plataforma de investigación, *paper trading* y aprendizaje cuantitativo para una cartera **masivamente
diversificada** de criptomonedas de baja capitalización en Solana: aportaciones de 100 EUR/mes y
microposiciones de ~1 EUR, con monetización y reciclaje del capital.

> **No opera dinero real.** No contiene wallet, claves ni firma de transacciones. La ejecución real
> no existe en el código (`LIVE_DISABLED` falla de forma explícita). La hipótesis de rentabilidad
> **no está demostrada**: ver [`docs/STATUS.md`](docs/STATUS.md) y
> [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md).

## Estado (v0.2.0)

Funcional y probado en esta versión: datos reales de mercado → seguridad → indicadores → decisiones →
ejecución simulada con costes reales de Solana → contabilidad de partida doble → conciliación → API.
Lo pendiente está en [`docs/ROADMAP.md`](docs/ROADMAP.md). El registro de trabajo para continuar en
nuevas sesiones está en [`docs/STATUS.md`](docs/STATUS.md).

## Instalación

Requisitos: Python ≥ 3.11 (probado con 3.13), Node ≥ 20 (solo para el dashboard).

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev,postgres]"
```

```bash
cp .env.example .env
```

En Windows (PowerShell): `py -3 -m venv .venv`, `.venv\Scripts\pip install -e ".[dev,postgres]"` y
`.venv\Scripts\aegis ...`.

## Uso

```bash
.venv/bin/aegis db upgrade
```

```bash
.venv/bin/aegis probe
```

`probe` comprueba una vez cada fuente externa (GeckoTerminal, DexScreener, Jupiter, RPC de Solana,
BCE, CoinGecko) y muestra la latencia.

```bash
.venv/bin/aegis demo --db sqlite:///var/demo.sqlite --hours 72
```

`demo` ejecuta un mercado **simulado** determinista. No es evidencia de rentabilidad y la base de
datos queda marcada como `simulated`: AEGIS se niega a mezclarla con datos reales.

```bash
.venv/bin/aegis once
```

```bash
.venv/bin/aegis run --interval 300
```

`once` y `run` usan datos de mercado reales con ejecución simulada (`PAPER_TRADING`). El worker
puede detenerse en cualquier momento (Ctrl+C / SIGTERM) sin perder estado.

```bash
.venv/bin/aegis status
```

```bash
.venv/bin/aegis reconcile
```

```bash
.venv/bin/aegis api --port 8000
```

```bash
.venv/bin/aegis kill-switch on --reason "pausa manual"
```

Otros comandos:

- `aegis export --out exports/`.
- `aegis archive`: archivo de investigación en Parquet verificado. Con
  `aegis archive --query "select count(*) from market_snapshots"` lo consulta con DuckDB.
- `aegis prune`: retención de la base operativa. Solo borra lo ya archivado y nunca toca la contabilidad.
- `aegis maintenance`: tarea diaria (archivo → verificación → backup → poda) que no poda ante cualquier
  fallo. Para el despliegue permanente, ver `docs/DEPLOYMENT.md` y `deploy/systemd/`.

### Dashboard

```bash
cd web && npm install && npm run dev
```

Abre http://localhost:3000 con la API en `http://localhost:8000` (configurable con
`NEXT_PUBLIC_AEGIS_API`).

## Pruebas

```bash
.venv/bin/pytest
```

```bash
.venv/bin/ruff check src tests
```

Detalle de la estrategia de pruebas y de los 30 casos obligatorios en [`docs/TESTING.md`](docs/TESTING.md).

## Documentación

| Documento | Contenido |
|---|---|
| [docs/AUDIT_MVP_0_1.md](docs/AUDIT_MVP_0_1.md) | Auditoría del MVP de partida |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Arquitectura, motores y decisiones (ADR) |
| [docs/STRATEGY_SPECIFICATION.md](docs/STRATEGY_SPECIFICATION.md) | Reglas económicas, función objetivo y KPIs |
| [docs/RISK_MANAGEMENT.md](docs/RISK_MANAGEMENT.md) | Controles, circuit breakers y fiscalidad |
| [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md) | Viabilidad de tickets de 1 EUR, costes de datos e infraestructura |
| [docs/BACKTEST_METHODOLOGY.md](docs/BACKTEST_METHODOLOGY.md) | Sesgos, limitaciones del universo histórico |
| [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md) | Indicadores e índices propietarios |
| [docs/MODEL_REGISTRY.md](docs/MODEL_REGISTRY.md) | Modelos, Champion–Challenger |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | Modelo de datos |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Entornos y despliegue |
| [docs/TESTING.md](docs/TESTING.md) | Pruebas y criterios de aceptación |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Fases y siguiente bloque |
| [SECURITY.md](SECURITY.md) | Seguridad y gestión de secretos |
| [AGENTS.md](AGENTS.md) | Instrucciones para agentes/colaboradores |
| [CHANGELOG.md](CHANGELOG.md) | Historial de cambios |
