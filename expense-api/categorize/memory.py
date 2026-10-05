"""Merchant Memory: merchant_key -> category learned from what the user confirmed."""
from sqlalchemy import text


def lookup(conn, keys: list[str]) -> dict[str, int]:
    if not keys:
        return {}
    rows = conn.execute(text("SELECT merchant_key, category_id FROM ledger.merchant_memory "
                             "WHERE merchant_key = ANY(:k)"), {"k": list(set(keys))})
    return {r[0]: r[1] for r in rows}


def remember(conn, merchant_key: str, category_id: int) -> None:
    """Call inside the same DB transaction as the confirm (T3.4). The latest choice wins."""
    conn.execute(text(
        "INSERT INTO ledger.merchant_memory (merchant_key, category_id) VALUES (:k, :c) "
        "ON CONFLICT (merchant_key) DO UPDATE SET category_id = EXCLUDED.category_id, "
        "hit_count = ledger.merchant_memory.hit_count + 1, last_confirmed_at = now()"),
        {"k": merchant_key, "c": category_id})
