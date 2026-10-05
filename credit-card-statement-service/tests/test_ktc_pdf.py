"""KTC PDF parser tests. Synthetic PDFs only (no personal data); the real-fixture test skips itself."""
import io
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pikepdf
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

import ktc_pdf
from main import app

PASSWORD = "test-pw-not-real"
REAL_FIXTURE = Path(__file__).resolve().parents[2] / "expense-api/tests/fixtures/ktc_statement_1.pdf"
H = 842


def build_pdf(rows, *, year="26", header="DESCRIPTION", prev="1,000.00", total=None, password=PASSWORD):
    """rows: (trans, posting, description, amount_text, negative); summary grid is drawn with ruling lines."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, H))
    c.setFont("Helvetica", 8)
    cells = [
        ("TH label", "1234-56XX-XXXX-7890", "CARD NUMBER"), ("TH label", "50,000.00", "SHARE CREDIT LIMIT"),
        ("TH label", f"18/09/{year}", "CLOSING DATE"), ("TH label", "0.00", "TOTAL FEE AMT."),
        ("TH label (-) 0.00", None, "TOTAL PAYMENT/OVER PAID (-)"), ("TH label", "0.00", "TOTAL VAT AMT."),
        (f"TH label 03/10/{year}", None, "PAYMENT DUE DATE"), ("TH label", "0.00", "TOTAL FEE INCL.VAT"),
        ("TH label 100.00", None, "MINIMUM PAYMENT"), ("TH label", "XXXXXX-1234", "DEBIT YOUR AD./NO."),
    ]
    for i, (th, value, label) in enumerate(cells):
        x, top = 100 + (i % 2) * 200, 100 + (i // 2) * 40
        c.rect(x, H - top - 40, 200, 40)
        c.drawString(x + 4, H - top - 10, th)
        if value:
            c.drawString(x + 4, H - top - 20, value)
        c.drawString(x + 4, H - top - 30, label)

    def row(y, trans=None, posting=None, desc="", amount=None, negative=False, desc_x=220):
        if trans:
            c.drawString(59, H - y, trans)
            c.drawString(152, H - y, posting)
        c.drawString(desc_x, H - y, desc)
        if amount:
            c.drawString(540 if negative else 546, H - y, "-" if negative else "")
            c.drawString(552, H - y, amount)

    y = 330
    for x, text in ((59, "TRANS."), (152, "POSTING"), (220, header), (500, "AMOUNT"), (540, "(BAHT)")):
        c.drawString(x, H - y, text)
    y += 12
    row(y, desc="PREVIOUS BALANCE", amount=prev)
    running = Decimal(prev.replace(",", ""))
    for trans, posting, desc, amount, negative in rows:
        y += 12
        row(y, trans, posting, desc, amount, negative)
        if desc.startswith("WRAP"):
            y += 12
            row(y, desc="continued text")
        value = Decimal(amount.replace(",", ""))
        running += -value if negative else value
    y += 12
    row(y, desc="NEW BALANCE", amount=total or f"{running:,.2f}")
    c.drawString(38, H - 820, "footer 1/1")
    c.save()
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(buf.getvalue())) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(user=password, owner=password))
    return out.getvalue()


ROWS = [
    ("01/09/26", "02/09/26", "COFFEE SHOP BANGKOK", "120.50", False),
    ("05/09/26", "06/09/26", "WRAP LONG MERCHANT NAME", "1,299.00", False),
    ("10/09/26", "10/09/26", "INTEREST", "15.25", False),
    ("10/09/26", "10/09/26", "VAT ON COLLECTION EXPENSE", "1.07", False),
    ("12/09/26", "12/09/26", "PAYMENT THANK YOU", "1,000.00", True),
    ("14/09/26", "15/09/26", "STORE REFUND", "99.00", True),
]


def test_parses_summary_and_lines():
    r = ktc_pdf.parse_pdf(build_pdf(ROWS), PASSWORD)
    s = r["statement"]
    assert s["statement_date"] == date(2026, 9, 18) and s["due_date"] == date(2026, 10, 3)
    assert s["card_last4"] == "7890" and s["card_masked"] == "1234-56XX-XXXX-7890"
    assert s["previous_balance"] == Decimal("1000.00")
    assert s["minimum_payment"] == Decimal("100.00")
    assert [l["type"] for l in r["lines"]] == ["purchase", "purchase", "interest", "fee", "payment", "refund"]
    assert [l["amount"] for l in r["lines"]][-2:] == [Decimal("-1000.00"), Decimal("-99.00")]
    assert r["lines"][1]["description"] == "WRAP LONG MERCHANT NAME continued text"
    assert r["lines"][0]["posting_date"] == date(2026, 9, 2)
    assert s["total_new_balance"] == s["previous_balance"] + sum(l["amount"] for l in r["lines"])


def test_buddhist_era_two_digit_year():
    rows = [("01/09/69", "02/09/69", "SHOP", "10.00", False)]
    r = ktc_pdf.parse_pdf(build_pdf(rows, year="69"), PASSWORD)
    assert r["statement"]["statement_date"] == date(2026, 9, 18)
    assert r["lines"][0]["transaction_date"] == date(2026, 9, 1)


def test_balance_mismatch_fails_closed():
    with pytest.raises(ktc_pdf.KtcParseError) as e:
        ktc_pdf.parse_pdf(build_pdf(ROWS, total="1.00"), PASSWORD)
    assert e.value.code == "balance_check_failed"


def test_renamed_table_header_fails_closed():
    with pytest.raises(ktc_pdf.KtcParseError) as e:
        ktc_pdf.parse_pdf(build_pdf(ROWS, header="DETAILS"), PASSWORD)
    assert e.value.code == "table_header_not_found"


def test_wrong_password():
    with pytest.raises(ktc_pdf.PasswordRejected):
        ktc_pdf.parse_pdf(build_pdf(ROWS), "nope")


client = TestClient(app)


def test_endpoint_ok_and_errors():
    ok = client.post("/parse-ktc-pdf", files={"file": ("s.pdf", build_pdf(ROWS))}, data={"password": PASSWORD})
    assert ok.status_code == 200
    body = ok.json()
    assert body["lines"][4]["amount"] == "-1000.00" and body["statement"]["card_last4"] == "7890"

    bad_pw = client.post("/parse-ktc-pdf", files={"file": ("s.pdf", build_pdf(ROWS))}, data={"password": "nope"})
    assert bad_pw.status_code == 401 and "nope" not in bad_pw.text

    junk = client.post("/parse-ktc-pdf", files={"file": ("s.pdf", b"not a pdf")}, data={"password": PASSWORD})
    assert junk.status_code == 422 and junk.json()["detail"]["code"] == "unreadable_pdf"


def test_legacy_csv_endpoint_still_works():
    csv = b"Date,Description,Amount\n2026-01-01,Coffee,50.5\n"
    r = client.post("/upload-statement/", files={"file": ("s.csv", csv)})
    assert r.status_code == 200 and r.json()[0]["description"] == "Coffee"


def test_real_statement_passes_balance_check():
    password = os.environ.get("KTC_PDF_PASSWORD")
    if not REAL_FIXTURE.exists() or not password:
        pytest.skip("real KTC fixture or KTC_PDF_PASSWORD not available (gitignored)")
    r = ktc_pdf.parse_pdf(REAL_FIXTURE.read_bytes(), password)
    assert r["lines"] and r["statement"]["card_last4"].isdigit()
