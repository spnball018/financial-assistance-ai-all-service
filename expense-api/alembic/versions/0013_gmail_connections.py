"""gmail connections (P7.3): each user's own Gmail, refresh token Fernet-encrypted with SECRET_KEY

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ledger.gmail_connections (
            user_id                  integer PRIMARY KEY REFERENCES ledger.users(id) ON DELETE CASCADE,
            gmail_address            text NOT NULL,
            refresh_token_encrypted  text NOT NULL,
            scopes                   text NOT NULL,
            connected_at             timestamptz NOT NULL DEFAULT now(),
            last_used_at             timestamptz,
            last_error               text
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.gmail_connections")
