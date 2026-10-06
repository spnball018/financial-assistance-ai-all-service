"""auth: users (admin | member), Google sign-in sessions, API tokens (secrets stored as sha256)

User 1 is the owner of all existing data. It takes the first address in env ADMIN_EMAILS, or the
placeholder owner@localhost, which the first ADMIN_EMAILS address to sign in claims (app/auth.py).

Revision ID: 0011
Revises: 0010
"""
import os

from alembic import op
from sqlalchemy import text

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

PLACEHOLDER_OWNER = "owner@localhost"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ledger.users (
            id             serial PRIMARY KEY,
            email          text NOT NULL UNIQUE CHECK (email = lower(email)),
            name           text,
            role           text NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
            is_active      boolean NOT NULL DEFAULT true,
            created_at     timestamptz NOT NULL DEFAULT now(),
            last_login_at  timestamptz
        )""")
    admins = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    op.get_bind().execute(text("INSERT INTO ledger.users (email, role) VALUES (:e, 'admin')"),
                          {"e": admins[0] if admins else PLACEHOLDER_OWNER})
    op.execute("""
        CREATE TABLE ledger.auth_sessions (
            id            bigserial PRIMARY KEY,
            user_id       integer NOT NULL REFERENCES ledger.users(id) ON DELETE CASCADE,
            token_hash    text NOT NULL UNIQUE,
            user_agent    text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at  timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL
        )""")
    op.execute("""
        CREATE TABLE ledger.api_tokens (
            id            serial PRIMARY KEY,
            user_id       integer NOT NULL REFERENCES ledger.users(id) ON DELETE CASCADE,
            name          text NOT NULL,
            token_prefix  text NOT NULL,
            token_hash    text NOT NULL UNIQUE,
            created_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz,
            last_used_at  timestamptz,
            revoked_at    timestamptz
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.api_tokens")
    op.execute("DROP TABLE ledger.auth_sessions")
    op.execute("DROP TABLE ledger.users")
