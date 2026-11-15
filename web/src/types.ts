export type Status = "pending" | "confirmed" | "ignored";
export type CategorySource = "none" | "memory" | "ai" | "user" | "manual" | "account";
export type MemoryAction = "category" | "transfer" | "ask";

export interface Txn {
  id: number;
  parent_id: number | null;
  account_id: number;
  counter_account_id: number | null;
  node_type: string;
  kind: "expense" | "income" | "transfer" | "opening";
  status: Status;
  needs_review: boolean;
  amount: number;
  currency: string;
  occurred_at: string;
  has_time: boolean;
  source: string;
  channel: string | null;
  merchant_raw: string | null;
  merchant_key: string | null;
  to_account: string | null;
  memory_action: MemoryAction | null;
  receipt_id: number | null;
  ref_transaction_id: number | null;
  receipts: number;
  description: string | null;
  note: string | null;
  category_id: number | null;
  category_source: CategorySource;
  ai_confidence: number | null;
  ai_reason: string | null;
  confirmed_at: string | null;
  children: Txn[];
}

export interface TxnDetails extends Omit<Txn, "children"> {
  raw: Record<string, unknown> | null;
  account_name: string;
  counter_account_name: string | null;
  category_th: string | null;
  source_ref: string | null;
  dedup_key: string;
  has_children: boolean;
  ancestors: { id: number; node_type: string; amount: number; merchant_raw: string | null; description: string | null }[];
  email: { id: number; source: string; subject: string | null; sender: string | null; received_at: string | null; has_html: boolean } | null;
  orders: { platform: string; order_id: string; ordered_at: string; total_amount: number; sellers: string[] | null; payment_method: string | null }[];
}

export type WalletEntryKind = "payment" | "top_up" | "transfer_out" | "transfer_in" | "refund" | "cashback" | "other";

export interface WalletEntry {
  occurred_at: string; // "YYYY-MM-DD HH:MM", Thai time
  title: string;
  amount: number;
  direction: "out" | "in";
  kind: WalletEntryKind;
  date_shown: boolean; // false: above the first date header, the date is a guess
  uncertain: boolean; // glare or the screen edge hid part of the amount or time
}

export interface WalletScreenshot {
  id: number;
  account_id: number;
  uploaded_at: string;
  ocr_status: "pending" | "done" | "failed";
  ocr_result: { entries: WalletEntry[]; balance: number | null } | null;
  ocr_error: string | null;
  /** while ocr_status is pending: where the background reading is */
  ocr_progress: {
    stage: "queued" | "reading" | "retry" | "saving";
    since: string;
    attempt?: number;
    attempts?: number;
    wait?: number;
    reason?: "rate_limit" | "server" | "timeout";
  } | null;
  applied_at: string | null;
}

export interface WalletApplyResult {
  new: number;
  duplicate: number;
  top_ups: { occurred_at: string; amount: number; status: "linked" | "already" | "not_found" | "ambiguous" | "confirmed"; transaction_id: number | null }[];
}

export interface ReportExpense {
  id: number;
  occurred_at: string;
  has_time: boolean;
  amount: number;
  merchant_raw: string | null;
  description: string | null;
  note: string | null;
  node_type: string;
  category_id: number;
  category_th: string;
  account_name: string;
  path: string[];
}

export interface ReportIncome {
  id: number;
  occurred_at: string;
  has_time: boolean;
  amount: number;
  merchant_raw: string | null;
  description: string | null;
  note: string | null;
  source: string;
  category_id: number | null;
  category_th: string | null;
  account_name: string;
}

export interface Category {
  id: number;
  parent_id: number | null;
  name_th: string;
  name_en: string;
  kind: string;
  sort_order: number;
}

export type AccountType = "bank" | "credit_card" | "ewallet" | "cash" | "investment";

export interface Account {
  id: number;
  name: string;
  type: AccountType;
  institution: string | null;
  masked_ref: string | null;
  currency: string;
  is_active: boolean;
  purpose: string | null;
  sort_order: number;
}

