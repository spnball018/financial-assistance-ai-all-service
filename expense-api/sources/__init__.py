"""Registry of per-source parsers, keyed by ledger.email_sources.source.

Adding a source = add sources/<name>.py following sources/base.py and register it here.
"""
from types import ModuleType

from sources import kbank, ktc, lazada, shopee

REGISTRY: dict[str, ModuleType] = {m.SOURCE: m for m in (kbank, ktc, lazada, shopee)}
