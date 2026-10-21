import asyncio
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import analysis, chat, db, extract, ingest, mailer, notify, security
from .config import CHART_DIR, DATA_DIR, MAX_UPLOAD_MB, SECRET_KEY, SECRET_SETTINGS, SETUP_TOKEN, SUPERADMIN_EMAIL
from .tpl import render

db.init()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 60, same_site="lax",
                   https_only=os.environ.get("BASE_URL", "").startswith("https"))
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
try:
    VERSION = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).parent).stdout.strip() or "dev"
except Exception:
    VERSION = "dev"


BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|python|headless|wget|httpx", re.I)
SKIP_PREFIXES = ("/static", "/api/", "/c/", "/healthz", "/setup", "/invoice/", "/favicon")


@app.middleware("http")
async def track(request: Request, call_next):
    """Record page views (humans only) and unhandled errors in the events table."""
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as e:
        db.log_event("error", None, None, path=path, error=f"{type(e).__name__}: {e}"[:300])
        raise
    if response.status_code >= 500:
        db.log_event("error", None, None, path=path, status=response.status_code)
    ua = request.headers.get("user-agent", "")
    if (request.method == "GET" and not path.startswith(SKIP_PREFIXES) and response.status_code < 400
            and ua and not BOT_RE.search(ua)):
        ref = request.headers.get("referer", "")
        ref_host = re.sub(r"^https?://([^/]+).*$", r"\1", ref) if ref else ""
        if "secondopinion.co.nz" in ref_host or "sslip.io" in ref_host:
            ref_host = ""
        sess = request.scope.get("session") or {}
        db.log_event("page_view", sess.get("email"), sess.get("sid"), path=path[:120],
                     ref=ref_host[:80] or None, device="mobile" if re.search(r"Mobi|Android|iPhone", ua) else "desktop")
    return response


# ---------- helpers ----------

def sid(request):
    s = request.session.get("sid")
    if not s:
        s = request.session["sid"] = secrets.token_urlsafe(16)
    return s


def current_email(request):
    return request.session.get("email")


def is_admin(request):
    return current_email(request) == SUPERADMIN_EMAIL


def page(request, name, status_code=200, **ctx):
    ctx.update(request=request, user_email=current_email(request), is_admin=is_admin(request),
               mail_address=db.get_setting("mail_address"), version=VERSION)
    return HTMLResponse(render(name, **ctx), status_code)


def require_admin(request):
    if not is_admin(request):
        raise HTTPException(404)


def login(request, email):
    email = email.lower()
    request.session["email"] = email
    ingest.claim_session(request.session.get("sid"), email)
    db.x("UPDATE users SET last_login=? WHERE email=?", (time.time(), email))


def safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") else "/dashboard"


def background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---------- public pages ----------

@app.get("/healthz")
def healthz():
    return {"ok": True, "version": VERSION}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    sid(request)
    return page(request, "index.html")


@app.post("/api/upload")
async def upload(request: Request, files: list[UploadFile] = File(...)):
    email = current_email(request)
    session_id = sid(request)
    sub_id, token = ingest.new_submission("web", email=email, session_id=session_id)
    count = 0
    for f in files[:20]:
        data = await f.read()
        if not data or len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            continue
        ingest.add_file(sub_id, data, f.filename, owner_email=email, session_id=session_id)
        count += 1
    if not count:
        raise HTTPException(400, "No usable files")
    db.log_event("upload", email, session_id, files=count)
    return {"token": token, "redirect": f"/results/{token}"}


def _submission_for(request, token):
    sub = db.q1("SELECT * FROM submissions WHERE token=?", (token,))
    if not sub:
        raise HTTPException(404)
    email = current_email(request)
    if not (is_admin(request) or sub["session_id"] == request.session.get("sid") or (email and sub["email"] == email)):
        raise HTTPException(404)
    return sub


@app.get("/api/submission/{token}")
def submission_status(request: Request, token: str):
    sub = _submission_for(request, token)
    invs = db.q("SELECT id, filename, status, error, carrier, account_name, period_month, total_excl_gst, line_count"
                " FROM invoices WHERE submission_id=? ORDER BY id", (sub["id"],))
    pending = any(i["status"] in ("queued", "processing") for i in invs)
    return {"pending": pending, "invoices": invs}


