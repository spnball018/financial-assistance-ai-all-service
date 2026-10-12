"""Lazada order emails -> one Order (ledger.ecommerce_orders). Text extraction lives in parsers/marketplace.py."""
from parsers.marketplace import LayoutError, parse_lazada
from sources.base import ParseContext, ParseResult, RawEmail

SOURCE = "lazada_email"
PARSER_VERSION = "lazada-1"


def parse(email: RawEmail, ctx: ParseContext) -> ParseResult:
    try:
        order = parse_lazada(email.html_body, email.received_at)
    except LayoutError as e:
        return ParseResult(status="failed", error=f"Lazada email did not match the expected layout: {e}")
    if order is None:
        return ParseResult(status="skipped", error="not a Lazada order email (e-invoice, alert, promotion)")
    return ParseResult(status="parsed", orders=[order])
