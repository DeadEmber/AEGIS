"""Dataset registry: pinned revisions, file hashes, licences and provenance.

Every dataset AEGIS learns from is identified by (repository, revision) and every file by its
SHA-256. `fetch` downloads only free, public files from the pinned revision; `verify` recomputes
the hashes; `write_provenance` records what was fetched, from where and when. Datasets whose licence
forbids redistribution (`redistributable=False`) must never be committed to the repository, not
even as test fixtures (enforced by `tests/unit/test_history.py`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

HF_BASE = "https://huggingface.co/datasets"


@dataclass(frozen=True)
class DatasetFile:
    path: str
    sha256: str
    bytes: int
    role: str  # observations | static | outcomes | events | docs
    pit: str  # contemporaneous | retrospective | mixed | docs
    optional: bool = False  # not fetched by default (size); fetch explicitly


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    title: str
    repo: str
    revision: str
    licence: str
    redistributable: bool
    citation: str
    period: tuple[str, str]
    files: tuple[DatasetFile, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def file(self, path: str) -> DatasetFile:
        return next(f for f in self.files if f.path == path)

    def root(self, base: Path) -> Path:
        return base / self.id / self.revision


PUMPFUN = DatasetSpec(
    id="pumpfun_corpus",
    title="PumpFun Launch-to-Graduation Corpus (Jun-Jul 2026)",
    repo="Slinky21/Pumpfun_Memecoin_Corpus",
    revision="13bec2bf3089d56d50c35e602616d1b993bb37f5",
    licence="MIT (repository tag) / CC BY 4.0 (README) — attribution required; redistribution allowed",
    redistributable=True,
    citation="Slink Dev (slink21taken). PumpFun Launch Corpus. 2026. huggingface.co/datasets/Slinky21/Pumpfun_Memecoin_Corpus",
    period=("2026-06-05", "2026-07-14"),
    files=(
        DatasetFile("README.md", "0cd64ffd8c20b6aee2ecb70c956d47fa7a24e786f44c7c84f85b4c4fffbc7639", 8253, "docs", "docs"),
        DatasetFile("KNOWN_ISSUES.md", "82534ee226e1447e5ab0c0eb4b1b5453f0241aa5426d561b8f4b00314d1da299", 14974, "docs", "docs"),
        DatasetFile("quickstart.py", "68e6584536a9051e49a289b06b74a811546a62ccdb42a3b393409c2cc0e5e30b", 24188, "docs", "docs"),
        DatasetFile("migrations.parquet", "ef5d5141fd94acbcd121bed50e39e525cf7777d25338fc9852a67fd8a085105d", 480591,
                    "events", "contemporaneous"),
        DatasetFile("postgard_snapshots.parquet", "34a63b8333a41b3cc84d05461febe725cf37842fa0e21d6ea00472dcdcfa1e72",
                    89336038, "observations", "contemporaneous"),
        DatasetFile("postgard_outcomes.parquet", "00a9980cb97630e9ea89574742f0e134d448e5566c5d7bda461f59bb02de022b",
                    984653, "outcomes", "retrospective"),
        DatasetFile("tokens.parquet", "c005d86d424013e5c78701161b025f3d8c3d472afb61466e0ad6fd5afe9e8ea6", 214405469,
                    "static", "mixed"),
        DatasetFile("snapshots.parquet", "41b6a221dca6ea01d68fac2129c3c5cd3966a27839f267e0f6aa4e980b6fc7d8", 570740794,
                    "observations", "contemporaneous", optional=True),
    ),
    notes=(
        "post-graduation snapshots are DexScreener-style pair polls: ~60 s for 0-2 h, ~5 min 2-6 h, ~15 min 6-24 h, "
        "~30 min 24-48 h after graduation; tracking ends at 48 h (positions beyond are censored)",
        "dex_id 'pumpfun' rows after graduation describe the completed bonding curve (no liquidity): not tradable",
        "migrations.pool_address holds sentinels for ~25% of rows (synthetic_graduation_queue, "
        "backfilled_from_pumpswap_trade): never used as a real pool address",
        "tokens.parquet: use *_corrected concentration fields; exclude top10_pct_suspect rows from concentration "
        "features; graduated_at / peak_* / trade_count / entry_price_* are retrospective",
        "regime breaks: Jun 9-10 holder cap, Jun 20-22 mayhem rollout, Jun 23-24 concentration bug, Jun 24-25 silence "
        "drop, Jul 3 collection outage (zero trades) and Jul 4+ curve overshoot",
        "trades/*.parquet (5.6 GB, 18 shards) are not fetched by default",
    ),
)

LAUNCHTAPE = DatasetSpec(
    id="launchtape",
    title="Solana Launch Tape — free 2-hour sample",
    repo="launchtape/solana-launch-tape",
    revision="791aeee832cb9a10f55361f10972b33272c857f8",
    licence="Launch Tape data licence: personal/research use inside the team; NO redistribution in whole or in part",
    redistributable=False,
    citation="Solana Launch Tape (launchtape.io), free sample, huggingface.co/datasets/launchtape/solana-launch-tape",
    period=("2026-10-10", "2026-10-14"),
    files=(
        DatasetFile("README.md", "af86d11e532e42dd66fe7c684074b4b33aaac9f62557ef66323576fa20f62996", 15283, "docs", "docs"),
        DatasetFile("data/MANIFEST.json", "bc1fa1de8a7028461c4b7a767823747702c3c9505fca980d7407f6fe3cd52d65", 6319,
                    "docs", "docs"),
        DatasetFile("data/SHA256SUMS", "e82ea4fbab96b8dcb594c6260ade5ca9bebdd52bb160b96196de7a49b14e3b18", 1527,
                    "docs", "docs"),
        DatasetFile("data/gradtrades-2026-10-10-1400-1600utc.parquet",
                    "747d276e7e18c0d924e0915a8a244ef7b859c1ad13695ef995fa409c5dda5a16", 51640135, "observations",
                    "contemporaneous"),
        DatasetFile("data/pregrad-2026-10-10-1400-1600utc.parquet",
                    "38d7d8d6bf78153e9603cd897fbaac3353e9ba602edaf0f3007cdbb6519445a1", 1850094, "mixed", "mixed"),
        DatasetFile("data/padfeed-2026-10-10-1400-1600utc.parquet",
                    "e1a6ca7906f45d48d566c99e5e42277f38bb3a69ac820b5b9d935cacacd7865b", 267744, "observations",
                    "contemporaneous"),
        DatasetFile("data/meteora-pools-2026-10-10-1400-1600utc.parquet",
                    "29ea4ead73115d2be6f1b779cd5261963c6ee79e6f007f37fe74d76be71ce4e5", 279306, "observations",
                    "contemporaneous"),
        DatasetFile("data/curvetape-2026-10-13-1400-1600utc.parquet",
                    "097f2425f9a6953736957cf0dfa9d09f872dd7757aca36cc2dd193e0cf94351a", 58028330, "observations",
                    "contemporaneous"),
        DatasetFile("data/launchmeta-2026-10-13-1400-1600utc.parquet",
                    "a19ac4fc6afeee1047b2568beeda9f62e16228f5afd97ac3ed796495c6704208", 1251139, "static",
                    "contemporaneous"),
        DatasetFile("data/gradholders-2026-10-14-0400-0600utc.parquet",
                    "e8396eb78568287fe64774601cfb1d8ec1084f1811e5a4111162ead9de4be496", 296075, "observations",
                    "contemporaneous"),
    ),
    notes=(
        "gradtrades: every PumpSwap trade in the first 30 min after each graduation of 2026-10-10 14:00-16:00 UTC, with "
        "pool reserves (poolBase, poolQuote, virtualQuote) and fees (lp, protocol, creator)",
        "pregrad outcome rows (capAt, maxGain, minDrop, crashed, dump) are retrospective (+10 min) and never decision inputs",
        "feed gaps are rows (down/up/resume/end with why): kept as gaps, never filled",
        "full days are paid (launchtape.io): NOT purchased (no paid services without owner approval)",
    ),
)

REGISTRY: dict[str, DatasetSpec] = {d.id: d for d in (PUMPFUN, LAUNCHTAPE)}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def verify(spec: DatasetSpec, base: Path, *, include_optional: bool = False) -> dict[str, dict[str, object]]:
    """Recompute hashes. Status per file: ok | missing | size_mismatch | hash_mismatch."""
    out: dict[str, dict[str, object]] = {}
    root = spec.root(base)
    for f in spec.files:
        p = root / f.path
        if not p.exists():
            out[f.path] = {"status": "missing" if not f.optional or include_optional else "optional_missing"}
            continue
        size = p.stat().st_size
        if size != f.bytes:
            out[f.path] = {"status": "size_mismatch", "bytes": size, "expected": f.bytes}
            continue
        digest = sha256_file(p)
        out[f.path] = {"status": "ok" if digest == f.sha256 else "hash_mismatch", "sha256": digest}
    return out


def is_complete(report: dict[str, dict[str, object]]) -> bool:
    return all(v["status"] in ("ok", "optional_missing") for v in report.values())


def fetch(spec: DatasetSpec, base: Path, *, include_optional: bool = False, timeout_s: float = 900.0,
          client: httpx.Client | None = None) -> dict[str, str]:
    """Download the pinned revision's files (free, public). Existing verified files are kept.

    Files are written to `<base>/<id>/<revision>/<path>` via a `.part` file and only renamed after the
    hash matches the registry — a corrupted or changed upstream file is never accepted."""
    root = spec.root(base)
    result: dict[str, str] = {}
    own = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=timeout_s)
    try:
        for f in spec.files:
            if f.optional and not include_optional:
                result[f.path] = "skipped_optional"
                continue
            dest = root / f.path
            if dest.exists() and dest.stat().st_size == f.bytes and sha256_file(dest) == f.sha256:
                result[f.path] = "present"
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            url = f"{HF_BASE}/{spec.repo}/resolve/{spec.revision}/{f.path}"
            with http.stream("GET", url) as r:
                r.raise_for_status()
                with part.open("wb") as fh:
                    for block in r.iter_bytes(1 << 20):
                        fh.write(block)
            digest = sha256_file(part)
            if digest != f.sha256:
                part.unlink()
                raise ValueError(f"{spec.id}/{f.path}: sha256 {digest} != pinned {f.sha256}")
            part.replace(dest)
            result[f.path] = "fetched"
    finally:
        if own:
            http.close()
    write_provenance(spec, base, result)
    return result


def write_provenance(spec: DatasetSpec, base: Path, fetch_result: dict[str, str] | None = None) -> Path:
    root = spec.root(base)
    root.mkdir(parents=True, exist_ok=True)
    doc = {
        "dataset": asdict(spec),
        "source_url": f"{HF_BASE}/{spec.repo}/tree/{spec.revision}",
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "fetch": fetch_result or {},
        "verify": verify(spec, base),
    }
    path = root / "AEGIS_PROVENANCE.json"
    path.write_text(json.dumps(doc, indent=1, default=str))
    return path
