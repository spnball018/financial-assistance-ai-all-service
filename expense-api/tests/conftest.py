import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def signed_in():
    """API tests act as a signed-in user; tests/test_auth.py removes this to test sign-in itself."""
    from app.auth import Principal, require_user
    from app.main import app

    app.dependency_overrides[require_user] = lambda: Principal(user_id=1, email="test@example.com", role="admin", via="session")
    yield
    app.dependency_overrides.pop(require_user, None)


@pytest.fixture(scope="session")
def db_engine():
    """Engine on a throwaway database. Set TEST_DATABASE_URL (database name must contain 'test'):
        postgresql://postgres:postgres@localhost:5432/financial_test
    Never point this at the real `financial` database: tests truncate ledger tables."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if "test" not in url.rsplit("/", 1)[-1]:
        pytest.fail("TEST_DATABASE_URL must point at a database whose name contains 'test'")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   env={**os.environ, "DATABASE_URL": url}, capture_output=True)
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE ledger.transactions, ledger.raw_emails, ledger.sync_runs "
                          "RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE ledger.email_sources SET is_active = (source = 'kbank_email'), "
                          "gmail_label = CASE source WHEN 'kbank_email' THEN 'financial-kasikorn' END"))
    return db_engine
