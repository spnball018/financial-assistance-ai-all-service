"""MCP connector sign-in: OAuth clients (claude.ai registers itself), one-time authorization codes and
the access/refresh tokens they turn into. These tokens only open /mcp (read-only tools), never the REST API;
only sha256 of codes and tokens is stored, like api_tokens.

Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ledger.oauth_clients (
        client_id   text PRIMARY KEY,
        info        jsonb NOT NULL,          -- the registered client metadata (redirect_uris, name, ...)
        created_at  timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""
    CREATE TABLE ledger.oauth_codes (
        code_hash   text PRIMARY KEY,
        client_id   text NOT NULL REFERENCES ledger.oauth_clients(client_id) ON DELETE CASCADE,
        user_id     integer NOT NULL REFERENCES ledger.users(id),
        data        jsonb NOT NULL,          -- redirect_uri, code_challenge, scopes, resource
        expires_at  timestamptz NOT NULL
    )""")
    op.execute("""
    CREATE TABLE ledger.oauth_tokens (
        id            bigserial PRIMARY KEY,
        token_hash    text NOT NULL UNIQUE,
        kind          text NOT NULL CHECK (kind IN ('access', 'refresh')),
        grant_id      text NOT NULL,         -- one approval; refreshing keeps it, revoking ends all its tokens
        client_id     text NOT NULL REFERENCES ledger.oauth_clients(client_id) ON DELETE CASCADE,
        user_id       integer NOT NULL REFERENCES ledger.users(id),
        scopes        text[] NOT NULL,
        resource      text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        expires_at    timestamptz NOT NULL,
        last_used_at  timestamptz,
        revoked_at    timestamptz
    )""")
    op.execute("CREATE INDEX oauth_tokens_user ON ledger.oauth_tokens (user_id, grant_id)")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.oauth_tokens")
    op.execute("DROP TABLE ledger.oauth_codes")
    op.execute("DROP TABLE ledger.oauth_clients")
