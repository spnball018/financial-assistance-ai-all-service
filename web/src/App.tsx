import { useEffect, useState } from "react";
import { api } from "./api";
import { BalancesPage } from "./BalancesPage";
import { ReviewPage } from "./ReviewPage";
import { SettingsPage } from "./SettingsPage";
import { SummaryPage } from "./SummaryPage";
import type { Account, Category } from "./types";

type Page = "review" | "summary" | "balances" | "settings";
const PAGES: Page[] = ["review", "summary", "balances", "settings"];

export function App() {
  const [page, setPage] = useState<Page>(() => {
    const h = location.hash.slice(1) as Page;
    return PAGES.includes(h) ? h : "review";
  });
  const [categories, setCategories] = useState<Category[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.categories().then(setCategories, (e: Error) => setError(`ต่อ expense-api ไม่ได้: ${e.message}`));
  }, []);
  // reload on every page switch so accounts edited in Settings show up in the review dropdown
  useEffect(() => {
    api.accounts().then(setAccounts, () => {});
  }, [page]);

  const go = (p: Page) => {
    setPage(p);
    location.hash = p;
  };

  return (
    <div className="app">
      <header>
        <h1>Expense Tracker</h1>
        <nav>
          <button className={page === "review" ? "tab active" : "tab"} onClick={() => go("review")}>
            ตรวจรายการ
          </button>
          <button className={page === "summary" ? "tab active" : "tab"} onClick={() => go("summary")}>
            สรุปรายเดือน
          </button>
          <button className={page === "balances" ? "tab active" : "tab"} onClick={() => go("balances")}>
            ยอดคงเหลือ
          </button>
          <button className={page === "settings" ? "tab active" : "tab"} onClick={() => go("settings")}>
            ตั้งค่า
          </button>
        </nav>
      </header>
      {error && <div className="message error">{error}</div>}
      <main>
        {page === "review" && <ReviewPage categories={categories} accounts={accounts} />}
        {page === "summary" && <SummaryPage />}
        {page === "balances" && <BalancesPage />}
        {page === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
