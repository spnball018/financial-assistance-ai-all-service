import type { Account, AccountInput, ApiToken, Balance, Category, CreatedApiToken, EmailSource, GmailStatus, Me, MerchantRule, User, Receipt, TransferMatch, ConfirmResult, Status, Summary, SyncResult, Txn } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** App listens for this and shows the sign-in page (session expired or signed out elsewhere). */
export const AUTH_REQUIRED = "auth-required";

/** Every request goes through here: X-Requested-With is the CSRF guard expense-api requires for writes. */
async function send(path: string, init?: RequestInit): Promise<Response> {
  const json = typeof init?.body === "string";
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "X-Requested-With": "fetch", ...(json ? { "Content-Type": "application/json" } : {}), ...(init?.headers ?? {}) },
  });
  if (res.status === 401 && path !== "/auth/me") window.dispatchEvent(new Event(AUTH_REQUIRED));
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
  return res;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  return (await send(path, init)).json() as Promise<T>;
}

async function noContent(path: string, init?: RequestInit): Promise<void> {
  await send(path, init);
}

export const api = {
  me: () => call<Me>("/auth/me"),
  logout: () => noContent("/auth/logout", { method: "POST" }),
  tokens: () => call<ApiToken[]>("/auth/tokens"),
  createToken: (name: string, expires_in_days: number | null) =>
    call<CreatedApiToken>("/auth/tokens", { method: "POST", body: JSON.stringify({ name, expires_in_days }) }),
  revokeToken: (id: number) => noContent(`/auth/tokens/${id}`, { method: "DELETE" }),
  gmailStatus: () => call<GmailStatus>("/gmail/status"),
  disconnectGmail: () => noContent("/gmail/connection", { method: "DELETE" }),
  users: () => call<User[]>("/users"),
  addUser: (email: string) => call<User>("/users", { method: "POST", body: JSON.stringify({ email }) }),
  updateUser: (id: number, body: Partial<Pick<User, "role" | "is_active">>) =>
    call<User>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  categories: () => call<Category[]>("/categories"),
  accounts: () => call<Account[]>("/accounts"),
  balances: () => call<Balance[]>("/accounts/balances"),
  setOpening: (id: number, body: { amount: string; as_of: string }) =>
    call<{ amount: number; as_of: string }>(`/accounts/${id}/opening-balance`, { method: "PUT", body: JSON.stringify(body) }),
  deleteOpening: (id: number) => noContent(`/accounts/${id}/opening-balance`, { method: "DELETE" }),
  emailSources: () => call<EmailSource[]>("/email-sources"),
  updateEmailSource: (s: EmailSource) =>
    call<EmailSource>(`/email-sources/${s.source}`, {
      method: "PUT",
      body: JSON.stringify({ gmail_label: s.gmail_label, sender_filter: s.sender_filter, account_id: s.account_id, is_active: s.is_active }),
    }),
  ktcPasswordStatus: () => call<{ is_set: boolean; updated_at: string | null }>("/settings/ktc-password/status"),
  setKtcPassword: (password: string) =>
    noContent("/settings/ktc-password", { method: "PUT", body: JSON.stringify({ password }) }),
  deleteKtcPassword: () => noContent("/settings/ktc-password", { method: "DELETE" }),
  transferMatches: () => call<TransferMatch[]>("/reconcile/transfer-matches"),
  linkTransfer: (income_id: number, transfer_id: number) =>
    call<TransferMatch>("/reconcile/transfer-matches", { method: "POST", body: JSON.stringify({ income_id, transfer_id }) }),
  rules: () => call<MerchantRule[]>("/merchant-rules"),
  deleteRule: (key: string) => noContent(`/merchant-rules?merchant_key=${encodeURIComponent(key)}`, { method: "DELETE" }),
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
  uploadReceipt: (txnId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<Receipt>(`/transactions/${txnId}/receipts`, { method: "POST", body: form });
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
