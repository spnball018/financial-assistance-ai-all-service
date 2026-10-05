import type { Account, AccountInput, Balance, Category, EmailSource, MerchantRule, Receipt, ConfirmResult, Status, Summary, SyncResult, Txn } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  categories: () => call<Category[]>("/categories"),
  accounts: () => call<Account[]>("/accounts"),
  balances: () => call<Balance[]>("/accounts/balances"),
  setOpening: (id: number, body: { amount: string; as_of: string }) =>
    call<{ amount: number; as_of: string }>(`/accounts/${id}/opening-balance`, { method: "PUT", body: JSON.stringify(body) }),
  deleteOpening: (id: number) =>
    fetch(`/api/accounts/${id}/opening-balance`, { method: "DELETE" }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, r.statusText);
    }),
  emailSources: () => call<EmailSource[]>("/email-sources"),
  updateEmailSource: (s: EmailSource) =>
    call<EmailSource>(`/email-sources/${s.source}`, {
      method: "PUT",
      body: JSON.stringify({ gmail_label: s.gmail_label, sender_filter: s.sender_filter, account_id: s.account_id, is_active: s.is_active }),
    }),
  rules: () => call<MerchantRule[]>("/merchant-rules"),
  deleteRule: (key: string) =>
    fetch(`/api/merchant-rules?merchant_key=${encodeURIComponent(key)}`, { method: "DELETE" }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, r.statusText);
    }),
  createAccount: (body: AccountInput) => call<Account>("/accounts", { method: "POST", body: JSON.stringify(body) }),
  updateAccount: (id: number, body: Partial<Account>) =>
    call<Account>(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  transactions: (params: { status?: Status; month?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return call<{ total: number; items: Txn[] }>(`/transactions?${q}`);
  },
  patch: (
    id: number,
    body: Partial<Pick<Txn, "category_id" | "description">> & {
      transfer_account_id?: number;
      status?: "pending" | "ignored";
      remember?: boolean;
    },
  ) =>
    call<Txn>(`/transactions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  uploadReceipt: async (txnId: number, file: File): Promise<Receipt> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/transactions/${txnId}/receipts`, { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : res.statusText);
    return body as Receipt;
  },
  receipts: (txnId: number) => call<Receipt[]>(`/transactions/${txnId}/receipts`),
  rereadReceipt: (id: number) => call<Receipt>(`/receipts/${id}/read`, { method: "POST" }),
  applyReceipt: (id: number, items: { name: string; amount: number; category_id: number | null; confidence: number | null }[]) =>
    call<{ parent_id: number; items: number; adjustment: number }>(`/receipts/${id}/apply`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  undoReceipt: (txnId: number) => call<{ removed: number }>(`/transactions/${txnId}/receipt-items`, { method: "DELETE" }),
  createTransaction: (body: {
    kind: "expense" | "income";
    account_id: number;
    amount: string;
    occurred_at: string;
    has_time: boolean;
    category_id: number;
    merchant: string | null;
    description: string | null;
  }) => call<Txn>("/transactions", { method: "POST", body: JSON.stringify(body) }),
  confirm: (body: { ids: number[] } | { all: true }) =>
    call<ConfirmResult>("/transactions/confirm", { method: "POST", body: JSON.stringify(body) }),
  sync: () => call<SyncResult>("/sync", { method: "POST" }),
  summary: (month: string) => call<Summary>(`/reports/summary?month=${month}`),
};