@app.get("/results/{token}", response_class=HTMLResponse)
def results(request: Request, token: str):
    sub = _submission_for(request, token)
    db.log_event("results_viewed", current_email(request), request.session.get("sid"))
    return page(request, "results.html", token=token, logged_in=bool(current_email(request)), sub=sub)


def _teaser(a):
    """High-level view for people who haven't registered: hide the detail behind the insights."""
    if not a.get("has_data"):
        return a
    for i in a["insights"]:
        i["count"] = len(i["items"])
        i["items"] = []
        i["saving"] = None
    a["kpi"]["savings_month"] = None
    a["kpi"]["savings_year"] = None
    a["devices"] = [{"count": len(a["devices"])}] if a["devices"] else []
    for l in a["lines"]:
        l["flags"] = []
    a["teaser"] = True
    return a


def _clean(a):
    a = dict(a)
    a["invoices"] = [{k: i[k] for k in ("id", "filename", "status", "error", "carrier", "account_name",
                                        "account_number", "period_month", "total_excl_gst", "line_count",
                                        "invoice_number")} for i in a.get("invoices", [])]
    return a


@app.get("/api/analysis")
def api_analysis(request: Request, as_email: str = None, account: str = None):
    email = current_email(request)
    if as_email and is_admin(request):
        email = as_email.lower()
    if email:
        return JSONResponse(_clean(analysis.build(email=email, account=account or None)))
    return JSONResponse(_clean(_teaser(analysis.build(session_id=request.session.get("sid")))))


@app.get("/c/{name}")
def chart_image(name: str):
    if not re.fullmatch(r"[\w-]+\.png", name):
        raise HTTPException(404)
    p = CHART_DIR / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/invoice/{invoice_id}.pdf")
def invoice_pdf(request: Request, invoice_id: int):
    inv = db.q1("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    email = current_email(request)
    if not inv or not (is_admin(request) or (email and inv["owner_email"] == email)):
        raise HTTPException(404)
    return FileResponse(inv["path"], media_type="application/pdf", filename=inv["filename"],
                        content_disposition_type="inline")


# ---------- auth: email -> code -> password ----------

def _auth(request, mode, **ctx):
    return page(request, "auth.html", mode=mode, **ctx)


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, email: str = "", next: str = ""):
    if current_email(request):
        return RedirectResponse(safe_next(next), 303)
    return _auth(request, "register", email=email, next=next)


@app.post("/register")
def register_submit(request: Request, email: str = Form(...), next: str = Form("")):
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return _auth(request, "register", email=email, next=next, error="Please enter a valid email address.")
    user = db.q1("SELECT * FROM users WHERE email=?", (email,))
    if user and user["password_hash"]:
        return _auth(request, "login", email=email, next=next, info="You already have an account - log in below.")
    return _send_code(request, email, "register", next)


def _send_code(request, email, purpose, next=""):
    code = security.issue_code(email, purpose)
    if code:
        try:
            notify.code(email, code, purpose)
        except Exception as e:
            return _auth(request, "register" if purpose == "register" else "forgot", email=email, next=next,
                         error=f"We couldn't send the email ({type(e).__name__}: {str(e)[:160]}). Please try again shortly.")
    request.session["pending_email"] = email
    request.session["pending_purpose"] = purpose
    return _auth(request, "verify", email=email, next=next)


@app.post("/verify")
def verify(request: Request, code: str = Form(...), next: str = Form("")):
    email = request.session.get("pending_email")
    if not email:
        return RedirectResponse("/register", 303)
    if not security.verify_code(email, code):
        return _auth(request, "verify", email=email, next=next, error="That code didn't match or has expired.")
    request.session["verified_email"] = email
    user = db.q1("SELECT * FROM users WHERE email=?", (email,))
    return _auth(request, "password", email=email, next=next, user=user)


@app.post("/resend")
def resend(request: Request, next: str = Form("")):
    email = request.session.get("pending_email")
    if not email:
        return RedirectResponse("/register", 303)
    return _send_code(request, email, request.session.get("pending_purpose", "register"), next)


