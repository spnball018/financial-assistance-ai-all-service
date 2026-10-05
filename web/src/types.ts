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
  description: string | null;
  category_id: number | null;
  category_source: CategorySource;
  ai_confidence: number | null;
  ai_reason: string | null;
  confirmed_at: string | null;
  children: Txn[];
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
