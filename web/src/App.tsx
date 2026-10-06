import { useEffect, useState } from "react";
import { api, ApiError, AUTH_REQUIRED } from "./api";
import { GmailBanner } from "./AccessSettings";
import { LoginPage } from "./LoginPage";
import { BalancesPage } from "./BalancesPage";
import { ManualEntryPage } from "./ManualEntryPage";
import { ReviewPage } from "./ReviewPage";
import { SettingsPage } from "./SettingsPage";
import { SummaryPage } from "./SummaryPage";
import type { Account, Category, Me } from "./types";

type Page = "review" | "manual" | "summary" | "balances" | "settings";
const PAGES: Page[] = ["review", "manual", "summary", "balances", "settings"];

export function App() {
  // undefined = still checking, null = signed out
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    api.me().then(setMe, (e: Error) => {
      if (e instanceof ApiError && e.status === 401) setMe(null);
      else setAuthError(`ต่อ expense-api ไม่ได้: ${e.message}`);
    });
    const signedOut = () => setMe(null);
    window.addEventListener(AUTH_REQUIRED, signedOut);
    return () => window.removeEventListener(AUTH_REQUIRED, signedOut);
  }, []);

  if (authError) return <div className="app"><div className="message error">{authError}</div></div>;
  if (me === undefined) return <div className="app muted">กำลังโหลด…</div>;
  if (me === null) return <LoginPage />;
  return <SignedIn me={me} onSignOut={() => setMe(null)} />;
}

function SignedIn({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
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

  // back/forward buttons and links change only the hash
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.slice(1) as Page;
      if (PAGES.includes(h)) setPage(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
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
          <button className={page === "manual" ? "tab active" : "tab"} onClick={() => go("manual")}>
            บันทึกเอง
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
        <div className="spacer" />
        <div className="who small">
          <span className="muted" title={me.email}>{me.name ?? me.email}</span>
          {me.auth_mode === "google" && (
            <button className="link" onClick={() => api.logout().finally(onSignOut)}>
              ออกจากระบบ
            </button>
          )}
        </div>
      </header>
      {error && <div className="message error">{error}</div>}
      {me.auth_mode === "google" && <GmailBanner page={page} />}
      <main>
        {page === "review" && <ReviewPage categories={categories} accounts={accounts} />}
        {page === "manual" && <ManualEntryPage categories={categories} accounts={accounts} />}
        {page === "summary" && <SummaryPage />}
        {page === "balances" && <BalancesPage />}
        {page === "settings" && <SettingsPage me={me} />}
      </main>
    </div>
  );
}
