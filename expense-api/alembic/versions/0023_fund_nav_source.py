"""funds.nav_source: where the fund's latest NAV came from, 'user' (typed in) or 'finnomena' (fetched from
Finnomena's public fund API by the fund's code). Set exactly when nav is.

Revision ID: 0023
Revises: 0022
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.funds ADD COLUMN nav_source text CHECK (nav_source IN ('user', 'finnomena'))")
    op.execute("UPDATE ledger.funds SET nav_source = 'user' WHERE nav IS NOT NULL")
    op.execute("ALTER TABLE ledger.funds ADD CONSTRAINT funds_nav_source_with_nav CHECK ((nav IS NULL) = (nav_source IS NULL))")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.funds DROP COLUMN nav_source")
