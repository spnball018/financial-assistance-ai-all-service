"""T2.1: split a bank payment to Shopee/Lazada into the order(s) it paid for.

An order (ledger.ecommerce_orders, from sources/shopee.py and lazada.py) is matched to one pending
payment that has not been split yet: payee looks like the platform, same amount, paid from a few
minutes before to a day after the order time (Lazada PromptPay can be paid hours later). Shopee
checks out several shops at once as separate orders with the same order time and one payment, so
orders are grouped by order time first. When the order time is only an estimate (Lazada shipped
email), the payment must fall in the 14 days before it.

A match adds children under the payment, still pending, so the user confirms it on the review page:
payment -> order (one per order) -> order_item + adjustment (shipping, discounts). `unlink` removes
them and remembers the payment so the same pair is not proposed again. The adjustments are not
categorized on their own (a discount is not a kind of spending, and Merchant Memory must not learn
"Shopee discount"): `adjustments_follow_items` gives them the category of the order's largest item.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from ledger.transactions import Invalid, get_one
from sources.base import TxnNode
from sync import repo

BEFORE = timedelta(minutes=5)
AFTER = timedelta(hours=24)
ESTIMATE_BEFORE = timedelta(days=14)

PAYEE = {"shopee": r"shopee|ช้อปปี้", "lazada": r"lazada|ลาซาด้า"}
PLATFORM_NAME = {"shopee": "Shopee", "lazada": "Lazada"}
SOURCE = {"shopee": "shopee_email", "lazada": "lazada_email"}

_CANDIDATES = text("""
SELECT t.id FROM ledger.transactions t
WHERE t.user_id = :uid AND t.parent_id IS NULL AND t.status = 'pending' AND t.kind = 'expense'
  AND t.has_time AND t.amount = :amount AND t.merchant_raw ~* :payee
  AND t.occurred_at BETWEEN :lo AND :hi
  AND NOT (t.id = ANY(:rejected))
  AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)""")


def _unmatched(conn, uid: int) -> list:
    return conn.execute(text("""
        SELECT id, platform, order_id, ordered_at, total_amount, raw, raw_email_id
        FROM ledger.ecommerce_orders
        WHERE user_id = :uid AND matched_txn_id IS NULL ORDER BY ordered_at, id"""), {"uid": uid}).all()


def _window(o) -> tuple:
    if (o.raw or {}).get("ordered_at_exact", True):
        return o.ordered_at - BEFORE, o.ordered_at + AFTER
    return o.ordered_at - ESTIMATE_BEFORE, o.ordered_at


def _order_node(o) -> TxnNode:
    raw = o.raw or {}
    name = PLATFORM_NAME[o.platform]
    sellers = ", ".join(raw.get("sellers") or [])
    children = []
    for n, ln in enumerate(raw.get("lines", [])):
        amount = Decimal(ln["amount"])
        if amount == 0:
            continue  # free gifts
        item = ln["kind"] == "item"
        children.append(TxnNode(
            node_type="order_item" if item else "adjustment", kind="expense", amount=amount,
            occurred_at=o.ordered_at, dedup_key=f"{o.platform}:{o.order_id}:{n}",
            merchant_raw=ln["name"][:200],
            description=(f"{ln['variant']} · " if ln.get("variant") else "") + (f"x{ln['quantity']} · " if item and ln["quantity"] > 1 else "") + f"{name} {sellers}".strip(),
            source_ref=o.order_id, raw={"line_kind": ln["kind"], "quantity": ln["quantity"]}))
    return TxnNode(node_type="order", kind="expense", amount=o.total_amount, occurred_at=o.ordered_at,
                   dedup_key=f"{o.platform}:{o.order_id}", merchant_raw=f"{name} · {sellers}" if sellers else name,
                   description=f"คำสั่งซื้อ #{o.order_id}", source_ref=o.order_id,
                   raw={"ecommerce_order_id": o.id}, children=children)


def _split(conn, uid: int, payment_id: int, orders: list) -> None:
    payment = get_one(conn, uid, payment_id)
    for o in orders:
        email = repo.load_raw_email(conn, uid, o.raw_email_id)
        repo.insert_tree(conn, _order_node(o), user_id=uid, account_id=payment["account_id"], source=SOURCE[o.platform],
                         raw_email=email, parent_id=payment_id)
        conn.execute(text("UPDATE ledger.ecommerce_orders SET matched_txn_id = :t WHERE id = :id"),
                     {"t": payment_id, "id": o.id})


def link_orders(conn, uid: int) -> dict:
    counts = {"linked_orders": 0, "linked_payments": 0, "ambiguous": 0}
    orders = _unmatched(conn, uid)
    # one checkout = same platform and order time (exact times only)
    groups: dict[tuple, list] = defaultdict(list)
    for o in orders:
        exact = (o.raw or {}).get("ordered_at_exact", True)
        groups[(o.platform, o.ordered_at) if exact else (o.platform, "order", o.id)].append(o)

    def candidates(group: list) -> list[int]:
        lo, hi = _window(group[0])
        rejected = sorted({t for o in group for t in (o.raw or {}).get("rejected_txn_ids", [])})
        return conn.execute(_CANDIDATES, {
            "uid": uid, "amount": sum(o.total_amount for o in group), "payee": PAYEE[group[0].platform],
            "lo": lo, "hi": hi, "rejected": rejected}).scalars().all()

    proposals: list[tuple[list, int]] = []
    for group in groups.values():
        found = candidates(group)
        if len(found) != 1 and len(group) > 1:
            # the shops were paid separately after all: try each order on its own
            for o in group:
                single = candidates([o])
                if len(single) == 1:
                    proposals.append(([o], single[0]))
                elif single:
                    counts["ambiguous"] += 1
            continue
        if len(found) == 1:
            proposals.append((group, found[0]))
        elif found:
            counts["ambiguous"] += 1

    # a payment claimed by two different groups is ambiguous: link neither
    claims: dict[int, int] = defaultdict(int)
    for _, pid in proposals:
        claims[pid] += 1
    for group, pid in proposals:
        if claims[pid] > 1:
            counts["ambiguous"] += 1
            continue
        _split(conn, uid, pid, group)
        counts["linked_orders"] += len(group)
        counts["linked_payments"] += 1
    return counts


def unlink(conn, uid: int, payment_id: int) -> int:
    """Undo a match on a pending payment; the orders will not be matched to this payment again."""
    t = get_one(conn, uid, payment_id)
    if t["status"] != "pending":
        raise Invalid("only a pending payment can be unlinked")
    conn.execute(text("""
        UPDATE ledger.ecommerce_orders
        SET matched_txn_id = NULL,
            raw = jsonb_set(raw, '{rejected_txn_ids}', COALESCE(raw->'rejected_txn_ids', '[]'::jsonb) || to_jsonb(:p))
        WHERE user_id = :uid AND matched_txn_id = :p"""), {"p": payment_id, "uid": uid})
    # order rows cascade to their items and adjustments
    return conn.execute(text("DELETE FROM ledger.transactions WHERE user_id = :uid AND parent_id = :p "
                             "AND node_type = 'order'"), {"p": payment_id, "uid": uid}).rowcount


def adjustments_follow_items(conn, uid: int) -> int:
    """Run after categorizing. Only touches pending adjustments that have no category yet."""
    return conn.execute(text("""
        UPDATE ledger.transactions a
        SET category_id = i.category_id, category_source = i.category_source, updated_at = now()
        FROM (
          SELECT DISTINCT ON (parent_id) parent_id, category_id, category_source FROM ledger.transactions
          WHERE user_id = :uid AND node_type = 'order_item' AND category_id IS NOT NULL
          ORDER BY parent_id, amount DESC, id) i
        WHERE i.parent_id = a.parent_id AND a.user_id = :uid AND a.status = 'pending'
          AND a.node_type = 'adjustment' AND a.source IN ('shopee_email', 'lazada_email')
          AND a.category_id IS NULL"""), {"uid": uid}).rowcount
