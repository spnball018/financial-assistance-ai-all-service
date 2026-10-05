import { useEffect, useState } from "react";
import { api } from "./api";
import { ReviewPage } from "./ReviewPage";
import { SettingsPage } from "./SettingsPage";
import { SummaryPage } from "./SummaryPage";
import type { Category } from "./types";

type Page = "review" | "summary" | "settings";

export function App() {
  const [page, setPage] = useState<Page>(() => {
    const h = location.hash.slice(1);
    return h === "summary" || h === "settings" ? h : "review";
  });
  const [categories, setCategories] = useState<Category[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.categories().then(setCategories, (e: Error) => setError(`ต่อ expense-api ไม่ได้: ${e.message}`));
  }, []);

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
          <button className={page === "settings" ? "tab active" : "tab"} onClick={() => go("settings")}>
            ตั้งค่า
          </button>
        </nav>
      </header>
      {error && <div className="message error">{error}</div>}
      <main>
        {page === "review" && <ReviewPage categories={categories} />}
        {page === "summary" && <SummaryPage />}
        {page === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
