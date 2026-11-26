"""Generate the auto sections of docs/FEATURE_CATALOG.md and docs/DATA_DICTIONARY.md from code.

Run: python scripts/gen_docs.py   (tests/unit/test_docs.py fails if the docs are stale)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aegis.features.library import FEATURE_SET_VERSION, REGISTRY  # noqa: E402
from aegis.storage.schema import metadata  # noqa: E402

BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"

TABLE_NOTES = {
    "networks": "Redes soportadas (solo `solana` habilitada).",
    "tokens": "Activo identificado por (network, address). Estado de mercado actual, ATH observado, contadores.",
    "pools": "Pools de cada token; `token_side` indica si el token es base o quote; `is_primary` = pool de referencia.",
    "exchange_rates": "Tipos de referencia con fuente y hora (EUR/USD BCE, SOL/EUR CoinGecko).",
    "market_snapshots": "Observaciones point-in-time por pool (`observed_at` = cuándo lo supo AEGIS).",
    "token_profiles": "Holders, concentración, organic score, supply (Jupiter/GeckoTerminal).",
    "candles": "OHLCV por pool y timeframe (investigación histórica).",
    "trades": "Operaciones individuales (pendiente de ingesta continua).",
    "liquidity_events": "Cambios relevantes de liquidez (pendiente).",
    "wallets": "Direcciones observadas (grafo de relaciones; pendiente).",
    "wallet_relationships": "Relaciones INFERIDAS con nivel de confianza; nunca hechos confirmados.",
    "security_assessments": "Veredicto del motor de seguridad con hallazgos y caducidad.",
    "security_events": "Hallazgos críticos individuales (CONFIRMED/INDICATOR/UNKNOWN).",
    "token_states": "Historial de transiciones de la máquina de estados con motivo y evidencia.",
    "features": "Vectores de indicadores point-in-time (persistencia limitada, ver retención).",
    "model_versions": "Registro de modelos (heurísticos y entrenados) con estado champion/challenger.",
    "predictions": "Predicciones versionadas por modelo.",
    "explosion_events": "Episodios de explosión detectados (número de ola, precio base y pico).",
    "recovery_events": "Recuperaciones tras desplomes y su clasificación.",
    "decisions": "Toda decisión con acción, motivos, scores, hash de entradas y de estrategia.",
    "orders": "Órdenes simuladas con clave de idempotencia única y cotización usada.",
    "executions": "Fills simulados con descomposición completa de costes.",
    "positions": "Posiciones (libro operativo); como máximo una abierta por token (índice parcial único).",
    "position_lots": "Lotes de adquisición (coste incluye gastos de adquisición).",
    "lot_disposals": "Asignación FIFO de cada venta a lotes: coste, importe y ganancia.",
    "ledger_transactions": "Cabecera de transacción contable (append-only, idempotente).",
    "ledger_postings": "Apuntes de partida doble en EUR (debe +, haber −; cada transacción suma 0).",
    "contributions": "Aportaciones externas mensuales (una por periodo YYYY-MM).",
    "portfolio_snapshots": "Patrimonio liquidable, compartimentos, KPI base y resultado de conciliación por ciclo.",
    "shadow_positions": "Cartera sombra contrafactual (nunca forma parte del patrimonio).",
    "control_flags": "Kill switch, suspensión de compras, data_mode.",
    "scheduler_queue": "Cola de monitorización: próximo vencimiento, última observación, retraso y fallos consecutivos.",
    "source_health": "Salud por fuente de datos: llamadas, errores, 429, latencia.",
    "worker_heartbeats": "Latido del worker y resumen del último ciclo.",
    "data_quality_events": "Incidencias de calidad de datos.",
    "system_events": "Eventos de sistema (ciclos, fallos).",
    "risk_events": "Eventos de riesgo (kill switch, conciliación).",
    "configuration_versions": "Configuraciones (estrategia y límites) por hash de contenido.",
    "audit_logs": "Auditoría de acciones de control.",
    "backtest_runs": "Ejecuciones de backtest con parámetros y limitaciones declaradas.",
    "archive_batches": "Manifiesto del archivo Parquet: rango de ids, filas, SHA-256 canónico y verificación.",
    "backtest_results": "Métricas por estrategia y ejecución.",
}


def _replace(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    a, b = text.index(BEGIN) + len(BEGIN), text.index(END)
    path.write_text(text[:a] + "\n" + body.rstrip() + "\n" + text[b:], encoding="utf-8")


def features_md() -> str:
    lines = [f"Feature set `{FEATURE_SET_VERSION}` — {len(REGISTRY)} indicadores.", "",
             "| Nombre | Grupo | Unidad | Horizonte | Fuente | Definición | Validez |", "|---|---|---|---|---|---|---|"]
    for spec in sorted(REGISTRY.values(), key=lambda s: (s.group, s.name)):
        lines.append(f"| `{spec.name}` | {spec.group} | {spec.unit} | {spec.horizon} | {spec.source} | "
                     f"{spec.definition.replace('|', '/')} | {spec.validity} |")
    return "\n".join(lines)


def data_md() -> str:
    out = [f"{len(metadata.tables)} tablas (generado desde `aegis.storage.schema`).", ""]
    for name in sorted(metadata.tables):
        t = metadata.tables[name]
        out.append(f"### `{name}`\n\n{TABLE_NOTES.get(name, '')}\n")
        out.append("| Columna | Tipo | Nulo | Clave |\n|---|---|---|---|")
        for c in t.columns:
            key = "PK" if c.primary_key else ("FK→" + next(iter(c.foreign_keys)).target_fullname if c.foreign_keys else "")
            out.append(f"| `{c.name}` | {type(c.type).__name__} | {'sí' if c.nullable else 'no'} | {key} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    _replace(ROOT / "docs" / "FEATURE_CATALOG.md", features_md())
    _replace(ROOT / "docs" / "DATA_DICTIONARY.md", data_md())


if __name__ == "__main__":
    main()
