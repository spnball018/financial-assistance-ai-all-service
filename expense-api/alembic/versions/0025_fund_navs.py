"""fund_navs: daily NAV history of every fund code someone holds, fetched from Finnomena every day
(funds/history.py) for charts and analysis. Public market data, shared by all users like categories:
no user_id. Codes are stored upper-case.

Revision ID: 0025
Revises: 0024
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ledger.fund_navs (
        code        text NOT NULL CHECK (code = upper(code)),
        nav_date    date NOT NULL,
        nav         numeric(16,4) NOT NULL CHECK (nav > 0),
        fetched_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (code, nav_date)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.fund_navs")