@app.post("/set-password")
def set_password(request: Request, password: str = Form(...), name: str = Form(""), company: str = Form(""),
                 next: str = Form("")):
    email = request.session.get("verified_email")
    if not email:
        return RedirectResponse("/register", 303)
    if len(password) < 8:
        return _auth(request, "password", email=email, next=next, error="Use at least 8 characters.")
    now = time.time()
    if db.q1("SELECT 1 FROM users WHERE email=?", (email,)):
        db.x("UPDATE users SET password_hash=?, verified_at=COALESCE(verified_at, ?),"
             " name=COALESCE(NULLIF(?,''), name), company=COALESCE(NULLIF(?,''), company) WHERE email=?",
             (security.hash_password(password), now, name.strip(), company.strip(), email))
    else:
        db.x("INSERT INTO users(email, password_hash, name, company, created_at, verified_at) VALUES (?,?,?,?,?,?)",
             (email, security.hash_password(password), name.strip() or None, company.strip() or None, now, now))
        db.log_event("registered", email, request.session.get("sid"))
    request.session.pop("verified_email", None)
    request.session.pop("pending_email", None)
    login(request, email)
    return RedirectResponse(safe_next(next), 303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "", email: str = ""):
    if current_email(request):
        return RedirectResponse(safe_next(next), 303)
    return _auth(request, "login", next=next, email=email)


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("")):
    email = email.strip().lower()
    user = db.q1("SELECT * FROM users WHERE email=?", (email,))
    if not user or not user["password_hash"] or not security.check_password(password, user["password_hash"]):
        return _auth(request, "login", email=email, next=next, error="That email and password don't match.")
    login(request, email)
    return RedirectResponse(safe_next(next), 303)


@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, email: str = ""):
    return _auth(request, "forgot", email=email)


@app.post("/forgot")
def forgot_submit(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return _auth(request, "forgot", email=email, error="Please enter a valid email address.")
    return _send_code(request, email, "reset")


@app.get("/logout")
def logout(request: Request):
    request.session.pop("email", None)
    return RedirectResponse("/", 303)


# ---------- signed-in pages ----------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    email = current_email(request)
    if not email:
        return RedirectResponse("/login?next=/dashboard", 303)
    db.log_event("dashboard_viewed", email)
    user = db.q1("SELECT * FROM users WHERE email=?", (email,))
    return page(request, "dashboard.html", user=user, view_email=email, admin_view=False)


@app.post("/api/chat")
async def api_chat(request: Request):
    email = current_email(request)
    if not email:
        raise HTTPException(401)
    body = await request.json()
    q = (body.get("q") or "").strip()[:2000]
    if not q:
        raise HTTPException(400)
    try:
        answer = await asyncio.to_thread(chat.ask, email, q)
    except Exception as e:
        answer = f"Sorry - something went wrong answering that ({type(e).__name__}). Please try again."
    return {"answer": answer}


@app.get("/api/chat/history")
def chat_history(request: Request):
    email = current_email(request)
    if not email:
        raise HTTPException(401)
    rows = db.q("SELECT role, content FROM chat_messages WHERE email=? ORDER BY id DESC LIMIT 20", (email,))
    return rows[::-1]


@app.get("/offer", response_class=HTMLResponse)
def offer_page(request: Request):
    email = current_email(request)
    if not email:
        return RedirectResponse("/register?next=/offer", 303)
    offers = db.q("SELECT * FROM offers WHERE active=1 ORDER BY sort, id")
    a = analysis.build(email=email)
    return page(request, "offer.html", offers=offers, a=a, booking_url=db.get_setting("booking_url"),
                sent=request.query_params.get("sent"))


@app.post("/offer")
def offer_submit(request: Request, kind: str = Form(...), offer_id: str = Form(""), phone: str = Form(""),
                 notes: str = Form("")):
    email = current_email(request)
    if not email:
        return RedirectResponse("/register?next=/offer", 303)
    if kind not in ("quote", "offer", "call"):
        raise HTTPException(400)
    oid = int(offer_id) if offer_id.isdigit() else None
    rid = db.x("INSERT INTO offer_requests(email, kind, offer_id, phone, notes, created_at) VALUES (?,?,?,?,?,?)",
               (email, kind, oid, phone[:40], notes[:2000], time.time()))
    db.log_event("offer_request", email, kind=kind, offer_id=oid)
    background(notify.offer_request, rid)
    return RedirectResponse(f"/offer?sent={kind}", 303)


# ---------- first-run setup (secrets are entered here, never committed) ----------

@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, token: str = ""):
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(404)
    return page(request, "setup.html", token=token, configured={k: bool(db.get_setting(k)) for k in SECRET_SETTINGS},
                mail_user=db.get_setting("mail_user"), saved=request.query_params.get("saved"))


