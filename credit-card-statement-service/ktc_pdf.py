"""Parse a KTC credit card e-Statement PDF. Owned by the KTC track: docs/ktc-track.md

Everything happens in memory: the PDF is unlocked into a BytesIO and never written to disk.
Never log the password, the PDF content or exception text from the PDF libraries.

Design: fail closed. The layout is found from header text (not fixed coordinates) and the result is
accepted only if previous balance + sum(lines) == new balance, so a template change surfaces as an
error instead of silently wrong numbers.
"""
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import pdfplumber
import pikepdf

PARSER_LAYOUT = "ktc-pdf-1"

DATE_RE = re.compile(r"^\d{2}/\d{2}/(\d{2}|\d{4})$")
AMOUNT_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
CARD_RE = re.compile(r"\d{4}-\d{2}[X*]{2}-[X*]{4}-\d{4}")
LABEL_RE = re.compile(r"^[A-Z][A-Z /.()\-]+$")
LINE_TOLERANCE = 3

# English keywords match whole words only (COFFEE must not hit FEE); Thai has no word breaks, so substring.
FEE_RE = re.compile(r"\b(FEE|FEES|VAT)\b|ค่าธรรมเนียม|ภาษีมูลค่าเพิ่ม")
INTEREST_RE = re.compile(r"\bINTEREST\b|ดอกเบี้ย")
PAYMENT_RE = re.compile(r"\b(PAYMENT|AUTOPAY|AUTO DEBIT)\b|ชำระ|หักบัญชี")


class PasswordRejected(Exception):
    pass


class KtcParseError(Exception):
    """`code` is a stable machine-readable reason; it never contains PDF content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def unlock(data: bytes, password: str) -> io.BytesIO:
    try:
        with pikepdf.open(io.BytesIO(data), password=password) as pdf:
            out = io.BytesIO()
            pdf.save(out)
    except pikepdf.PasswordError:
        raise PasswordRejected() from None
    except Exception:
        raise KtcParseError("unreadable_pdf") from None
    out.seek(0)
    return out


def _to_date(text: str) -> date:
    d, m, y = text.split("/")
    year = int(y)
    if year < 100:
        # 2-digit year: >= 50 is Buddhist era (69 -> 2569 -> 2026), else Gregorian (26 -> 2026)
        year = 1957 + year if year >= 50 else 2000 + year
    elif year > 2400:
        year -= 543
    try:
        return date(year, int(m), int(d))
    except ValueError:
        raise KtcParseError("bad_date") from None


def _to_amount(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise KtcParseError("bad_amount") from None


def _classify(description: str, amount: Decimal) -> str:
    upper = description.upper()
    if amount < 0:
        return "payment" if PAYMENT_RE.search(upper) else "refund"
    if INTEREST_RE.search(upper):
        return "interest"
    if FEE_RE.search(upper):
        return "fee"
    return "purchase"


def _summary_fields(page) -> dict:
    """Read the page-1 summary grid by its English labels; value = last token before the label."""
    found: dict[str, str] = {}
    for table in page.extract_tables():
        for row in table:
            for cell in row:
                lines = [x.strip() for x in (cell or "").split("\n") if x.strip()]
                for i, line in enumerate(lines):
                    if i and LABEL_RE.match(line):
                        found[line] = lines[i - 1].split()[-1]
    return found


def _group_lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _header(lines: list[list[dict]]) -> Optional[tuple[int, float]]:
    """(index of the header's last line, x of the DESCRIPTION column) or None."""
    for i, line in enumerate(lines):
        texts = {w["text"] for w in line}
        if {"POSTING", "DESCRIPTION"} <= texts and any(t.startswith("AMOUNT") for t in texts):
            desc_x = next(w["x0"] for w in line if w["text"] == "DESCRIPTION")
            return i, desc_x
    return None


def _split_amount(line: list[dict]) -> tuple[list[dict], Optional[Decimal]]:
    """Strip a trailing amount (and a separate '-' sign) from a row."""
    if not line or not AMOUNT_RE.match(line[-1]["text"]):
        return line, None
    amount = _to_amount(line[-1]["text"])
    rest = line[:-1]
    if rest and rest[-1]["text"] == "-":
        amount, rest = -abs(amount), rest[:-1]
    return rest, amount


def parse_pdf(data: bytes, password: str) -> dict:
    pdf_bytes = unlock(data, password)
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            summary = _summary_fields(pdf.pages[0])
            page_lines = [_group_lines(p.extract_words()) for p in pdf.pages]
    except Exception:
        raise KtcParseError("unreadable_pdf") from None

    for label in ("CLOSING DATE", "PAYMENT DUE DATE", "CARD NUMBER"):
        if label not in summary:
            raise KtcParseError("summary_layout_changed")
    card = CARD_RE.search(summary["CARD NUMBER"])
    if not card:
        raise KtcParseError("summary_layout_changed")

    lines: list[dict] = []
    previous_balance: Optional[Decimal] = None
    new_balance: Optional[Decimal] = None
    saw_header = False

    for plines in page_lines:
        header = _header(plines)
        if not header or new_balance is not None:
            continue
        saw_header = True
        start, desc_x = header
        for row in plines[start + 1:]:
            dates = [w for w in row[:2] if DATE_RE.match(w["text"]) and w["x0"] < desc_x - 5]
            body, amount = _split_amount(row)
            if len(dates) == 2:
                if amount is None:
                    raise KtcParseError("line_without_amount")
                desc = " ".join(w["text"] for w in body[2:])
                lines.append({
                    "transaction_date": _to_date(dates[0]["text"]),
                    "posting_date": _to_date(dates[1]["text"]),
                    "description": desc,
                    "amount": amount,
                    "approval_code": None,
                    "type": _classify(desc, amount),
                })
            elif dates:
                raise KtcParseError("line_layout_changed")
            elif amount is not None:
                # undated amount row: first = previous balance, one after the lines = new balance
                if not lines and previous_balance is None:
                    previous_balance = amount
                elif lines:
                    new_balance = amount
                    break
            elif lines and body and body[0]["x0"] >= desc_x - 2:
                lines[-1]["description"] += " " + " ".join(w["text"] for w in body)

    if not saw_header:
        raise KtcParseError("table_header_not_found")
    if previous_balance is None or new_balance is None:
        raise KtcParseError("balance_rows_not_found")
    if previous_balance + sum(line["amount"] for line in lines) != new_balance:
        raise KtcParseError("balance_check_failed")

    return {
        "statement": {
            "statement_date": _to_date(summary["CLOSING DATE"]),
            "due_date": _to_date(summary["PAYMENT DUE DATE"]),
            "card_masked": card.group(),
            "card_last4": card.group()[-4:],
            "previous_balance": previous_balance,
            "total_new_balance": new_balance,
            "minimum_payment": _to_amount(summary["MINIMUM PAYMENT"]) if AMOUNT_RE.match(summary.get("MINIMUM PAYMENT", "")) else None,
        },
        "lines": lines,
    }
