"""Alembic migrations reproduce the SQLAlchemy metadata exactly (no drift) on SQLite."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from aegis.storage.db import make_engine, migrate
from aegis.storage.schema import metadata


def test_migrations_match_metadata(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path}/m.sqlite")
    migrate(eng)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), metadata)
    assert diff == [], diff