@app.post("/setup")
def setup_submit(request: Request, token: str = Form(...), anthropic_api_key: str = Form(""),
                 mail_password: str = Form(""), mail_user: str = Form(""), admin_password: str = Form("")):
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(404)
    if len(admin_password) >= 8:
        now = time.time()
        db.x("INSERT INTO users(email, password_hash, created_at, verified_at) VALUES (?,?,?,?)"
             " ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash",
             (SUPERADMIN_EMAIL, security.hash_password(admin_password), now, now))
    if anthropic_api_key.strip():
        db.set_setting("anthropic_api_key", anthropic_api_key.strip())
    if mail_password.strip():
        db.set_setting("mail_password", mail_password.replace(" ", "").strip())
    if mail_user.strip():
        db.set_setting("mail_user", mail_user.strip().lower())
    return RedirectResponse(f"/setup?token={token}&saved=1", 303)


# ---------- superadmin ----------

@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    require_admin(request)
    recent = db.q("SELECT i.*, s.source FROM invoices i LEFT JOIN submissions s ON s.id=i.submission_id"
                  " ORDER BY i.id DESC LIMIT 12")
    return page(request, "admin/home.html", s=analysis.admin_stats(), recent=recent, tab="home", live=activity(24))


@app.get("/admin/invoices", response_class=HTMLResponse)
def admin_invoices(request: Request, status: str = "", carrier: str = "", qs: str = ""):
    require_admin(request)
    where, args = ["1=1"], []
    if status:
        where.append("i.status=?")
        args.append(status)
    if carrier:
        where.append("i.carrier=?")
        args.append(carrier)
    if qs:
        where.append("(i.owner_email LIKE ? OR i.account_name LIKE ? OR i.account_number LIKE ? OR i.filename LIKE ?)")
        args += [f"%{qs}%"] * 4
    rows = db.q(f"SELECT i.*, s.source FROM invoices i LEFT JOIN submissions s ON s.id=i.submission_id"
                f" WHERE {' AND '.join(where)} ORDER BY i.id DESC LIMIT 300", args)
    return page(request, "admin/invoices.html", rows=rows, status=status, carrier=carrier, qs=qs, tab="invoices")


