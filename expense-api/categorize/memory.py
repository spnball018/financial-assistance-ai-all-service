"""Merchant rules learned from what the user confirmed (T3.4, T6.2).

A rule says, for one payee: use this category | it is a transfer to one of my accounts | ask me
every time (never remember). Transfer rules are keyed by payee + last 4 digits of the destination
when the email has one, because the user's own name is the payee for several of their accounts.
"""
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text


@dataclass
class Rule:
    action: str  # category | transfer | ask
    category_id: Optional[int] = None
    counter_account_id: Optional[int] = None


def digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def rule_key(merchant_key: Optional[str], to_account: Optional[str] = None) -> Optional[str]:
    """Key a transfer rule is stored under: 'payee|1234' when the destination number is known."""
    if not merchant_key:
        return None
    d = digits(to_account)
    return f"{merchant_key}|{d[-4:]}" if len(d) >= 4 else merchant_key


def lookup(conn, uid: int, keys: list[str]) -> dict[str, Rule]:
    if not keys:
        return {}
    rows = conn.execute(text("SELECT merchant_key, action, category_id, counter_account_id "
                             "FROM ledger.merchant_memory WHERE user_id = :u AND merchant_key = ANY(:k)"),
                        {"u": uid, "k": list(set(keys))})
    return {r.merchant_key: Rule(r.action, r.category_id, r.counter_account_id) for r in rows}


def find(rules: dict[str, Rule], merchant_key: Optional[str], to_account: Optional[str]) -> Optional[Rule]:
    """Most specific rule first (payee + destination), then the payee alone."""
    if not merchant_key:
        return None
    specific = rule_key(merchant_key, to_account)
    return rules.get(specific) or rules.get(merchant_key)


def remember(conn, uid: int, merchant_key: str, category_id: Optional[int] = None, *,
             counter_account_id: Optional[int] = None, action: str = "category", to_account: Optional[str] = None) -> None:
    """Upsert a rule; the latest choice wins. Call inside the confirm's DB transaction.
    Category and ask rules are per payee; transfer rules are per payee + destination."""
    key = rule_key(merchant_key, to_account) if action == "transfer" else merchant_key
    conn.execute(text(
        "INSERT INTO ledger.merchant_memory (user_id, merchant_key, action, category_id, counter_account_id) "
        "VALUES (:u, :k, :a, :c, :t) "
        "ON CONFLICT (user_id, merchant_key) DO UPDATE SET action = EXCLUDED.action, category_id = EXCLUDED.category_id, "
        "counter_account_id = EXCLUDED.counter_account_id, "
        "hit_count = ledger.merchant_memory.hit_count + 1, last_confirmed_at = now()"),
        {"u": uid, "k": key, "a": action, "c": category_id if action == "category" else None,
         "t": counter_account_id if action == "transfer" else None})


def is_ask(conn, uid: int, merchant_key: Optional[str]) -> bool:
    if not merchant_key:
        return False
    return bool(conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.merchant_memory "
                                  "WHERE user_id = :u AND merchant_key = :k AND action = 'ask')"),
                             {"u": uid, "k": merchant_key}).scalar())


def set_ask(conn, uid: int, merchant_key: str, ask: bool) -> None:
    """ask=True: never remember this payee (drops any learned rules for it). ask=False: lift that."""
    if ask:
        conn.execute(text("DELETE FROM ledger.merchant_memory WHERE user_id = :u AND merchant_key LIKE :p"),
                     {"u": uid, "p": merchant_key.replace("%", r"\%").replace("_", r"\_") + "|%"})
        remember(conn, uid, merchant_key, action="ask")
    else:
        conn.execute(text("DELETE FROM ledger.merchant_memory WHERE user_id = :u AND merchant_key = :k "
                          "AND action = 'ask'"), {"u": uid, "k": merchant_key})


def own_account_for(conn, uid: int, to_account: Optional[str], from_account_id: int) -> Optional[int]:
    """The user's account whose masked_ref matches the end of the destination number, if exactly one."""
    d = digits(to_account)
    if len(d) < 4:
        return None
    rows = conn.execute(text("SELECT id, masked_ref FROM ledger.accounts "
                             "WHERE user_id = :u AND masked_ref IS NOT NULL AND id <> :me"),
                        {"u": uid, "me": from_account_id}).all()
    hits = [r.id for r in rows if len(digits(r.masked_ref)) >= 4 and d.endswith(digits(r.masked_ref))]
    return hits[0] if len(hits) == 1 else None
