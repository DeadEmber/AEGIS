"""Research archive: append-only tables exported to compressed Parquet (zstd) and queried with DuckDB.

Storage tiers (audits 2026-11-26):
- operational DB (SQLite/PostgreSQL): recent data for the worker and API; subject to retention;
- research archive (Parquet under AEGIS_ARCHIVE_DIR): every row of the research tables at full
  resolution, with timestamps, provenance, feature-set versions and the information available at
  decision time, plus the append-only FINANCIAL tables (ledger, orders, executions, disposals,
  contributions) as an immutable backup of the audit trail.

Completeness (second audit): incremental batches take only the CONTIGUOUS id prefix whose rows are
eligible, so a row with a lower id and a newer timestamp can no longer be skipped; and `reconcile`
archives any committed row that is not covered by a verified batch (late commits on PostgreSQL, rows
skipped by the old max_id logic). Coverage is by ID SET, not by range.

Integrity: each batch is written to a temp file, renamed atomically, re-read and compared through a
canonical SHA-256 of its content. `manifest.json` at the archive root lists every verified batch, so a
backup can be verified and restored without the database. Deletion (retention.prune) requires the row
id to be present in a batch whose file passes the integrity check.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import JSON, BigInteger, Boolean, Integer, and_, insert, select
from sqlalchemy.engine import Connection

from aegis.core.clock import UTC, ensure_utc
from aegis.storage import schema as s

SCHEMA_VERSION = "archive-1"
MANIFEST = "manifest.json"
FUTURE_TOLERANCE = dt.timedelta(days=1)

# append-only tables -> timestamp column used for the eligibility cut-off (None: committed rows are final)
RESEARCH_TABLES: dict[str, str | None] = {
    "market_snapshots": "observed_at",
    "features": "as_of",
    "decisions": "decided_at",
    "token_profiles": "observed_at",
    "token_states": "valid_from",
    "security_assessments": "assessed_at",
    "security_events": "occurred_at",
    "exchange_rates": "fetched_at",
    "system_events": "occurred_at",
    "data_quality_events": "occurred_at",
    "audit_logs": "occurred_at",
}
FINANCIAL_TABLES: dict[str, str | None] = {  # archived as backup; NEVER pruned
    "contributions": "occurred_at",
    "ledger_transactions": "recorded_at",
    "ledger_postings": None,
    "orders": "requested_at",
    "executions": "executed_at",
    "lot_disposals": "disposed_at",
}
ARCHIVE_TABLES: dict[str, str | None] = {**RESEARCH_TABLES, **FINANCIAL_TABLES}


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


def _canon(v: Any) -> Any:
    return ensure_utc(v).isoformat() if isinstance(v, dt.datetime) else v


def _canonical_hash(columns: list[str], rows: Iterable[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([_canon(r[c]) for c in columns], separators=(",", ":"), default=str).encode())
        h.update(b"\n")
    return h.hexdigest()


@dataclass(frozen=True)
class BatchResult:
    table: str
    rows: int
    path: str | None
    status: str  # verified | empty | failed
    min_id: int | None = None
    max_id: int | None = None
    kind: str = "incremental"  # incremental | reconcile


class ArchiveError(RuntimeError):
    pass


def _last_archived_id(conn: Connection, table: str) -> int:
    v = conn.execute(select(s.archive_batches.c.max_id).where(and_(
        s.archive_batches.c.table_name == table, s.archive_batches.c.status == "verified"))
        .order_by(s.archive_batches.c.max_id.desc()).limit(1)).scalar()
    return int(v or 0)


def _write_batch(conn: Connection, root: Path, table: str, rows: list[Any], now: dt.datetime, kind: str,
                 provenance: dict[str, str] | None) -> BatchResult:
    t = s.metadata.tables[table]
    cols = list(t.columns)
    names = [c.name for c in cols]
    data = [{c.name: _cell(c, r._mapping[c.name]) for c in cols} for r in rows]
    digest = _canonical_hash(names, data)
    schema = pa.schema([pa.field(c.name, _pa_type(c)) for c in cols]).with_metadata({
        "aegis_table": table, "aegis_archive_schema": SCHEMA_VERSION, "aegis_batch_kind": kind,
        "exported_at": dt.datetime.now(UTC).isoformat(), "content_sha256": digest, **(provenance or {})})
    arrow = pa.Table.from_pylist(data, schema=schema)
    min_id, max_id = int(rows[0].id), int(rows[-1].id)
    ts_name = ARCHIVE_TABLES[table]
    stamps = [r._mapping[ts_name] for r in rows if ts_name and r._mapping[ts_name] is not None]
    min_ts, max_ts = (min(stamps), max(stamps)) if stamps else (None, None)
    month = ensure_utc(min_ts).strftime("%Y-%m") if min_ts else "no-ts"
    suffix = "" if kind == "incremental" else f"_{kind}"
    rel = Path(table) / month / f"{table}_{min_id:012d}_{max_id:012d}{suffix}.parquet"
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
    return BatchResult(table, len(rows), str(rel), "verified", min_id, max_id, kind)


def _eligible(row: Any, ts_name: str | None, cutoff: dt.datetime, now: dt.datetime) -> bool:
    if ts_name is None:
        return True
    ts = row._mapping[ts_name]
    if ts is None:
        return True
    # a timestamp far in the future is a data anomaly: archive it rather than block the table forever
    return ts < cutoff or ts > now + FUTURE_TOLERANCE


def archive_table(conn: Connection, root: Path, table: str, now: dt.datetime, *,
                  lag: dt.timedelta = dt.timedelta(minutes=10), batch_rows: int = 50_000,
                  provenance: dict[str, str] | None = None) -> BatchResult:
    """Export the next batch: the CONTIGUOUS prefix (by id) of rows after the last archived id whose
    timestamps are older than `now - lag`. Stopping at the first ineligible row guarantees that no lower
    id is ever skipped (the max_id watermark only advances over archived rows)."""
    t = s.metadata.tables[table]
    ts_name = ARCHIVE_TABLES[table]
    now = ensure_utc(now)
    cutoff = now - lag
    after = _last_archived_id(conn, table)
    rows = conn.execute(select(t).where(t.c.id > after).order_by(t.c.id).limit(batch_rows)).all()
    prefix = []
    for r in rows:
        if not _eligible(r, ts_name, cutoff, now):
            break
        prefix.append(r)
    if not prefix:
        return BatchResult(table, 0, None, "empty")
    return _write_batch(conn, root, table, prefix, now, "incremental", provenance)


def archived_ids(conn: Connection, root: Path, table: str, *, check_integrity: bool = True) -> set[int]:
    """Ids present in verified batches whose files exist (and pass the integrity check)."""
    t = s.metadata.tables[table]
    names = [c.name for c in t.columns]
    out: set[int] = set()
    for b in conn.execute(select(s.archive_batches).where(and_(
            s.archive_batches.c.table_name == table, s.archive_batches.c.status == "verified"))):
        path = root / b.path
        if not path.exists():
            continue
        try:
            if check_integrity and not verify_file(path, names, b.sha256, b.rows):
                continue
            out.update(pq.read_table(path, columns=["id"]).column("id").to_pylist())
        except (OSError, pa.ArrowException):
            continue
    return out


def reconcile_table(conn: Connection, root: Path, table: str, now: dt.datetime, *,
                    lag: dt.timedelta = dt.timedelta(minutes=10), provenance: dict[str, str] | None = None,
                    batch_rows: int = 50_000) -> BatchResult:
    """Archive committed rows at or below the watermark that no verified batch contains (late commits,
    rows skipped by the pre-0.2.3 cut-off logic, batches whose file was lost)."""
    t = s.metadata.tables[table]
    ts_name = ARCHIVE_TABLES[table]
    now = ensure_utc(now)
    watermark = _last_archived_id(conn, table)
    if watermark == 0:
        return BatchResult(table, 0, None, "empty", kind="reconcile")
    covered = archived_ids(conn, root, table, check_integrity=False)
    orphans = [r for r in conn.execute(select(t).where(t.c.id <= watermark).order_by(t.c.id))
               if r.id not in covered and _eligible(r, ts_name, now - lag, now)][:batch_rows]
    if not orphans:
        return BatchResult(table, 0, None, "empty", kind="reconcile")
    res = _write_batch(conn, root, table, orphans, now, "reconcile", provenance)
    conn.execute(insert(s.data_quality_events).values(
        occurred_at=now, source="archive", kind="archive_orphans_reconciled", severity="warn", token_id=None,
        details={"table": table, "rows": len(orphans), "ids_sample": [int(r.id) for r in orphans[:20]]}))
    return res


def archive_all(conn: Connection, root: Path, now: dt.datetime, provenance: dict[str, str] | None = None,
                max_batches_per_table: int = 100, reconcile: bool = True) -> list[BatchResult]:
    out: list[BatchResult] = []
    for table in ARCHIVE_TABLES:
        for _ in range(max_batches_per_table):
            res = archive_table(conn, root, table, now, provenance=provenance)
            if res.status != "verified":
                break
            out.append(res)
        if reconcile:
            rec = reconcile_table(conn, root, table, now, provenance=provenance)
            if rec.status == "verified":
                out.append(rec)
    write_manifest(conn, root)
    return out


def verify_file(path: Path, names: list[str], digest: str, n_rows: int) -> bool:
    """Re-read the Parquet file and compare row count, columns and canonical content hash."""
    tbl = pq.read_table(path)
    if tbl.num_rows != n_rows or tbl.column_names != names:
        return False
    return _canonical_hash(names, tbl.to_pylist()) == digest


# ----------------------------------------------------------------------------- manifest, backup, restore
def write_manifest(conn: Connection, root: Path) -> Path:
    batches = [{"table": b.table_name, "path": b.path, "rows": b.rows, "min_id": b.min_id, "max_id": b.max_id,
                "sha256": b.sha256, "schema_version": b.schema_version, "created_at": b.created_at.isoformat()}
               for b in conn.execute(select(s.archive_batches).where(s.archive_batches.c.status == "verified")
                                     .order_by(s.archive_batches.c.id))]
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps({"format": SCHEMA_VERSION, "written_at": dt.datetime.now(UTC).isoformat(),
                               "batches": batches}, indent=1), encoding="utf-8")
    os.replace(tmp, root / MANIFEST)
    return root / MANIFEST


def read_manifest(root: Path) -> list[dict[str, Any]]:
    p = root / MANIFEST
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["batches"]


def _file_ok(root: Path, b: dict[str, Any]) -> bool:
    path = root / b["path"]
    if not path.exists():
        return False
    try:
        names = [c.name for c in s.metadata.tables[b["table"]].columns]
        return verify_file(path, names, b["sha256"], b["rows"])
    except (OSError, pa.ArrowException, KeyError):
        return False


@dataclass
class VerifyReport:
    ok: int = 0
    missing: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.missing and not self.corrupt


def verify_archive(root: Path) -> VerifyReport:
    """Verify every batch listed in root/manifest.json (database not required)."""
    rep = VerifyReport()
    for b in read_manifest(root):
        if not (root / b["path"]).exists():
            rep.missing.append(b["path"])
        elif _file_ok(root, b):
            rep.ok += 1
        else:
            rep.corrupt.append(b["path"])
    return rep


def backup(root: Path, dest: Path) -> dict[str, int]:
    """Incremental file-level backup: copy batches missing or invalid in `dest`, verify each copy, then
    write dest/manifest.json last. Never deletes anything in `dest`."""
    copied = skipped = failed = 0
    batches = read_manifest(root)
    for b in batches:
        if _file_ok(dest, b):
            skipped += 1
            continue
        if not _file_ok(root, b):
            failed += 1  # source itself invalid: never propagate a corrupt file
            continue
        target = dest / b["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".parquet.tmp")
        shutil.copy2(root / b["path"], tmp)
        os.replace(tmp, target)
        if _file_ok(dest, b):
            copied += 1
        else:
            failed += 1
    if failed == 0:
        shutil.copy2(root / MANIFEST, dest / MANIFEST)
    return {"copied": copied, "already_present": skipped, "failed": failed, "batches": len(batches)}


def restore(backup_root: Path, root: Path) -> dict[str, int]:
    """Restore batches that are missing or corrupt in `root` from a verified backup. A batch that is valid
    in `root` is never overwritten."""
    restored = kept = unavailable = 0
    for b in read_manifest(backup_root):
        if _file_ok(root, b):
            kept += 1
            continue
        if not _file_ok(backup_root, b):
            unavailable += 1
            continue
        target = root / b["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_root / b["path"], target)
        restored += 1 if _file_ok(root, b) else 0
    if not (root / MANIFEST).exists() and (backup_root / MANIFEST).exists():
        shutil.copy2(backup_root / MANIFEST, root / MANIFEST)
    return {"restored": restored, "kept_valid": kept, "unavailable": unavailable}


def import_manifest(conn: Connection, root: Path, now: dt.datetime) -> int:
    """After losing the operational DB: re-register batches from manifest.json (verified files only)."""
    known = {(r.table_name, r.min_id, r.max_id) for r in conn.execute(
        select(s.archive_batches.c.table_name, s.archive_batches.c.min_id, s.archive_batches.c.max_id))}
    n = 0
    for b in read_manifest(root):
        if (b["table"], b["min_id"], b["max_id"]) in known or not _file_ok(root, b):
            continue
        conn.execute(insert(s.archive_batches).values(
            table_name=b["table"], path=b["path"], rows=b["rows"], min_id=b["min_id"], max_id=b["max_id"],
            sha256=b["sha256"], schema_version=b["schema_version"], created_at=ensure_utc(now),
            verified_at=ensure_utc(now), status="verified"))
        n += 1
    return n


def duckdb_connect(root: Path):  # type: ignore[no-untyped-def]
    """DuckDB session with one view per archived table (reads Parquet in place, no import).

    Rows can appear in an incremental and a reconcile batch only if a file was restored twice; the
    views deduplicate on id to keep counts exact."""
    import duckdb

    con = duckdb.connect()
    for table in ARCHIVE_TABLES:
        if list((root / table).rglob("*.parquet")):
            pattern = str(root / table / "**" / "*.parquet")
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{pattern}', union_by_name=true) "
                        f"QUALIFY row_number() OVER (PARTITION BY id ORDER BY id) = 1")
    return con
