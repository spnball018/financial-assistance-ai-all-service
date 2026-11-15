"""The consent page of the MCP connector sign-in (app/oauth.py). The browser reaches it as /api/oauth/consent,
sent there by the SDK's /authorize. Signed out -> Google sign-in first, then back here. Approve -> a one-time
code to the client's redirect_uri (claude.ai); deny -> error=access_denied.

Public like /auth/* (the person may not be signed in yet); it checks the session cookie itself, and the POST
needs the form's CSRF token, so another site cannot approve on the person's behalf.
"""
import html
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth, oauth

router = APIRouter(prefix="/oauth", tags=["oauth"], include_in_schema=False)


def _person(request: Request) -> Optional[auth.Principal]:
    if auth.auth_mode() == "off":
        return auth.local_principal() if (request.url.hostname or "") in auth.LOCAL_HOSTS else None
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    return auth.session_principal(cookie) if cookie else None


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>เชื่อมต่อ Claude</title>
<style>
 :root {{ color-scheme: light dark; --bg:#f7f7f5; --card:#fff; --text:#1d1f21; --muted:#6a737d; --accent:#2f6f4f; --border:#e3e3e0 }}
 @media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181a; --card:#1f2225; --text:#e8e8e6; --muted:#9aa0a6; --accent:#5fae86; --border:#34373a }} }}
 body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.5 system-ui, sans-serif; }}
 main {{ max-width:420px; margin:10vh auto; padding:24px 16px; background:var(--card); border:1px solid var(--border); border-radius:12px; }}
 h1 {{ font-size:20px; margin:0 0 12px; }} p, li {{ color:var(--muted); }} .who {{ color:var(--text); }}
 form {{ display:flex; gap:8px; margin-top:20px; }}
 button {{ flex:1; font:inherit; padding:10px; border-radius:8px; border:1px solid var(--border); background:transparent; color:var(--text); cursor:pointer; }}
 button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
</style></head><body><main>{body}</main></body></html>""", status_code=status)


@router.get("/consent")
def consent(request: Request, req: str = ""):
    ask = oauth.read_consent_request(req)
    if ask is None:
        return _page("<h1>ลิงก์หมดอายุ</h1><p>กลับไปที่ Claude แล้วกดเชื่อมต่อใหม่อีกครั้ง</p>", 400)
    person = _person(request)
    if person is None:
        back = "/api/oauth/consent?req=" + quote(req, safe="")
        return RedirectResponse("/api/auth/login?next=" + quote(back, safe=""), status_code=303)
    name = html.escape(ask.get("client_name") or "Claude")
    return _page(f"""<h1>ให้ {name} อ่านข้อมูลการเงินของคุณ?</h1>
<p>บัญชี <span class="who">{html.escape(person.email)}</span></p>
<ul><li>อ่านรายการ ยอดคงเหลือ สรุปรายเดือน และพอร์ตกองทุน</li>
<li><b>แก้ไขหรือลบอะไรไม่ได้</b></li>
<li>ยกเลิกได้ทุกเมื่อในหน้าตั้งค่า → การเข้าถึง</li></ul>
<form method="post" action="/api/oauth/consent">
 <input type="hidden" name="req" value="{html.escape(req)}">
 <input type="hidden" name="csrf" value="{html.escape(oauth.csrf_token(req, person.user_id))}">
 <button name="decision" value="deny">ไม่อนุญาต</button>
 <button class="primary" name="decision" value="allow">อนุญาต</button>
</form>""")


@router.post("/consent")
def decide(request: Request, req: str = Form(""), csrf: str = Form(""), decision: str = Form("")):
    ask = oauth.read_consent_request(req)
    person = _person(request)
    if ask is None or person is None or not oauth.check_csrf(csrf, req, person.user_id):
        return _page("<h1>ทำรายการไม่สำเร็จ</h1><p>กลับไปที่ Claude แล้วกดเชื่อมต่อใหม่อีกครั้ง</p>", 400)
    if decision != "allow":
        return RedirectResponse(oauth.redirect_with(ask["redirect_uri"], error="access_denied", state=ask["state"]),
                                status_code=303)
    code = oauth.issue_code(ask, person.user_id)
    return RedirectResponse(oauth.redirect_with(ask["redirect_uri"], code=code, state=ask["state"]), status_code=303)
