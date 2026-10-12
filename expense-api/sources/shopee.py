"""Shopee order emails -> one Order (ledger.ecommerce_orders). Text extraction lives in parsers/marketplace.py."""
from parsers.marketplace import LayoutError, parse_shopee
from sources.base import ParseContext, ParseResult, RawEmail

SOURCE = "shopee_email"
PARSER_VERSION = "shopee-1"


def parse(email: RawEmail, ctx: ParseContext) -> ParseResult:
    try:
        order = parse_shopee(email.html_body)
    except LayoutError as e:
        return ParseResult(status="failed", error=f"Shopee email did not match the expected layout: {e}")
    if order is None:
        return ParseResult(status="skipped", error="not a Shopee order email")
    return ParseResult(status="parsed", orders=[order])
