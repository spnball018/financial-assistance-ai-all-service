"""Categorize pending leaf transactions: Merchant Memory first, then AI, then Uncategorized."""
import logging
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from categorize import memory
from categorize.ai import Category, Classifier, Item
from categorize.normalize import normalize_merchant

log = logging.getLogger("categorize")

UNCATEGORIZED_EN = "Uncategorized"


@dataclass
class Counts:
    memory: int = 0
    ai: int = 0
    uncategorized: int = 0  # AI was unsure (below threshold) or returned an invalid category
    left: int = 0           # still category_source='none' (no AI configured or AI call failed)
    error: Optional[str] = None

    def as_dict(self) -> dict:
        d = {"memory": self.memory, "ai": self.ai, "uncategorized": self.uncategorized, "left": self.left}
        if self.error:
            d["error"] = self.error
        return d


def _batch_size() -> int:
    return max(1, int(os.environ.get("CATEGORIZE_BATCH_SIZE", "25")))


def _threshold() -> float:
    return float(os.environ.get("AI_CONFIDENCE_THRESHOLD", "0.7"))


# Leaves only: parents (payment with an order under it, statement) are never reported, so never categorized.
_TODO = text("""
SELECT t.id, t.merchant_raw, t.description, t.amount, t.node_type, t.raw->>'channel' AS channel
FROM ledger.transactions t
WHERE t.status = 'pending' AND t.kind = 'expense' AND t.category_source = 'none'
  AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
ORDER BY t.id""")

_SET = text("""
UPDATE ledger.transactions
SET category_id = :cat, category_source = :src, ai_confidence = :conf, ai_reason = :reason, updated_at = now()
WHERE id = :id AND category_source = 'none'""")


def _leaf_categories(conn) -> tuple[list[Category], int]:
    rows = conn.execute(text("""
        SELECT c.id, c.name_en, c.name_th, p.name_en AS p_en, p.name_th AS p_th
        FROM ledger.categories c LEFT JOIN ledger.categories p ON p.id = c.parent_id
        WHERE c.kind = 'expense'
          AND NOT EXISTS (SELECT 1 FROM ledger.categories k WHERE k.parent_id = c.id)
        ORDER BY COALESCE(c.parent_id, c.id), c.sort_order""")).all()
    cats = [Category(r.id, f"{r.p_en} > {r.name_en} / {r.p_th} > {r.name_th}" if r.p_en
                     else f"{r.name_en} / {r.name_th}") for r in rows]
    unc = next(r.id for r in rows if r.name_en == UNCATEGORIZED_EN)
    return cats, unc


def categorize_pending(engine: Engine, classifier: Optional[Classifier]) -> dict:
    c = Counts()
    with engine.begin() as conn:
        todo = conn.execute(_TODO).all()
        if not todo:
            return c.as_dict()
        # store merchant_key on every row we look at; T3.4 uses it when the user confirms
        keys = {}
        for r in todo:
            keys[r.id] = normalize_merchant(r.merchant_raw)
            conn.execute(text("UPDATE ledger.transactions SET merchant_key = :k WHERE id = :id"),
                         {"k": keys[r.id], "id": r.id})
        known = memory.lookup(conn, [k for k in keys.values() if k])
        rest = []
        for r in todo:
            cat = known.get(keys[r.id])
            if cat:
                conn.execute(_SET, {"cat": cat, "src": "memory", "conf": None, "reason": None, "id": r.id})
                c.memory += 1
            else:
                rest.append(r)
        cats, unc = _leaf_categories(conn)

    if classifier is None:
        c.left = len(rest)
        return c.as_dict()

    valid = {x.id for x in cats}
    threshold = _threshold()
    size = _batch_size()
    for start in range(0, len(rest), size):
        batch = rest[start:start + size]
        items = [Item(id=r.id, merchant=r.merchant_raw or "", channel=r.channel or r.node_type,
                      amount=r.amount, description=r.description) for r in batch]
        try:
            preds = {p.id: p for p in classifier.classify(items, cats)}
        except Exception as e:
            # keep the class name only; the message can echo the prompt (merchant names, amounts)
            log.warning("AI categorize failed: %s", type(e).__name__)
            c.error = f"ai: {type(e).__name__}"
            c.left += len(rest) - start
            break
        with engine.begin() as conn:
            for r in batch:
                p = preds.get(r.id)
                if p is None:
                    c.left += 1  # AI skipped it; try again next run
                    continue
                conf = max(0.0, min(1.0, p.confidence))
                ok = p.category_id in valid and conf >= threshold
                conn.execute(_SET, {"cat": p.category_id if ok else unc, "src": "ai", "conf": round(conf, 3),
                                    "reason": (p.reason or "")[:500], "id": r.id})
                if ok and p.category_id != unc:
                    c.ai += 1
                else:
                    c.uncategorized += 1
    return c.as_dict()