@app.get("/admin/invoice/{invoice_id}", response_class=HTMLResponse)
def admin_invoice(request: Request, invoice_id: int):
    require_admin(request)
    inv = db.q1("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not inv:
        raise HTTPException(404)
    lines = db.q("SELECT * FROM lines WHERE invoice_id=?", (invoice_id,))
    usage = db.q("SELECT * FROM ai_usage WHERE invoice_id=?", (invoice_id,))
    pretty = json.dumps(json.loads(inv["extraction_json"]), indent=2) if inv["extraction_json"] else ""
    return page(request, "admin/invoice.html", inv=inv, lines=lines, usage=usage, pretty=pretty, tab="invoices",
                msg=request.query_params.get("msg"))


@app.post("/admin/invoice/{invoice_id}/reprocess")
def admin_reprocess(request: Request, invoice_id: int):
    require_admin(request)
    db.x("UPDATE invoices SET status='queued', error=NULL, verified=0 WHERE id=?", (invoice_id,))
    return RedirectResponse(f"/admin/invoice/{invoice_id}?msg=Queued+for+reprocessing", 303)


@app.post("/admin/invoice/{invoice_id}/save")
def admin_save(request: Request, invoice_id: int, extraction_json: str = Form(...), verify: str = Form("")):
    """Correct an extraction by hand. Verified bills become reference examples for future extraction."""
    require_admin(request)
    try:
        data = json.loads(extraction_json)
    except Exception as e:
        return RedirectResponse(f"/admin/invoice/{invoice_id}?msg=Invalid+JSON:+{str(e)[:60]}", 303)
    extract.store(invoice_id, data)
    ok, diff = extract.reconcile(data)
    db.x("UPDATE invoices SET status='done', recon_ok=?, recon_diff=?, verified=?, confidence=? WHERE id=?",
         (int(ok), diff, 1 if verify else 0, 1.0 if verify else (0.97 if ok else 0.6), invoice_id))
    if verify and data.get("carrier"):
        extract.learn(data["carrier"], data, invoice_id)
    return RedirectResponse(f"/admin/invoice/{invoice_id}?msg=Saved", 303)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    require_admin(request)
    rows = db.q("""
        SELECT e.email, u.name, u.company, u.created_at, u.last_login, u.password_hash IS NOT NULL AS registered,
               (SELECT COUNT(*) FROM invoices i WHERE i.owner_email=e.email) bills,
               (SELECT MAX(created_at) FROM invoices i WHERE i.owner_email=e.email) last_bill,
               (SELECT COUNT(*) FROM offer_requests r WHERE r.email=e.email) requests,
               (SELECT COUNT(*) FROM chat_messages c WHERE c.email=e.email AND role='user') questions
        FROM (SELECT email FROM users UNION SELECT DISTINCT owner_email FROM invoices WHERE owner_email IS NOT NULL) e
        LEFT JOIN users u ON u.email=e.email ORDER BY COALESCE(last_bill, u.created_at) DESC""")
    anon = db.q1("SELECT COUNT(DISTINCT session_id) n, COUNT(*) bills FROM invoices WHERE owner_email IS NULL")
    return page(request, "admin/users.html", rows=rows, anon=anon, tab="users")


@app.get("/admin/user", response_class=HTMLResponse)
def admin_user(request: Request, email: str):
    require_admin(request)
    user = db.q1("SELECT * FROM users WHERE email=?", (email.lower(),))
    return page(request, "dashboard.html", user=user, view_email=email.lower(), admin_view=True)


@app.get("/admin/requests", response_class=HTMLResponse)
def admin_requests(request: Request):
    require_admin(request)
    rows = db.q("SELECT r.*, o.title offer_title, u.company FROM offer_requests r LEFT JOIN offers o ON o.id=r.offer_id"
                " LEFT JOIN users u ON u.email=r.email ORDER BY r.id DESC")
    return page(request, "admin/requests.html", rows=rows, tab="requests")


@app.post("/admin/requests/{rid}")
def admin_request_status(request: Request, rid: int, status: str = Form(...)):
    require_admin(request)
    db.x("UPDATE offer_requests SET status=? WHERE id=?", (status[:20], rid))
    return RedirectResponse("/admin/requests", 303)


@app.get("/admin/offers", response_class=HTMLResponse)
def admin_offers(request: Request):
    require_admin(request)
    return page(request, "admin/offers.html", rows=db.q("SELECT * FROM offers ORDER BY sort, id"), tab="offers")


@app.post("/admin/offers")
def admin_offers_save(request: Request, id: str = Form(""), title: str = Form(...), subtitle: str = Form(""),
                      details: str = Form(""), badge: str = Form(""), active: str = Form(""), sort: int = Form(0),
                      delete: str = Form("")):
    require_admin(request)
    oid = int(id) if id.isdigit() else None
    if oid and delete:
        db.x("DELETE FROM offers WHERE id=?", (oid,))
    elif oid:
        db.x("UPDATE offers SET title=?, subtitle=?, details=?, badge=?, active=?, sort=? WHERE id=?",
             (title, subtitle, details, badge, 1 if active else 0, sort, oid))
    else:
        db.x("INSERT INTO offers(title, subtitle, details, badge, active, sort, created_at) VALUES (?,?,?,?,?,?,?)",
             (title, subtitle, details, badge, 1 if active else 0, sort, time.time()))
    return RedirectResponse("/admin/offers", 303)


@app.get("/admin/knowledge", response_class=HTMLResponse)
def admin_knowledge(request: Request, carrier: str = ""):
    require_admin(request)
    rows = db.q("SELECT * FROM knowledge WHERE (?='' OR carrier=?) ORDER BY carrier, kind, seen_count DESC",
                (carrier, carrier))
    return page(request, "admin/knowledge.html", rows=rows, carrier=carrier, tab="knowledge")


@app.post("/admin/knowledge/{kid}")
def admin_knowledge_edit(request: Request, kid: int, action: str = Form(...), value: str = Form("")):
    require_admin(request)
    if action == "delete":
        db.x("DELETE FROM knowledge WHERE id=?", (kid,))
    elif action == "toggle":
        db.x("UPDATE knowledge SET approved=1-approved WHERE id=?", (kid,))
    elif action == "save":
        db.x("UPDATE knowledge SET value=? WHERE id=?", (value[:300], kid))
    return RedirectResponse("/admin/knowledge", 303)


@app.post("/admin/knowledge")
def admin_knowledge_add(request: Request, carrier: str = Form(...), kind: str = Form(...), key: str = Form(...),
                        value: str = Form("")):
    require_admin(request)
    db.x("INSERT OR REPLACE INTO knowledge(carrier, kind, key, value, approved, updated_at) VALUES (?,?,?,?,1,?)",
         (carrier, kind, key[:160], value[:300], time.time()))
    return RedirectResponse("/admin/knowledge", 303)


EDITABLE = ["sales_email", "booking_url", "ai_provider", "claude_model", "chat_model", "local_ai_url",
            "local_ai_model", "mail_polling", "mail_user", "mail_address", "imap_host", "smtp_host"]


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    require_admin(request)
    hb = float(db.get_setting("worker_heartbeat") or 0)
    return page(request, "admin/settings.html", tab="settings",
                vals={k: db.get_setting(k) for k in EDITABLE},
                secrets_set={k: bool(db.get_setting(k)) for k in SECRET_SETTINGS},
                mail_status=json.loads(db.get_setting("mail_status") or "{}"),
                worker_age=time.time() - hb if hb else None, saved=request.query_params.get("saved"),
                last_test=db.get_setting("last_test_email"), deploy_log=_deploy_log(),
                queue=db.q1("SELECT COUNT(*) n FROM invoices WHERE status IN ('queued','processing')")["n"])


def _deploy_log():
    p = DATA_DIR / "deploy.log"
    return "\n".join(p.read_text().splitlines()[-6:]) if p.exists() else ""


@app.post("/admin/settings")
async def admin_settings_save(request: Request):
    require_admin(request)
    form = await request.form()
    for k in EDITABLE:
        if k in form:
            db.set_setting(k, str(form[k]).strip())
    for k in SECRET_SETTINGS:
        v = str(form.get(k, "")).strip()
        if v:
            db.set_setting(k, v.replace(" ", "") if k == "mail_password" else v)
    return RedirectResponse("/admin/settings?saved=1", 303)


@app.post("/admin/test-email")
def admin_test_email(request: Request):
    require_admin(request)
    try:
        mailer.send(SUPERADMIN_EMAIL, "SecondOpinion test email",
                    render("email/no_attachment.html", email=SUPERADMIN_EMAIL))
        msg = "sent OK"
    except Exception as e:
        msg = f"failed: {type(e).__name__}: {e}"[:200]
    db.set_setting("last_test_email", f"{time.strftime('%H:%M:%S')} {msg}")
    return RedirectResponse("/admin/settings?saved=test", 303)


@app.post("/admin/poll-now")
def admin_poll_now(request: Request):
    require_admin(request)
    background(mailer.poll)
    return RedirectResponse("/admin/settings?saved=poll", 303)


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, exc.status_code)
    return page(request, "404.html", status_code=exc.status_code)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return page(request, "privacy.html")


