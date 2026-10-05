"""Review queue operations on ledger.transactions: list as trees, edit, confirm, manual entry."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from categorize import memory
from categorize.normalize import normalize_merchant

COLUMNS = """t.id, t.parent_id, t.account_id, t.counter_account_id, t.node_type, t.kind, t.status, t.needs_review,
  t.amount, t.currency, t.occurred_at, t.has_time, t.source, t.merchant_raw, t.merchant_key, t.description,
  t.category_id, t.category_source, t.ai_confidence, t.ai_reason, t.confirmed_at, t.raw->>'channel' AS channel,
  t.raw->>'to_account' AS to_account,
  (SELECT m.action FROM ledger.merchant_memory m WHERE m.merchant_key = t.merchant_key) AS memory_action"""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def _uncategorized_id(conn) -> int:
    return conn.execute(text("SELECT id FROM ledger.categories WHERE parent_id IS NULL "
                             "AND name_en = 'Uncategorized'")).scalar_one()


def check_category(conn, category_id: int, kind: str = "expense") -> None:
    """Only leaf categories of the matching kind can be assigned (a parent like 'Food & Dining' is too
    vague, and an income category on a payment would be wrong)."""
    ok = conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM ledger.categories c WHERE c.id = :id AND c.kind = :kind "
        "AND NOT EXISTS (SELECT 1 FROM ledger.categories k WHERE k.parent_id = c.id))"),
        {"id": category_id, "kind": kind}).scalar()
    if not ok:
        raise Invalid(f"category {category_id} is not a leaf {kind} category")


def list_trees(conn, *, status: Optional[str] = None, account_id: Optional[int] = None,
               month: Optional[str] = None, limit: int = 200, offset: int = 0) -> dict:
    """Root transactions (newest first) with their descendants nested under `children`.
    Filters apply to the root; a tree is always returned whole."""
    where, params = ["t.parent_id IS NULL"], {"limit": limit, "offset": offset}
    if status:
        where.append("t.status = :status")
        params["status"] = status
    if account_id:
        where.append("t.account_id = :account_id")
        params["account_id"] = account_id
    if month:
        where.append("to_char(t.occurred_at AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM') = :month")
        params["month"] = month
    cond = " AND ".join(where)
    total = conn.execute(text(f"SELECT count(*) FROM ledger.transactions t WHERE {cond}"), params).scalar()
    roots = [dict(r._mapping) for r in conn.execute(text(
        f"SELECT {COLUMNS} FROM ledger.transactions t WHERE {cond} "
        "ORDER BY t.occurred_at DESC, t.id DESC LIMIT :limit OFFSET :offset"), params)]
    by_id = {r["id"]: r for r in roots}
    for r in roots:
        r["children"] = []
    if roots:
        # all descendants of the page's roots in one query
        rows = conn.execute(text(f"""
            WITH RECURSIVE tree AS (
              SELECT id FROM ledger.transactions WHERE parent_id = ANY(:ids)
              UNION ALL
              SELECT c.id FROM ledger.transactions c JOIN tree ON c.parent_id = tree.id)
            SELECT {COLUMNS} FROM ledger.transactions t JOIN tree ON tree.id = t.id
            ORDER BY t.id"""), {"ids": list(by_id)})
        for row in rows:
            node = dict(row._mapping)
            node["children"] = []
            by_id[node["id"]] = node
        for node in list(by_id.values()):
            if node["parent_id"] is not None and node["parent_id"] in by_id:
                by_id[node["parent_id"]]["children"].append(node)
    return {"total": total, "items": roots}


def get_one(conn, txn_id: int) -> dict:
    row = conn.execute(text(f"SELECT {COLUMNS} FROM ledger.transactions t WHERE t.id = :id"), {"id": txn_id}).first()
    if row is None:
        raise NotFound(txn_id)
    return dict(row._mapping)


def _is_leaf(conn, txn_id: int) -> bool:
    return not conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.transactions WHERE parent_id = :id)"),
                            {"id": txn_id}).scalar()


def update(conn, txn_id: int, *, category_id: Optional[int] = None, transfer_account_id: Optional[int] = None,
           description: Optional[str] = None, status: Optional[str] = None, remember: Optional[bool] = None,
           fields: set[str]) -> dict:
    """Partial update; `fields` lists what the client actually sent (so description can be cleared).

    category_id         -> an expense in that category (turns a transfer back into an expense)
    transfer_account_id -> a transfer to one of the user's accounts (not an expense, no category)
    remember=False      -> never remember this payee (rule 'ask'); True lifts that"""
    current = get_one(conn, txn_id)
    if "category_id" in fields and "transfer_account_id" in fields:
        raise Invalid("send either category_id or transfer_account_id, not both")
    if "status" in fields:
        if status not in ("pending", "ignored"):
            raise Invalid("status can only be set to pending or ignored here; use POST /transactions/confirm")
        if current["status"] == "confirmed":
            raise Invalid("a confirmed transaction cannot go back to pending/ignored")
        # ignoring/un-ignoring applies to the whole tree, like confirm
        conn.execute(text("""
            WITH RECURSIVE tree AS (SELECT id FROM ledger.transactions WHERE id = :id
              UNION ALL SELECT c.id FROM ledger.transactions c JOIN tree ON c.parent_id = tree.id)
            UPDATE ledger.transactions SET status = :s, updated_at = now() WHERE id IN (SELECT id FROM tree)"""),
            {"id": txn_id, "s": status})
    if ("category_id" in fields or "transfer_account_id" in fields) and current["kind"] not in ("expense", "transfer", "income"):
        raise Invalid(f"a {current['kind']} transaction cannot be re-categorized")
    if "transfer_account_id" in fields and current["kind"] == "income":
        raise Invalid("an income cannot become a transfer out; record the transfer on the sending account")
    if ("category_id" in fields or "transfer_account_id" in fields) and not _is_leaf(conn, txn_id):
        raise Invalid("only leaf transactions get a category or become transfers; edit the items under it")
    asks = memory.is_ask(conn, current["merchant_key"])
    if "category_id" in fields:
        if category_id is None:
            raise Invalid("category_id cannot be null")
        new_kind = "income" if current["kind"] == "income" else "expense"
        check_category(conn, category_id, new_kind)
        conn.execute(text("UPDATE ledger.transactions SET kind = :k, counter_account_id = NULL, "
                          "category_id = :c, category_source = 'user', updated_at = now() WHERE id = :id"),
                     {"c": category_id, "id": txn_id, "k": new_kind})
        # editing an already-confirmed row is also a confirmation of the payee's rule (T3.4)
        if current["status"] == "confirmed" and current["merchant_key"] and not asks:
            memory.remember(conn, current["merchant_key"], category_id)
    if "transfer_account_id" in fields:
        if transfer_account_id is None:
            raise Invalid("transfer_account_id cannot be null")
        if transfer_account_id == current["account_id"]:
            raise Invalid("a transfer must go to a different account")
        if not conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.accounts WHERE id = :a)"),
                            {"a": transfer_account_id}).scalar():
            raise Invalid(f"account {transfer_account_id} does not exist")
        conn.execute(text("UPDATE ledger.transactions SET kind = 'transfer', counter_account_id = :a, "
                          "category_id = NULL, category_source = 'user', ai_confidence = NULL, ai_reason = NULL, "
                          "updated_at = now() WHERE id = :id"), {"a": transfer_account_id, "id": txn_id})
        if current["status"] == "confirmed" and current["merchant_key"] and not asks:
            memory.remember(conn, current["merchant_key"], action="transfer", counter_account_id=transfer_account_id,
                            to_account=current["to_account"])
    if "remember" in fields:
        if remember is None:
            raise Invalid("remember must be true or false")
        if not current["merchant_key"]:
            raise Invalid("this transaction has no payee to remember")
        memory.set_ask(conn, current["merchant_key"], ask=not remember)
    if "description" in fields:
        conn.execute(text("UPDATE ledger.transactions SET description = :d, updated_at = now() WHERE id = :id"),
                     {"d": description, "id": txn_id})
    return get_one(conn, txn_id)


def confirm(conn, ids: Optional[list[int]] = None, all_pending: bool = False) -> dict:
    """Confirm whole trees (an id inside a tree confirms its root's tree). Leaf expenses without a
    category get Uncategorized. Each confirmed leaf teaches Merchant Memory, and the learned category
    is applied right away to other pending rows of the same merchant that the user has not set."""
    if all_pending:
        roots = [r[0] for r in conn.execute(text(
            "SELECT id FROM ledger.transactions WHERE parent_id IS NULL AND status = 'pending'"))]
    else:
        if not ids:
            raise Invalid("give ids or all=true")
        found = conn.execute(text("""
            WITH RECURSIVE up AS (SELECT id, parent_id FROM ledger.transactions WHERE id = ANY(:ids)
              UNION SELECT p.id, p.parent_id FROM ledger.transactions p JOIN up ON up.parent_id = p.id)
            SELECT id FROM up WHERE parent_id IS NULL"""), {"ids": ids}).scalars().all()
        n_found = conn.execute(text("SELECT count(*) FROM ledger.transactions WHERE id = ANY(:ids)"),
                               {"ids": ids}).scalar()
        if n_found != len(set(ids)):
            raise NotFound("some ids do not exist")
        roots = list(found)
    if not roots:
        return {"confirmed": 0, "remembered": 0, "remembered_transfers": 0, "applied_to_pending": 0}

    tree_ids = conn.execute(text("""
        WITH RECURSIVE tree AS (SELECT id FROM ledger.transactions WHERE id = ANY(:roots) AND status = 'pending'
          UNION ALL SELECT c.id FROM ledger.transactions c JOIN tree ON c.parent_id = tree.id)
        SELECT id FROM tree"""), {"roots": roots}).scalars().all()
    if not tree_ids:
        return {"confirmed": 0, "remembered": 0, "remembered_transfers": 0, "applied_to_pending": 0}
    unc = _uncategorized_id(conn)
    conn.execute(text("""
        UPDATE ledger.transactions t SET category_id = :unc, category_source = 'user'
        WHERE t.id = ANY(:ids) AND t.kind = 'expense' AND t.category_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)"""),
        {"ids": tree_ids, "unc": unc})
    confirmed = conn.execute(text(
        "UPDATE ledger.transactions SET status = 'confirmed', confirmed_at = now(), updated_at = now() "
        "WHERE id = ANY(:ids) AND status = 'pending'"), {"ids": tree_ids}).rowcount

    # learn from confirmed leaves (latest per payee/destination wins); "Uncategorized" teaches nothing,
    # and payees marked "ask" are never learned
    leaves = conn.execute(text("""
        SELECT t.merchant_key, t.kind, t.category_id, t.counter_account_id, t.raw->>'to_account' AS to_account
        FROM ledger.transactions t
        WHERE t.id = ANY(:ids) AND t.merchant_key IS NOT NULL AND t.kind IN ('expense', 'transfer')
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
          AND NOT EXISTS (SELECT 1 FROM ledger.merchant_memory m WHERE m.merchant_key = t.merchant_key AND m.action = 'ask')
        ORDER BY t.occurred_at"""), {"ids": tree_ids}).all()
    learned: dict[str, tuple] = {}
    for r in leaves:
        if r.kind == "transfer" and r.counter_account_id:
            learned[memory.rule_key(r.merchant_key, r.to_account)] = ("transfer", r)
        elif r.kind == "expense" and r.category_id and r.category_id != unc:
            learned[r.merchant_key] = ("category", r)
    applied = 0
    n_transfer = 0
    for key, (action, r) in learned.items():
        if action == "category":
            memory.remember(conn, r.merchant_key, r.category_id)
            applied += conn.execute(text(
                "UPDATE ledger.transactions SET kind = 'expense', counter_account_id = NULL, category_id = :c, "
                "category_source = 'memory', ai_confidence = NULL, ai_reason = NULL, updated_at = now() "
                "WHERE status = 'pending' AND merchant_key = :k AND category_source IN ('none', 'ai') "
                "AND kind = 'expense' AND (category_id IS DISTINCT FROM :c) "
                "AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = ledger.transactions.id)"),
                {"c": r.category_id, "k": r.merchant_key}).rowcount
        else:
            n_transfer += 1
            memory.remember(conn, r.merchant_key, action="transfer", counter_account_id=r.counter_account_id,
                            to_account=r.to_account)
            # same payee and, when known, same destination number
            d = memory.digits(r.to_account)
            applied += conn.execute(text(
                "UPDATE ledger.transactions SET kind = 'transfer', counter_account_id = :a, category_id = NULL, "
                "category_source = 'memory', ai_confidence = NULL, ai_reason = NULL, updated_at = now() "
                "WHERE status = 'pending' AND merchant_key = :k AND category_source IN ('none', 'ai') "
                "AND kind = 'expense' AND account_id <> :a "
                "AND (:d = '' OR right(regexp_replace(coalesce(raw->>'to_account', ''), '\\D', '', 'g'), 4) = :d) "
                "AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = ledger.transactions.id)"),
                {"a": r.counter_account_id, "k": r.merchant_key, "d": d[-4:] if len(d) >= 4 else ""}).rowcount
    return {"confirmed": confirmed, "remembered": len(learned), "remembered_transfers": n_transfer,
            "applied_to_pending": applied}


def create_manual(conn, *, account_id: int, amount: Decimal, occurred_at: datetime, category_id: int,
                  merchant: Optional[str] = None, description: Optional[str] = None, has_time: bool = True,
                  kind: str = "expense") -> dict:
    """Cash, TrueMoney, income... anything without an email: confirmed immediately."""
    if kind not in ("expense", "income"):
        raise Invalid("kind must be expense or income")
    if amount <= 0:
        raise Invalid("amount must be positive")
    if occurred_at.tzinfo is None:
        raise Invalid("occurred_at needs a timezone, e.g. 2026-10-05T12:30:00+07:00")
    if not conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.accounts WHERE id = :a)"), {"a": account_id}).scalar():
        raise Invalid(f"account {account_id} does not exist")
    check_category(conn, category_id, kind)
    key = normalize_merchant(merchant)
    new_id = conn.execute(text("""
        INSERT INTO ledger.transactions (account_id, node_type, kind, status, amount, occurred_at, has_time,
          source, dedup_key, merchant_raw, merchant_key, description, category_id, category_source, confirmed_at)
        VALUES (:a, 'manual', :kind, 'confirmed', :amt, :at, :ht, 'manual', :dk, :m, :k, :d, :c, 'manual', now())
        RETURNING id"""), {"a": account_id, "amt": amount, "at": occurred_at, "ht": has_time,
                           "dk": f"manual:{uuid.uuid4()}", "m": merchant, "k": key, "d": description,
                           "c": category_id, "kind": kind}).scalar()
    # income payers are not learned: Merchant Memory only categorizes expenses
    if kind == "expense" and key and category_id != _uncategorized_id(conn) and not memory.is_ask(conn, key):
        memory.remember(conn, key, category_id)
    return get_one(conn, new_id)
