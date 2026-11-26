"""Database engine creation and bootstrap."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

from aegis.storage import schema

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(url: str, *, echo: bool = False) -> Engine:
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:"):
            eng = create_engine(
                "sqlite://", echo=echo, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            path = url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            eng = create_engine(url, echo=echo, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if url not in ("sqlite://", "sqlite:///:memory:"):
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return eng
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url, echo=echo, pool_pre_ping=True)


def create_all(engine: Engine) -> None:
    """Create schema directly (tests / throwaway DBs). Persistent DBs use `migrate()`."""
    schema.metadata.create_all(engine)
    with engine.begin() as conn:
        seed(conn)


def migrate(engine: Engine, revision: str = "head") -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)
    with engine.begin() as conn:
        seed(conn)


def seed(conn: Connection) -> None:
    if conn.execute(select(schema.networks.c.id).where(schema.networks.c.id == "solana")).first() is None:
        conn.execute(insert(schema.networks).values(id="solana", name="Solana", native_symbol="SOL", enabled=True))
