import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from parsers.marketplace import BKK, LayoutError, html_lines, parse_lazada, parse_shopee
from sources import lazada, shopee
from sources.base import ParseContext, RawEmail

FIXTURES = Path(__file__).parent / "fixtures"


def html(*lines: str) -> str:
    """The real emails are nested tables; one <div> per visible line is enough for the parser."""
    return "<html><head><style>p{}</style></head><body>" + "".join(f"<div>{ln}</div>" for ln in lines) + "</body></html>"


SHOPEE = html(
    "เรียน คุณ someone,", "คำสั่งซื้อหมายเลข #2607219JCGSV15", "รายละเอียดคำสั่งซื้อ",
    "หมายเลขคำสั่งซื้อ:", "#2607219JCGSV15", "วันที่สั่งซื้อ:", "21 ก.ค. 2026 17:29:46", "ผู้ขาย:", "charmantcc.th",
    "1.  Pry Bar tool set", "ตัวเลือกสินค้า:", "จำนวน:", "2", "ราคา:", "฿54",
    "2.", "Free cleaning cloth", "ตัวเลือกสินค้า:", "blue", "จำนวน:", "1", "ราคา:", "฿0",
    "2607219JCGSV15",
    "ยอดรวมค่าสินค้า:", "฿108",
    "โค้ดส่วนลดของ Shopee:", "฿16", "โค้ดส่วนลดของ Shopee:", "HBDPLATJUL26",
    "โค้ดส่วนลดร้านค้า:", "฿4", "โค้ดส่วนลดร้านค้า:", "CHAR2780A",
    "ค่าจัดส่งสินค้า:", "฿29",
    "ยอดที่ต้องชำระทั้งหมด:", "฿117", "คำถามที่พบบ่อย")


def lazada_html(intro: str) -> str:
    return html(
        "ขอบคุณที่ช้อปกับลาซาด้า", intro, "การจัดส่ง 1", "จัดจำหน่ายโดย: MKP Racing",
        "Front basket for Mio", "THB&nbsp; 145.00", "จำนวน: 1",
        "การจัดส่ง 2", "จัดจำหน่ายโดย: Tanmonkey coffee", "Decaf beans 200g", "THB 290.00", "จำนวน: 2",
        "ยอดรวม:", "THB", "725.00", "ค่าธรรมเนียมจัดส่ง:", "THB", "64.00", "คูปองส่วนลด:", "THB", "32.00",
        "Service fee:", "THB", "0.00", "ยอดรวมทั้งหมด(รวม VAT):", "THB", "757.00",
        "วิธีการจัดส่ง:", "STANDARD", "ช่องทางการชำระเงิน:", "VIRTUAL_ACCOUNT_DEEPLINK_KBANK", "อย่าลืมซื้อสินค้านี้")


LAZADA_CONFIRMED = lazada_html("เราได้รับหมายเลขคำสั่งซื้อ 1110704249637288 ของคุณเมื่อ 04 สิงหาคม 2569 เวลา 00:40"
                               "ผ่านช่องทาง บัตรเครดิต/เดบิต แล้ว!")
LAZADA_SHIPPED = lazada_html("เราขอยืนยันว่าคำสั่งซื้อหมายเลข 1110704249637288 ของคุณได้รับการจัดส่งเรียบร้อยแล้ว")


def test_html_lines_drops_styles_and_entities():
    assert html_lines("<style>.x{}</style><div>a&nbsp;b</div><p>  c  </p>") == ["a b", "c"]


def test_shopee_order_with_discounts_and_free_item():
    o = parse_shopee(SHOPEE)
    assert (o.platform, o.order_id, o.sellers, o.total) == ("shopee", "2607219JCGSV15", ["charmantcc.th"], Decimal("117"))
    assert o.ordered_at == datetime(2026, 7, 21, 17, 29, 46, tzinfo=BKK) and o.ordered_at_exact
    assert [(ln.kind, ln.name, ln.amount, ln.quantity) for ln in o.lines] == [
        ("item", "Pry Bar tool set", Decimal("108"), 2),
        ("item", "Free cleaning cloth", Decimal("0"), 1),
        ("discount", "โค้ดส่วนลดของ Shopee", Decimal("-16"), 1),
        ("discount", "โค้ดส่วนลดร้านค้า", Decimal("-4"), 1),
        ("shipping", "ค่าจัดส่งสินค้า", Decimal("29"), 1),
    ]
    assert o.lines[1].variant == "blue" and o.lines[0].variant is None


