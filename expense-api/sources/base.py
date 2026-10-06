"""Contract between the sync engine (sync/) and per-source parsers (sources/<source>.py).

The engine fetches Gmail messages for each active ledger.email_sources row, stores them in
ledger.raw_emails / raw_attachments, then calls the source module's `parse()`. A source module
never touches the database; it only turns one raw email into a tree of TxnNode.

Each source module must expose:
    SOURCE: str            # matches ledger.email_sources.source, e.g. "ktc_pdf"
    PARSER_VERSION: str    # stored in raw_emails.parser_version; bump when output changes
    def parse(email: RawEmail, ctx: ParseContext) -> ParseResult
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Literal, Optional

NodeType = Literal["payment", "statement", "statement_line", "order", "order_item", "adjustment"]
Kind = Literal["expense", "income", "transfer"]


@dataclass
class RawAttachment:
    attachment_id: str
    filename: str
    mime_type: str
    size: int


@dataclass
class RawEmail:
    raw_email_id: int
    gmail_message_id: str
    subject: str
    sender: str
    received_at: Optional[datetime]
    text_body: str
    html_body: str
    attachments: list[RawAttachment]


@dataclass
class ParseContext:
    account_id: Optional[int]  # ledger.email_sources.account_id
    get_attachment: Callable[[str], bytes]  # attachment_id -> original bytes (cached in raw_attachments)
    get_setting: Callable[[str], Optional[str]]  # decrypted ledger.settings value, e.g. "ktc_pdf_password"


@dataclass
class TxnNode:
    """One ledger.transactions row; children become rows with parent_id = this row."""
    node_type: NodeType
    kind: Kind
    amount: Decimal
    occurred_at: datetime  # tz-aware, Asia/Bangkok
    dedup_key: str  # unique per user, e.g. "ktc:stmt:<...>"
    has_time: bool = True
    merchant_raw: Optional[str] = None
    description: Optional[str] = None
    source_ref: Optional[str] = None
    counter_account_id: Optional[int] = None  # required when kind == "transfer"
    raw: dict = field(default_factory=dict)  # parser extras -> transactions.raw (jsonb, JSON-safe values)
    children: list["TxnNode"] = field(default_factory=list)


@dataclass
class ParseResult:
    status: Literal["parsed", "skipped", "failed"]
    nodes: list[TxnNode] = field(default_factory=list)  # roots only
    error: Optional[str] = None  # never include passwords or PDF content
