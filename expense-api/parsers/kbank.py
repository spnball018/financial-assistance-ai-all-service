"""Parser for K PLUS "Result of ... (Success)" emails (plain-text part, no LLM)."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

BANGKOK = timezone(timedelta(hours=7))

# English subject (after "Result of ") -> channel
_CHANNELS = {
    "payment": "bill_payment",
    "funds transfer": "transfer",
    "promptpay funds transfer": "promptpay",
    "donation": "donation",
}
_SUBJECT_RE = re.compile(r"Result of (.+?) \(Success\)", re.I)
_FIELD_RE = re.compile(r"^[ \t]*([^:\r\n]+?)[ \t]*:[ \t]*(.*?)[ \t]*\r?$", re.M)


@dataclass
class KbankTxn:
    txn_no: str
    occurred_at: datetime
    amount: Decimal
    fee: Decimal
    channel: str  # bill_payment | transfer | promptpay | donation
    from_account: str
    merchant_raw: str  # payee: company / account holder / wallet holder
    to_account: Optional[str]  # transfer account or wallet id, if any
    balance_after: Optional[Decimal]  # "Available Balance" the bank reports after this transaction
    gmail_message_id: Optional[str]

    @property
    def dedup_key(self) -> str:
        return f"kbank:{self.txn_no}"


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _fields(block: str) -> dict:
    return {m.group(1).strip(): m.group(2) for m in _FIELD_RE.finditer(block)}


def _strip_account_name(company: str) -> str:
    """'MCP-PT Ban Pho 3 (...) (ชื่อบัญชี: บริษัท ... จำกัด)' -> keep the trade name."""
    return re.sub(r"\s*\(ชื่อบัญชี:.*\)\s*$", "", company).strip()


def parse_kbank(message: dict) -> Optional[KbankTxn]:
    """Parse a gmail-service message dict. Returns None for non-K PLUS-result emails
    (including any channel we do not know yet)."""
    body = message.get("text_body") or ""
    subj = _SUBJECT_RE.search(body)
    if not subj:
        return None
    channel = _CHANNELS.get(subj.group(1).strip().lower())
    if not channel:
        return None

    # The email carries a Thai block then an English one; the English labels are stable,
    # while the Thai block has the real payee name for PromptPay/transfers.
    en_block = body[body.index("Dear Customer"):]
    th_block = body[: body.index("Dear Customer")]
    en, th = _fields(en_block), _fields(th_block)

    try:
        occurred = datetime.strptime(en["Transaction Date"], "%d/%m/%Y  %H:%M:%S").replace(tzinfo=BANGKOK)
        txn_no = en["Transaction Number"]
        amount = _money(en["Amount (THB)"])
        fee = _money(en.get("Fee (THB)", "0"))
    except (KeyError, ValueError, ArithmeticError):  # Decimal raises InvalidOperation (ArithmeticError)
        return None
    try:
        balance_after = _money(en["Available Balance (THB)"])
    except (KeyError, ValueError, ArithmeticError):
        balance_after = None

    from_account = en.get("Paid From Account") or en.get("From Account") or ""
    to_account = en.get("To Account") or en.get("To Wallet ID") or en.get("To PromptPay ID")

    if channel == "bill_payment":
        merchant = _strip_account_name(en.get("Company Name", ""))
    elif channel == "donation":
        merchant = en.get("Company Name", "")
    elif channel == "promptpay":
        # Two variants: to a wallet ("ชื่อ Wallet") or to a PromptPay ID ("ชื่อผู้รับเงิน").
        # English "Wallet Name" is only the wallet brand, so prefer the Thai holder name.
        merchant = (th.get("ชื่อ Wallet") or th.get("ชื่อผู้รับเงิน")
                    or en.get("Received Name") or en.get("Wallet Name", ""))
    else:
        merchant = th.get("ชื่อบัญชี") or en.get("Account Name", "")

    return KbankTxn(
        txn_no=txn_no,
        occurred_at=occurred,
        amount=amount,
        fee=fee,
        channel=channel,
        from_account=from_account,
        merchant_raw=merchant,
        to_account=to_account,
        balance_after=balance_after,
        gmail_message_id=message.get("id"),
    )
