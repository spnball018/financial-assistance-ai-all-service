"""Reports read only ledger.v_reportable_expenses (confirmed leaf expenses), so parents and transfers never count."""
from sqlalchemy import text


def monthly_summary(conn, uid: int, month: str) -> dict:
    rows = conn.execute(text("""
        SELECT COALESCE(p.id, k.id) AS group_id, COALESCE(p.name_th, k.name_th) AS group_th,
               COALESCE(p.name_en, k.name_en) AS group_en,
               k.id AS category_id, k.name_th, k.name_en, count(*) AS count, sum(v.amount) AS amount
        FROM ledger.v_reportable_expenses v
        LEFT JOIN ledger.categories k ON k.id = v.category_id
        LEFT JOIN ledger.categories p ON p.id = k.parent_id
        WHERE v.user_id = :uid AND to_char(v.occurred_at AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM') = :m
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY 1, 4"""), {"m": month, "uid": uid}).all()
    groups: dict = {}
    for r in rows:
        g = groups.setdefault(r.group_id, {"category_id": r.group_id, "name_th": r.group_th, "name_en": r.group_en,
                                           "count": 0, "amount": 0, "subcategories": []})
        g["count"] += r.count
        g["amount"] += r.amount
        if r.category_id != r.group_id:
            g["subcategories"].append({"category_id": r.category_id, "name_th": r.name_th, "name_en": r.name_en,
                                       "count": r.count, "amount": r.amount})
    out = sorted(groups.values(), key=lambda g: g["amount"], reverse=True)
    pending = conn.execute(text("""
        SELECT count(*) FROM ledger.transactions
        WHERE user_id = :uid AND status = 'pending' AND parent_id IS NULL
          AND to_char(occurred_at AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM') = :m"""), {"m": month, "uid": uid}).scalar()
    # money moved to the user's own accounts: shown so it is visible, never counted as spending
    transfers = [dict(r._mapping) for r in conn.execute(text("""
        SELECT a.id AS account_id, a.name, count(*) AS count, sum(t.amount) AS amount
        FROM ledger.transactions t JOIN ledger.accounts a ON a.id = t.counter_account_id
        WHERE t.user_id = :uid AND t.status = 'confirmed' AND t.kind = 'transfer'
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
          AND to_char(t.occurred_at AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM') = :m
        GROUP BY a.id, a.name ORDER BY amount DESC"""), {"m": month, "uid": uid})]
    income = [dict(r._mapping) for r in conn.execute(text("""
        SELECT k.id AS category_id, k.name_th, k.name_en, count(*) AS count, sum(t.amount) AS amount
        FROM ledger.transactions t LEFT JOIN ledger.categories k ON k.id = t.category_id
        WHERE t.user_id = :uid AND t.status = 'confirmed' AND t.kind = 'income'
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
          AND to_char(t.occurred_at AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM') = :m
        GROUP BY k.id, k.name_th, k.name_en ORDER BY amount DESC"""), {"m": month, "uid": uid})]
    total = sum((g["amount"] for g in out), 0)
    income_total = sum((i["amount"] for i in income), 0)
    return {"month": month, "total": total,
            "count": sum(g["count"] for g in out), "pending_not_counted": pending, "categories": out,
            "transfers_out": transfers,
            "income": {"total": income_total, "categories": income}, "net": income_total - total}
