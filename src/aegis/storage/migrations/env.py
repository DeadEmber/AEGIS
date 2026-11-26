"""Alembic environment. Uses a connection passed via config.attributes (see aegis.storage.db.migrate)
or AEGIS_DATABASE_URL when invoked from the alembic CLI."""

from alembic import context

from aegis.storage.schema import metadata

config = context.config
target_metadata = metadata


def run() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        from aegis.config import Settings
        from aegis.storage.db import make_engine

        engine = make_engine(Settings().database_url)
        with engine.begin() as conn:
            context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
            context.run_migrations()
        return
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    context.run_migrations()


run()