def test_shopee_numeric_date_and_unexplained_gap():
    o = parse_shopee(SHOPEE.replace("21 ก.ค. 2026 17:29:46", "21/07/2026 17:29:46").replace("฿117", "฿100"))
    assert o.ordered_at == datetime(2026, 7, 21, 17, 29, 46, tzinfo=BKK)
    assert (o.lines[-1].kind, o.lines[-1].amount) == ("other", Decimal("-17"))
    assert sum(ln.amount for ln in o.lines) == o.total


def test_shopee_rejects_items_that_do_not_match_subtotal_and_unknown_lines():
    with pytest.raises(LayoutError):
        parse_shopee(SHOPEE.replace("<div>฿108</div>", "<div>฿999</div>"))
    with pytest.raises(LayoutError, match="unknown adjustment"):
        parse_shopee(SHOPEE.replace("ค่าจัดส่งสินค้า:", "ค่าบริการพิเศษ:"))


def test_shopee_non_order_email_is_none():
    assert parse_shopee(html("Flash sale 10.10", "ช้อปเลย")) is None


def test_lazada_confirmation_has_exact_time_and_all_shipments():
    o = parse_lazada(LAZADA_CONFIRMED, None)
    assert (o.order_id, o.total, o.sellers) == ("1110704249637288", Decimal("757.00"), ["MKP Racing", "Tanmonkey coffee"])
    assert o.ordered_at == datetime(2026, 8, 4, 0, 40, tzinfo=BKK) and o.ordered_at_exact
    assert o.payment_method == "VIRTUAL_ACCOUNT_DEEPLINK_KBANK"
    assert [(ln.kind, ln.name, ln.amount, ln.quantity) for ln in o.lines] == [
        ("item", "Front basket for Mio", Decimal("145.00"), 1),
        ("item", "Decaf beans 200g", Decimal("580.00"), 2),
        ("shipping", "ค่าจัดส่ง", Decimal("64.00"), 1),
        ("discount", "คูปองส่วนลด", Decimal("-32.00"), 1),
    ]


def test_lazada_shipped_email_uses_email_time():
    received = datetime(2026, 8, 5, 5, 4, tzinfo=timezone.utc)
    o = parse_lazada(LAZADA_SHIPPED, received)
    assert o.order_id == "1110704249637288" and not o.ordered_at_exact and o.ordered_at == received


def test_lazada_einvoice_is_none():
    assert parse_lazada(html("E-invoice", "ตามคำสั่งซื้อหมายเลข: 1110271080737288"), None) is None


def _email(body: str) -> RawEmail:
    return RawEmail(1, "m1", "s", "x@y", datetime(2026, 8, 5, tzinfo=timezone.utc), "", body, [])


CTX = ParseContext(account_id=None, get_attachment=lambda _: b"", get_setting=lambda _: None)


def test_sources_return_orders_not_transactions():
    r = shopee.parse(_email(SHOPEE), CTX)
    assert r.status == "parsed" and not r.nodes and r.orders[0].order_id == "2607219JCGSV15"
    assert lazada.parse(_email(LAZADA_SHIPPED), CTX).orders[0].platform == "lazada"
    assert shopee.parse(_email(html("promo")), CTX).status == "skipped"
    assert shopee.parse(_email(SHOPEE.replace("<div>฿108</div>", "<div>฿1</div>")), CTX).status == "failed"


def test_all_fixtures_parse():
    files = sorted(FIXTURES.glob("shopee_*.json")) + sorted(FIXTURES.glob("lazada_*.json"))
    if not files:
        pytest.skip("no fixtures")
    for f in files:
        d = json.loads(f.read_text())
        received = datetime.fromisoformat(d["received_at"])
        o = parse_shopee(d["html_body"]) if f.name.startswith("shopee") else parse_lazada(d["html_body"], received)
        if o is not None:
            assert sum(ln.amount for ln in o.lines) == o.total, f.name