export type AccountInput = Pick<Account, "name" | "type" | "institution" | "masked_ref" | "purpose">;

export interface SummaryGroup {
  category_id: number;
  name_th: string;
  name_en: string;
  count: number;
  amount: number;
  subcategories: Omit<SummaryGroup, "subcategories">[];
}

export interface Summary {
  month: string;
  total: number;
  count: number;
  pending_not_counted: number;
  categories: SummaryGroup[];
  transfers_out: { account_id: number; name: string; count: number; amount: number }[];
  income: { total: number; categories: { category_id: number | null; name_th: string | null; count: number; amount: number }[] };
  net: number;
}

export interface ConfirmResult {
  confirmed: number;
  remembered: number;
  remembered_transfers: number;
  applied_to_pending: number;
}

export interface SyncResult {
  run_id: number;
  counts: Record<string, { fetched: number; new: number; duplicate: number; skipped: number; failed: number; error?: string }>;
  categorize?: { transfer?: number; memory?: number; ai?: number; uncategorized?: number; left?: number; error?: string };
}

export interface Balance {
  account_id: number;
  name: string;
  type: AccountType;
  is_active: boolean;
  balance: number;
  balance_with_pending: number;
  pending_count: number;
  last_movement_at: string | null;
  opening: { amount: number; as_of: string } | null;
  bank_reported: { balance: number; as_of: string; computed: number; difference: number } | null;
}

export interface LedgerRow {
  transaction_id: number;
  status: Status;
  occurred_at: string;
  has_time: boolean;
  kind: "expense" | "income" | "transfer" | "opening";
  node_type: string;
  source: string;
  amount: number; // signed: money out of the account is negative
  counted: boolean; // false: ignored, or before the opening balance day
  merchant_raw: string | null;
  description: string | null;
  note: string | null;
  channel: string | null;
  category_th: string | null;
  other_account: string | null; // the other side of a transfer
  balance: number; // running, confirmed only
  balance_with_pending: number;
  bank_balance: number | null; // "Available Balance" in the K PLUS email
  difference: number | null; // bank_balance - balance_with_pending
  gap_starts: boolean;
}

export interface AccountLedger {
  id: number;
  name: string;
  type: AccountType;
  is_active: boolean;
  opening: { amount: number; as_of: string } | null;
  rows: LedgerRow[];
}

export interface EmailSource {
  id: number;
  source: "kbank_email" | "ktc_pdf" | "shopee_email" | "lazada_email";
  gmail_label: string | null;
  sender_filter: string | null;
  account_id: number | null;
  is_active: boolean;
}

export interface MerchantRule {
  merchant_key: string;
  action: MemoryAction;
  category_id: number | null;
  category_name: string | null;
  counter_account_id: number | null;
  account_name: string | null;
  hit_count: number;
  last_confirmed_at: string;
  transactions: number;
}

export interface Receipt {
  id: number;
  transaction_id: number | null;
  mime_type: string;
  uploaded_at: string;
  ocr_status: "pending" | "done" | "failed";
  ocr_model: string | null;
  ocr_error: string | null;
  applied_at: string | null;
  ocr_result: {
    merchant: string;
    total: number;
    items: { name: string; quantity: number; amount: number; category_id: number; confidence: number }[];
    adjustments: { name: string; amount: number }[];
  } | null;
  payment_amount: number | null;
  items_total: number;
  difference: number | null;
}

export interface TransferMatch {
  income_id: number;
  account_id: number;
  account_name: string;
  amount: number;
  income_at: string;
  income_from: string | null;
  transfer_id: number;
  from_account_id: number;
  from_account_name: string;
  transfer_at: string;
  transfer_status: string;
}

export interface Me {
  id: number;
  email: string;
  name: string | null;
  role: "admin" | "member";
  via: "session" | "token" | "off";
  auth_mode: "google" | "off";
}

