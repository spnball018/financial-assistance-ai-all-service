"""merchant_raw -> merchant_key used for Merchant Memory lookups (exact match)."""
import re
import unicodedata
from typing import Optional

# Honorifics on PromptPay / transfer payees, so "นาย สมชาย ใจดี" and "สมชาย ใจดี" share a key
_TITLES = re.compile(r"^(นางสาว|นาง|นาย|น\.ส\.|ด\.ช\.|ด\.ญ\.|mrs|mr|ms|miss)\.?\s+", re.I)
_PAREN = re.compile(r"\([^)]*\)")


def normalize_merchant(raw: Optional[str]) -> Optional[str]:
    """Lowercase, drop (...) parts, branch numbers/codes, punctuation and extra spaces.

    "MCP-PT Ban Pho 3 (พีที บ้านโพธิ์3)" -> "mcp pt ban pho"
    Thai vowel/tone marks are kept (they are not punctuation)."""
    if not raw:
        return None
    s = _PAREN.sub(" ", unicodedata.normalize("NFC", raw))
    s = _TITLES.sub("", s.strip())
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat == "Nd" or cat[0] in "PSZ" or ch in "\t\r\n":
            out.append(" ")  # digits (branch no., codes), punctuation, symbols, spaces
        else:
            out.append(ch)
    key = " ".join("".join(out).lower().split())
    return key or None
