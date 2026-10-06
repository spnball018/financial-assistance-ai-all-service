const ERRORS: Record<string, string> = {
  not_allowed: "อีเมลนี้ยังไม่ได้รับอนุญาต ขอให้ผู้ดูแลเพิ่มอีเมลของคุณในหน้าตั้งค่าก่อน",
  denied: "ยกเลิกการเข้าสู่ระบบ",
  state: "การเข้าสู่ระบบหมดเวลาหรือไม่ถูกต้อง ลองใหม่อีกครั้ง",
  config: "ระบบยังไม่ได้ตั้งค่า Google sign-in (GOOGLE_OAUTH_CLIENT_ID / SECRET_KEY)",
  failed: "เข้าสู่ระบบไม่สำเร็จ ลองใหม่อีกครั้ง",
};

export function LoginPage() {
  const error = new URLSearchParams(location.search).get("login_error");
  // come back to the same tab after signing in
  const next = "/" + location.hash;
  return (
    <div className="login">
      <h1>Expense Tracker</h1>
      {error && <div className="message error">{ERRORS[error] ?? ERRORS.failed}</div>}
      <a className="button primary" href={`/api/auth/login?next=${encodeURIComponent(next)}`}>
        เข้าสู่ระบบด้วย Google
      </a>
    </div>
  );
}