export interface User {
  id: number;
  email: string;
  name: string | null;
  role: "admin" | "member";
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ApiToken {
  id: number;
  name: string;
  token_prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** A Claude connector (MCP) the user approved; it can read, never change. */
export interface McpConnection {
  grant_id: string;
  client_id: string;
  client_name: string | null;
  connected_at: string;
  last_used_at: string | null;
}

/** Only the create response carries the token itself. */
export interface CreatedApiToken extends ApiToken {
  token: string;
}

export interface GmailStatus {
  connected: boolean;
  /** user 1 without a connection still reads gmail-service's token.json */
  uses_token_json: boolean;
  gmail_address?: string;
  connected_at?: string;
  last_used_at?: string | null;
  last_error?: "expired" | "secret_key" | null;
}

export interface FundPosition {
  id: number;
  account_id: number;
  code: string;
  name: string | null;
  is_active: boolean;
  units: number;
  cost: number;
  avg_cost: number | null;
  /** latest NAV: fetched from Finnomena, typed in by the user, or the last trade's when that is newer */
  nav: number | null;
  nav_date: string | null;
  nav_source: "finnomena" | "user" | "trade" | null;
  market_value: number | null;
  unrealized: number | null;
  realized: number;
  trades: number;
}

export interface FundAccount {
  account_id: number;
  name: string;
  institution: string | null;
  is_active: boolean;
  /** confirmed transfers in minus out (what the balances page shows) */
  net_deposits: number;
  cost: number;
  market_value: number;
  unrealized: number;
  realized: number;
  /** transfers into or out of the account that no trade points at yet */
  unlinked_transfers: number;
  funds: FundPosition[];
}

/** new_transfer: made with the trade · linked_transfer: an existing one · held_before: no money moved · switch */
export type FundFunding = "new_transfer" | "linked_transfer" | "held_before" | "switch";

export interface FundTrade {
  id: number;
  fund_id: number;
  fund_code: string;
  side: "buy" | "sell";
  trade_date: string;
  units: number;
  amount: number;
  nav: number;
  funding: FundFunding;
  transaction_id: number | null;
  switched_from: number | null;
  note: string | null;
  transfer_status: Status | null;
  transfer_at: string | null;
  /** the bank account the money came from (buy) or went to (sell) */
  other_account: string | null;
  /** the other fund of a switch */
  switch_fund_code: string | null;
}

export interface UnlinkedTransfer {
  transaction_id: number;
  occurred_at: string;
  has_time: boolean;
  status: Status;
  /** + into the fund account, - out of it */
  amount: number;
  other_account: string;
  description: string | null;
}

export interface FundTradeInput {
  /** the fund: fund_id, or account_id + fund_code (a new code is added to the account) */
  fund_id: number | null;
  account_id?: number;
  fund_code?: string;
  side: "buy" | "sell";
  trade_date: string;
  units: string;
  amount: string;
  nav: string | null;
  counter_account_id: number | null;
  transaction_id: number | null;
  held_before: boolean;
  note: string | null;
}

export interface FundSwitchInput {
  from_fund_id: number;
  to_fund_id: number;
  trade_date: string;
  amount: string;
  units_out: string;
  units_in: string;
  note: string | null;
}

export interface NavRefresh {
  updated: { fund_id: number; code: string; nav: number; nav_date: string }[];
  failed: { fund_id: number; code: string; error: string }[];
}

export interface NavSync {
  codes: number;
  navs_saved: number;
  funds_updated: number;
  failed: { code: string; error: string }[];
}

export interface FundValuePoint {
  date: string;
  value: number;
  cost: number;
  by_fund: Record<string, number>;
}

export interface FundValueHistory {
  account_id: number;
  name: string;
  funds: string[];
  points: FundValuePoint[];
}

export interface TradeNav {
  code: string;
  date: string;
  nav: number;
}
