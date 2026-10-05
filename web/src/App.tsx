import { useEffect, useState } from "react";
import { api } from "./api";
import { ReviewPage } from "./ReviewPage";
import { SummaryPage } from "./SummaryPage";
import type { Category } from "./types";

type Page = "review" | "summary";

export function App() {
  const [page, setPage] = useState<Page>(() => (location.hash === "#summary" ? "summary" : "review"));
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
        </nav>
      </header>
      {error && <div className="message error">{error}</div>}
      <main>{page === "review" ? <ReviewPage categories={categories} /> : <SummaryPage />}</main>
    </div>
  );
}
