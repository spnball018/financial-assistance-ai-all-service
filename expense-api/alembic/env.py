import os

from alembic import context
from sqlalchemy import create_engine, text

SCHEMA = "ledger"


def _url() -> str:
    url = os.environ["DATABASE_URL"]
    # compose passes a plain postgresql:// URL; use the psycopg (v3) driver
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations() -> None:
    engine = create_engine(_url())
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.commit()
        context.configure(
            connection=conn,
            target_metadata=None,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