@app.get("/setup/inspect")
def setup_inspect(token: str = "", invoice_id: int = 0, pdf: int = 0):
    """Diagnostics for checking extraction quality (setup-token protected)."""
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(404)
    if not invoice_id:
        return db.q("SELECT i.id, s.source, s.notified_at, s.notify_error, owner_email, filename, status, error, carrier,"
                    " account_name, period_month, total_excl_gst,"
                    " line_count, recon_ok, recon_diff, pages, has_text, provider, model, duration_s, i.created_at"
                    " FROM invoices i LEFT JOIN submissions s ON s.id=i.submission_id ORDER BY i.id DESC LIMIT 30")
    inv = db.q1("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not inv:
        raise HTTPException(404)
    if pdf:
        return FileResponse(inv["path"], media_type="application/pdf")
    _, text = extract.read_pdf(inv["path"])
    inv["extraction_json"] = json.loads(inv["extraction_json"]) if inv["extraction_json"] else None
    inv["pdf_text"] = text
    inv["usage"] = db.q("SELECT * FROM ai_usage WHERE invoice_id=?", (invoice_id,))
    return inv


@app.post("/setup/reprocess")
def setup_reprocess(token: str = Form(...), ids: str = Form(...), forget_value: str = Form(""),
                    resend: str = Form("")):
    """Re-queue bills for extraction; optionally drop learned facts with a given meaning (e.g. a retired category)."""
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(404)
    if forget_value:
        db.x("DELETE FROM knowledge WHERE value=?", (forget_value,))
    for i in [int(x) for x in ids.split(",") if x.strip().isdigit()]:
        if resend:
            db.x("UPDATE submissions SET notified_at=NULL, notify_error=NULL WHERE id=(SELECT submission_id FROM"
                 " invoices WHERE id=?)", (i,))
        else:
            db.x("UPDATE invoices SET status='queued', error=NULL WHERE id=?", (i,))
    return {"ok": True}


def activity(hours=24):
    since = time.time() - hours * 3600
    ev = db.q("SELECT kind, email, session_id, meta, created_at FROM events WHERE created_at>? ORDER BY id DESC",
              (since,))
    views = [e for e in ev if e["kind"] == "page_view"]
    metas = [json.loads(e["meta"] or "{}") for e in views]
    count = lambda k: sum(1 for e in ev if e["kind"] == k)
    tally = lambda key: sorted(
        ((v, sum(1 for m in metas if m.get(key) == v)) for v in {m.get(key) for m in metas if m.get(key)}),
        key=lambda t: -t[1])
    return {
        "hours": hours,
        "page_views": len(views),
        "visitors": len({e["session_id"] for e in views if e["session_id"]}),
        "uploads": count("upload"), "emails_in": count("email_received"), "registrations": count("registered"),
        "results_views": count("results_viewed"), "dashboard_views": count("dashboard_viewed"),
        "ai_questions": count("chat"), "offer_requests": count("offer_request"), "errors": count("error"),
        "pages": tally("path")[:10], "referrers": tally("ref")[:10], "devices": tally("device"),
        "bills": db.q("SELECT status, COUNT(*) n FROM invoices WHERE created_at>? GROUP BY status", (since,)),
        "failed_bills": db.q("SELECT id, filename, owner_email, error FROM invoices WHERE created_at>?"
                             " AND status IN ('failed','rejected')", (since,)),
        "unsent_emails": db.q("SELECT id, email, notify_error FROM submissions WHERE created_at>?"
                              " AND notify_error IS NOT NULL", (since,)),
        "ai_cost_usd": db.q1("SELECT ROUND(COALESCE(SUM(cost_usd),0),2) v FROM ai_usage WHERE created_at>?",
                             (since,))["v"],
        "error_events": [json.loads(e["meta"] or "{}") | {"at": e["created_at"]} for e in ev if e["kind"] == "error"][:20],
        "feed": [{"kind": e["kind"], "email": e["email"], "meta": json.loads(e["meta"] or "{}"), "at": e["created_at"]}
                 for e in ev if e["kind"] != "page_view"][:60],
        "mail_status": json.loads(db.get_setting("mail_status") or "{}"),
        "worker_age_s": round(time.time() - float(db.get_setting("worker_heartbeat") or 0)),
    }


@app.get("/setup/stats")
def setup_stats(token: str = "", hours: float = 24):
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(404)
    return activity(hours)
