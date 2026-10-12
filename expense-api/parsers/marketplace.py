"""Shopee and Lazada order emails (HTML only) -> one Order. Regex over the visible text, no LLM.

Shopee: "ถูกจัดส่งแล้ว" and "ได้รับรายการสินค้า ... แล้วหรือยัง?" both carry the full order.
Lazada: "ยืนยันคำสั่งซื้อ" (has the order time) and "ได้รับการจัดส่งเรียบร้อยแล้ว" (no order time).
Both list items at unit price x quantity, then the order's adjustments down to the amount paid.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from typing import Literal, Optional

BKK = timezone(timedelta(hours=7))

LineKind = Literal["item", "shipping", "discount", "coin", "other"]


@dataclass
class OrderLine:
    kind: LineKind
    name: str
    amount: Decimal  # signed: discounts and coins are negative
    quantity: int = 1
    variant: Optional[str] = None


@dataclass
class Order:
    platform: Literal["shopee", "lazada"]
    order_id: str
    ordered_at: datetime
    ordered_at_exact: bool  # False: Lazada shipped email, the email's time stands in
    total: Decimal
    sellers: list[str]
    lines: list[OrderLine] = field(default_factory=list)
    payment_method: Optional[str] = None


class LayoutError(ValueError):
    """The email looks like an order but did not add up; the message names what was missing."""


class _Text(HTMLParser):
    _BLOCK = {"br", "tr", "p", "div", "td", "li", "h1", "h2", "h3", "table"}
    _SKIP = {"style", "script", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        if tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.skip:
            self.skip -= 1
        if tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_lines(html: str) -> list[str]:
    p = _Text()
    p.feed(html or "")
    text = unescape("".join(p.out)).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


def _money(s: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.\-]", "", s))


_THAI_MONTHS = {m: i + 1 for i, m in enumerate(
    ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
     "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"])}
_THAI_MONTHS.update({m: i + 1 for i, m in enumerate(
    ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."])})


def _date(day: str, month: int, year: str, hh: str, mm: str, ss: str = "0") -> datetime:
    y = int(year)
    if y > 2400:  # Buddhist Era
        y -= 543
    return datetime(y, month, int(day), int(hh), int(mm), int(ss), tzinfo=BKK)


def _thai_date(s: str) -> Optional[datetime]:
    """'15/07/2026 10:52:24', '21 ก.ค. 2026 17:29:46' or '04 สิงหาคม 2569 เวลา 00:40'."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return _date(m[1], int(m[2]), m[3], m[4], m[5], m[6] or "0")
    m = re.search(r"(\d{1,2}) (\S+) (\d{4})(?: เวลา)? (\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m and m[2] in _THAI_MONTHS:
        return _date(m[1], _THAI_MONTHS[m[2]], m[3], m[4], m[5], m[6] or "0")
    return None


def _after(lines: list[str], label: str, start: int = 0) -> Optional[str]:
    for i in range(start, len(lines) - 1):
        if lines[i] == label:
            return lines[i + 1]
    return None


def _balance(order: Order) -> Order:
    """Items already matched the subtotal; a gap after the listed adjustments is a discount the email
    does not show (seen on Shopee: shipping paid by a voucher). Keep it as its own line."""
    gap = order.total - sum(ln.amount for ln in order.lines)
    if gap:
        order.lines.append(OrderLine("other", "ส่วนต่างที่อีเมลไม่ได้แจ้ง", gap))
    return order


# ── Shopee ───────────────────────────────────────────────────────────────

_SHOPEE_ADJ: list[tuple[str, LineKind, int]] = [  # label contains, kind, sign
    ("ส่วนลด", "discount", -1),
    ("ค่าจัดส่ง", "shipping", 1),
    ("Coins", "coin", -1),
    ("เหรียญ", "coin", -1),
]


def parse_shopee(html: str) -> Optional[Order]:
    """None when the email is not an order summary (promotions, chat, ...)."""
    lines = html_lines(html)
    if "หมายเลขคำสั่งซื้อ:" not in lines or "ยอดที่ต้องชำระทั้งหมด:" not in lines:
        return None
    order_id = (_after(lines, "หมายเลขคำสั่งซื้อ:") or "").lstrip("#")
    ordered_at = _thai_date(_after(lines, "วันที่สั่งซื้อ:") or "")
    seller = _after(lines, "ผู้ขาย:")
    if not order_id or ordered_at is None or seller is None:
        raise LayoutError("order id, order date or seller missing")

    start = lines.index("ผู้ขาย:") + 2
    end = lines.index("ยอดรวมค่าสินค้า:")
    items: list[OrderLine] = []
    i = start
    while i < end:
        m = re.match(r"^(\d+)\.\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        name = m[2] or lines[i + 1]
        i += 1 if m[2] else 2
        variant = None
        qty, price = 1, None
        while i < end and not re.match(r"^\d+\.(\s|$)", lines[i]):
            if lines[i] == "ตัวเลือกสินค้า:" and lines[i + 1] not in ("จำนวน:", "ราคา:"):
                variant = lines[i + 1]
            elif lines[i] == "จำนวน:":
                qty = int(lines[i + 1])
            elif lines[i] == "ราคา:":
                price = _money(lines[i + 1])
            i += 1
        if price is None:
            raise LayoutError(f"no price for item {m[1]}")
        items.append(OrderLine("item", name, price * qty, qty, variant))

    subtotal = _money(lines[end + 1])
    if sum(x.amount for x in items) != subtotal:
        raise LayoutError(f"items add up to {sum(x.amount for x in items)}, subtotal is {subtotal}")
    total_at = lines.index("ยอดที่ต้องชำระทั้งหมด:")
    adjustments: list[OrderLine] = []
    for j in range(end + 2, total_at, 1):
        label, value = lines[j], lines[j + 1] if j + 1 < len(lines) else ""
        if not label.endswith(":") or not value.startswith("฿"):
            continue  # e.g. the voucher code that follows each discount amount
        for key, kind, sign in _SHOPEE_ADJ:
            if key in label:
                adjustments.append(OrderLine(kind, label.rstrip(":"), sign * _money(value)))
                break
        else:
            raise LayoutError(f"unknown adjustment: {label}")
    order = Order("shopee", order_id, ordered_at, True, _money(lines[total_at + 1]), [seller],
                  items + [a for a in adjustments if a.amount])
    return _balance(order)


# ── Lazada ───────────────────────────────────────────────────────────────

_LAZADA_ADJ: dict[str, tuple[LineKind, int]] = {
    "ค่าธรรมเนียมจัดส่ง:": ("shipping", 1),
    "คูปองส่วนลด:": ("discount", -1),
    "Service fee:": ("other", 1),
}
_LAZADA_NAMES = {"shipping": "ค่าจัดส่ง", "discount": "คูปองส่วนลด", "other": "Service fee"}
_LAZADA_TOTAL = "ยอดรวมทั้งหมด(รวม VAT):"


def parse_lazada(html: str, received_at: Optional[datetime]) -> Optional[Order]:
    lines = [ln for ln in html_lines(html) if ln != "THB"]  # amounts sit on the line after a lone "THB"
    if _LAZADA_TOTAL not in lines:
        return None  # e-invoice, security alert, promotions
    body = " ".join(lines)
    m = re.search(r"(?:หมายเลขคำสั่งซื้อ|คำสั่งซื้อหมายเลข) (\d{10,})", body)
    if not m:
        raise LayoutError("order id missing")
    order_id = m[1]
    when = re.search(r"ของคุณเมื่อ (\d{1,2} \S+ \d{4} เวลา \d{1,2}:\d{2})", body)
    ordered_at = _thai_date(when[1]) if when else None
    exact = ordered_at is not None
    if ordered_at is None:
        if received_at is None:
            raise LayoutError("no order time and no email time")
        ordered_at = received_at.astimezone(BKK)

    sellers: list[str] = []
    items: list[OrderLine] = []
    first_total = lines.index("ยอดรวม:")
    for i, ln in enumerate(lines[:first_total]):
        if ln.startswith("จัดจำหน่ายโดย:"):
            sellers.append(ln.split(":", 1)[1].strip())
        p = re.match(r"^THB\s+([\d,]+\.\d{2})$", ln)
        if p and i + 1 < len(lines) and (q := re.match(r"^จำนวน: (\d+)$", lines[i + 1])):
            qty = int(q[1])
            items.append(OrderLine("item", lines[i - 1], _money(p[1]) * qty, qty))
    if not items:
        raise LayoutError("no items found")

    subtotal = _money(lines[first_total + 1])
    if sum(x.amount for x in items) != subtotal:
        raise LayoutError(f"items add up to {sum(x.amount for x in items)}, subtotal is {subtotal}")
    total_at = lines.index(_LAZADA_TOTAL)
    adjustments: list[OrderLine] = []
    for j in range(first_total + 2, total_at):
        if lines[j].endswith(":"):
            if lines[j] not in _LAZADA_ADJ:
                raise LayoutError(f"unknown adjustment: {lines[j]}")
            kind, sign = _LAZADA_ADJ[lines[j]]
            amount = sign * _money(lines[j + 1])
            if amount:
                adjustments.append(OrderLine(kind, _LAZADA_NAMES[kind], amount))
    order = Order("lazada", order_id, ordered_at, exact, _money(lines[total_at + 1]), sellers, items + adjustments,
                  payment_method=_after(lines, "ช่องทางการชำระเงิน:"))
    return _balance(order)
