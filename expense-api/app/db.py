import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def get_engine() -> Engine:
    """Lazily built so importing the app (and its tests) needs no database."""
    global _engine
    if _engine is None:
        # compose passes a plain postgresql:// URL; use the psycopg (v3) driver
        url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine
