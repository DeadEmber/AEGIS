"""Research archive: append-only tables exported to compressed Parquet (zstd) and queried with DuckDB.

Separation of storage tiers (audit 2026-11-26):
- operational DB (SQLite/PostgreSQL): recent data the worker and API need; subject to retention;
- research archive (Parquet files under AEGIS_ARCHIVE_DIR): every row of the research tables at full
  resolution, with timestamps, provenance (source, quality flags), feature-set versions and the
  information available at decision time (decisions.scores/reasons/inputs_hash).

Integrity: each batch is written to a temp file, renamed atomically, re-read and compared row-by-row
through a canonical SHA-256 of the content. Only `verified` batches allow deletion, and
`retention.prune` additionally checks that every row it deletes is present in the archived file.
No extra infrastructure: plain files (a persistent volume); DuckDB reads them in place.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import JSON, BigInteger, Boolean, Integer, and_, func, insert, select
from sqlalchemy.engine import Connection

from aegis.core.clock import UTC, ensure_utc
from aegis.storage import schema as s

SCHEMA_VERSION = "archive-1"
# append-only research tables -> timestamp column used for cut-off and partitioning
ARCHIVE_TABLES: dict[str, str] = {
    "market_snapshots": "observed_at",
    "features": "as_of",
    "decisions": "decided_at",
    "token_profiles": "observed_at",
    "token_states": "valid_from",
    "security_assessments": "assessed_at",
    "exchange_rates": "fetched_at",
    "system_events": "occurred_at",
    "data_quality_events": "occurred_at",
}


def _pa_type(col: Any) -> pa.DataType:
    t = col.type
    if isinstance(t, s.Dec):
        return pa.string()  # exact decimal as canonical string (prices down to 1e-12 and below)
    if isinstance(t, s.UtcDateTime):
        return pa.timestamp("us", tz="UTC")
    if isinstance(t, Boolean):
        return pa.bool_()
    if isinstance(t, Integer | BigInteger):
        return pa.int64()
    if isinstance(t, JSON):
        return pa.string()
    return pa.string()


def _cell(col: Any, v: Any) -> Any:
    if v is None:
        return None
    if isinstance(col.type, s.Dec):
        return format(Decimal(v), "f")
    if isinstance(col.type, JSON):
        return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(v, dt.datetime):
        return ensure_utc(v)
    return v


def _canonical_hash(columns: list[str], rows: Iterable[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([_canon(r[c]) for c in columns], separators=(",", ":"), default=str).encode())
        h.update(b"\n")
    return h.hexdigest()


def _canon(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return ensure_utc(v).isoformat()
    return v


@dataclass(frozen=True)
class BatchResult:
    table: str
    rows: int
    path: str | None
    status: str  # verified | empty | failed
    min_id: int | None = None
    max_id: int | None = None


class ArchiveError(RuntimeError):
    pass


def _last_archived_id(conn: Connection, table: str) -> int:
    v = conn.execute(select(func.max(s.archive_batches.c.max_id)).where(and_(
        s.archive_batches.c.table_name == table, s.archive_batches.c.status == "verified"))).scalar_one()
    return int(v or 0)


def archive_table(conn: Connection, root: Path, table: str, now: dt.datetime, *, lag: dt.timedelta = dt.timedelta(minutes=10),
                  batch_rows: int = 50_000, provenance: dict[str, str] | None = None) -> BatchResult:
    """Export the next batch of rows (id > last verified max_id, timestamp < now - lag)."""
    t = s.metadata.tables[table]
    ts_col = t.c[ARCHIVE_TABLES[table]]
    after = _last_archived_id(conn, table)
    rows = conn.execute(select(t).where(and_(t.c.id > after, ts_col < ensure_utc(now) - lag))
                        .order_by(t.c.id).limit(batch_rows)).all()
    if not rows:
        return BatchResult(table, 0, None, "empty")
    cols = list(t.columns)
    names = [c.name for c in cols]
    data = [{c.name: _cell(c, r._mapping[c.name]) for c in cols} for r in rows]
    digest = _canonical_hash(names, data)
    schema = pa.schema([pa.field(c.name, _pa_type(c)) for c in cols]).with_metadata({
        "aegis_table": table, "aegis_archive_schema": SCHEMA_VERSION,
        "exported_at": dt.datetime.now(UTC).isoformat(), "content_sha256": digest, **(provenance or {})})
    arrow = pa.Table.from_pylist(data, schema=schema)
    min_id, max_id = int(rows[0].id), int(rows[-1].id)
    min_ts = min(r._mapping[ts_col.name] for r in rows)
    max_ts = max(r._mapping[ts_col.name] for r in rows)
    rel = Path(table) / ensure_utc(min_ts).strftime("%Y-%m") / f"{table}_{min_id:012d}_{max_id:012d}.parquet"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(arrow, tmp, compression="zstd")
    os.replace(tmp, path)
    ok = verify_file(path, names, digest, len(rows))
    conn.execute(insert(s.archive_batches).values(
        table_name=table, path=str(rel), rows=len(rows), min_id=min_id, max_id=max_id, min_ts=min_ts, max_ts=max_ts,
        sha256=digest, schema_version=SCHEMA_VERSION, created_at=ensure_utc(now),
        verified_at=ensure_utc(now) if ok else None, status="verified" if ok else "failed"))
    if not ok:
        raise ArchiveError(f"archive verification failed for {path}")
    return BatchResult(table, len(rows), str(rel), "verified", min_id, max_id)


def verify_file(path: Path, names: list[str], digest: str, n_rows: int) -> bool:
    """Re-read the Parquet file and compare row count and canonical content hash."""
    tbl = pq.read_table(path)
    if tbl.num_rows != n_rows or tbl.column_names != names:
        return False
    return _canonical_hash(names, tbl.to_pylist()) == digest


def archive_all(conn: Connection, root: Path, now: dt.datetime, provenance: dict[str, str] | None = None,
                max_batches_per_table: int = 100) -> list[BatchResult]:
    out = []
    for table in ARCHIVE_TABLES:
        for _ in range(max_batches_per_table):
            res = archive_table(conn, root, table, now, provenance=provenance)
            out.append(res)
            if res.status != "verified":
                break
    return [r for r in out if r.status != "empty"]


def archived_id_ranges(conn: Connection, root: Path, table: str) -> list[tuple[int, int]]:
    """Verified id ranges whose current DB rows are ALL present in the archived file.

    Guards against a row inserted into a range after it was archived (late commit): that range is not
    returned, so nothing in it can be pruned until it is reconciled."""
    t = s.metadata.tables[table]
    ranges: list[tuple[int, int]] = []
    for b in conn.execute(select(s.archive_batches).where(and_(
            s.archive_batches.c.table_name == table, s.archive_batches.c.status == "verified"))
            .order_by(s.archive_batches.c.min_id)):
        path = root / b.path
        if not path.exists():
            continue
        names = [c.name for c in t.columns]
        try:
            if not verify_file(path, names, b.sha256, b.rows):  # integrity re-checked before any deletion
                continue
        except (OSError, pa.ArrowException):
            continue
        archived = set(pq.read_table(path, columns=["id"]).column("id").to_pylist())
        db_ids = {r.id for r in conn.execute(select(t.c.id).where(and_(t.c.id >= b.min_id, t.c.id <= b.max_id)))}
        if db_ids <= archived:
            ranges.append((int(b.min_id), int(b.max_id)))
    return ranges


def duckdb_connect(root: Path):  # type: ignore[no-untyped-def]
    """DuckDB session with one view per archived table (reads Parquet in place, no import)."""
    import duckdb

    con = duckdb.connect()
    for table in ARCHIVE_TABLES:
        files = list((root / table).rglob("*.parquet"))
        if files:
            pattern = str(root / table / "**" / "*.parquet")
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{pattern}', union_by_name=true)")
    return con
