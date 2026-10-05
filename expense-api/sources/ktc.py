"""KTC credit card statements (PDF attached to email). Owned by the KTC track: docs/ktc-track.md"""
from sources.base import ParseContext, ParseResult, RawEmail

SOURCE = "ktc_pdf"
PARSER_VERSION = "ktc-0"


def parse(email: RawEmail, ctx: ParseContext) -> ParseResult:
    return ParseResult(status="skipped", error="KTC parser not implemented yet")
