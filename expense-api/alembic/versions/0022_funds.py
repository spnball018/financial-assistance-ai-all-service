"""funds and fund_trades: mutual funds held in an investment account, and the user's buys, sells and
switches (units, amount, NAV), entered by hand. A fund account holds no cash: money moved into or out
of it stays a transfer in ledger.transactions, and each buy or sell points at the transfer that paid
for it (or received the money).

Revision ID: 0022
Revises: 0021
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ledger.funds (
        id          serial PRIMARY KEY,
        user_id     integer NOT NULL REFERENCES ledger.users(id),
        account_id  integer NOT NULL,
        code        text NOT NULL,
        name        text,
        nav         numeric(16,4) CHECK (nav > 0),
        nav_date    date,
        is_active   boolean NOT NULL DEFAULT true,
        created_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (id, user_id),
        CONSTRAINT funds_user_account_code_key UNIQUE (user_id, account_id, code),
        CHECK ((nav IS NULL) = (nav_date IS NULL)),
        FOREIGN KEY (account_id, user_id) REFERENCES ledger.accounts (id, user_id)
    )""")
    op.execute("""
    CREATE TABLE ledger.fund_trades (
        id              bigserial PRIMARY KEY,
        user_id         integer NOT NULL REFERENCES ledger.users(id),
        fund_id         integer NOT NULL,
        side            text NOT NULL CHECK (side IN ('buy','sell')),
        trade_date      date NOT NULL,
        units           numeric(20,4) NOT NULL CHECK (units > 0),
        amount          numeric(15,2) NOT NULL CHECK (amount > 0),
        nav             numeric(16,4) CHECK (nav > 0),
        -- new_transfer: the transfer was created with the trade (and is deleted with it)
        -- linked_transfer: an existing transfer, e.g. from a KBank email
        -- held_before: units held before the user started the ledger (buy only, no money moved)
        -- switch: one side of a switch between two funds (no money moved)
        funding         text NOT NULL CHECK (funding IN ('new_transfer','linked_transfer','held_before','switch')),
        transaction_id  bigint,
        switched_from   bigint,   -- the switch-in buy points at its switch-out sell
        note            text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        UNIQUE (id, user_id),
        CHECK (funding <> 'held_before' OR side = 'buy'),
        CHECK ((switched_from IS NOT NULL) = (funding = 'switch' AND side = 'buy')),
        FOREIGN KEY (fund_id, user_id) REFERENCES ledger.funds (id, user_id),
        FOREIGN KEY (transaction_id, user_id) REFERENCES ledger.transactions (id, user_id)
            ON DELETE SET NULL (transaction_id),
        FOREIGN KEY (switched_from, user_id) REFERENCES ledger.fund_trades (id, user_id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX fund_trades_fund_idx ON ledger.fund_trades (fund_id, trade_date)")
    # one transfer pays for one trade
    op.execute("CREATE UNIQUE INDEX fund_trades_transaction_key ON ledger.fund_trades (transaction_id) "
               "WHERE transaction_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.fund_trades")
    op.execute("DROP TABLE ledger.funds")
