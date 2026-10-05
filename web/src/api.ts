import type { Account, AccountInput, Balance, Category, ConfirmResult, Status, Summary, SyncResult, Txn } from "./types";

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
  confirm: (body: { ids: number[] } | { all: true }) =>
    call<ConfirmResult>("/transactions/confirm", { method: "POST", body: JSON.stringify(body) }),
  sync: () => call<SyncResult>("/sync", { method: "POST" }),
  summary: (month: string) => call<Summary>(`/reports/summary?month=${month}`),
};
