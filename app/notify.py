"""Outbound emails: analysis summaries, verification codes, sales hand-offs."""
import time
import traceback

from . import analysis, charts, db, mailer
from .config import BASE_URL, SUPERADMIN_EMAIL
from .tpl import render


def _is_registered(email):
    return bool(db.q1("SELECT 1 FROM users WHERE email=? AND password_hash IS NOT NULL", (email.lower(),)))


def submission_summary(submission_id):
    """Email the high-level analysis back to whoever emailed their bills in."""
    sub = db.q1("SELECT * FROM submissions WHERE id=?", (submission_id,))
    to = sub["email"]
    invs = db.q("SELECT * FROM invoices WHERE submission_id=?", (submission_id,))
    ok = [i for i in invs if i["status"] in ("done", "duplicate")]
    problems = [i for i in invs if i["status"] in ("rejected", "failed")]
    a = analysis.build(email=to)
    registered = _is_registered(to)
    ctx = {
        "a": a, "registered": registered, "ok": ok, "problems": problems,
        "spend_chart": charts.monthly_spend(a) if a.get("has_data") else None,
        "lines_chart": charts.top_lines(a) if a.get("has_data") and a["lines"] else None,
        "cta_url": f"{BASE_URL}/dashboard" if registered else f"{BASE_URL}/register?email={to}",
        "email": to,
    }
    if a.get("has_data"):
        subject = "Your second opinion is ready" if registered else "Your mobile bill analysis is ready"
    else:
        subject = "We couldn't analyse your bill"
    html = render("email/summary.html", **ctx)
    text = render("email/summary.txt", **ctx)
    try:
        mailer.send(to, subject, html, text)
        db.x("UPDATE submissions SET notified_at=? WHERE id=?", (time.time(), submission_id))
    except Exception as e:
        traceback.print_exc()
        db.x("UPDATE submissions SET notified_at=?, notify_error=? WHERE id=?",
             (time.time(), str(e)[:300], submission_id))


def session_summary(to, session_id, claim_url):
    """Summary for a web upload, sent to an email the visitor typed in. The button claims their bills."""
    a = analysis.build(session_id=session_id)
    ctx = {"a": a, "registered": False, "ok": [], "problems": [], "email": to, "cta_url": claim_url,
           "cta_label": "Save my analysis and see the detail",
           "spend_chart": charts.monthly_spend(a) if a.get("has_data") else None}
    mailer.send(to, "Your mobile bill analysis", render("email/summary.html", **ctx),
                render("email/summary.txt", **ctx))


def no_attachment(to, subject):
    html = render("email/no_attachment.html", email=to)
    mailer.send(to, "Re: " + (subject or "your email"), html,
                "Thanks for your email. We couldn't find a PDF bill attached. Please reply with your mobile or"
                " broadband bill (any NZ provider) attached as a PDF.")


def code(to, code_value, purpose):
    title = "Reset your password" if purpose == "reset" else "Confirm your email"
    html = render("email/code.html", code=code_value, title=title)
    mailer.send(to, f"{code_value} is your SecondOpinion code", html,
                f"Your SecondOpinion code is {code_value}. It expires in 15 minutes.")


def offer_request(req_id):
    from . import dossier
    req = db.q1("SELECT * FROM offer_requests WHERE id=?", (req_id,))
    offer = db.q1("SELECT * FROM offers WHERE id=?", (req["offer_id"],)) if req["offer_id"] else None
    to = db.get_setting("sales_email")
    dossier.send(req["email"], req=req, to=to)
    db.x("UPDATE offer_requests SET sent_to=? WHERE id=?", (to, req_id))
    # Confirmation to the customer
    mailer.send(req["email"], "We're on it - your SecondOpinion request",
                render("email/request_ack.html", req=req, offer=offer))


# ---------- superadmin visibility: instant alerts and the daily digest ----------

def admin_alert(title, lines=(), link=None, link_label=None, email=None):
    """Email the superadmin the moment a stranger does something that matters (upload, account, offer request).
    Activity from the superadmin's own addresses (Admin > Settings > own emails) is ignored."""
    from . import visitors
    if db.get_setting("alerts") != "on" or (email and not visitors.is_stranger(email)):
        return
    to = db.get_setting("alert_email") or SUPERADMIN_EMAIL
    try:
        mailer.send(to, "SecondOpinion: " + title, render("email/alert.html", title=title, lines=lines, link=link,
                                                             link_label=link_label), "\n".join([title, *lines]))
    except Exception:
        traceback.print_exc()


def bill_alert(invoice_id):
    inv = db.q1("SELECT i.*, s.token, s.source FROM invoices i LEFT JOIN submissions s ON s.id=i.submission_id"
                " WHERE i.id=?", (invoice_id,))
    if not inv:
        return
    from .config import carrier_label
    who = inv["owner_email"] or "an anonymous visitor"
    if inv["status"] in ("done", "duplicate"):
        title = f"New bill from {who}: {carrier_label(inv['carrier'])}"
        lines = [f"{inv['account_name'] or 'account name not shown'} - {inv['line_count'] or 0} mobile line(s),"
                 f" {analysis.fmt_money(inv['total_excl_gst'], 2)} excl GST, period {inv['period_month'] or '?'}",
                 f"Sent via {inv['source'] or 'web'}; file {inv['filename']}"]
    else:
        title = f"Bill {inv['status']} from {who}"
        lines = [inv["error"] or "", f"File {inv['filename']}"]
    link = f"{BASE_URL}/results/{inv['token']}" if inv["token"] else f"{BASE_URL}/admin/invoice/{invoice_id}"
    admin_alert(title, lines, link, "See what they saw", email=inv["owner_email"])


def daily_digest():
    """The 7am (NZ) summary of the previous day, to the superadmin."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import visitors
    s = visitors.summary(24)
    trend = db.q(
        "SELECT date(created_at,'unixepoch','localtime') d, SUM(kind='upload') uploads, SUM(kind='email_received') emails,"
        " SUM(kind='registered') registrations, SUM(kind='offer_request') offers,"
        " SUM(kind='page_view' AND meta LIKE '%\"js\": 1%') views FROM events"
        " WHERE created_at > strftime('%s','now','-7 days') GROUP BY d ORDER BY d")
    since = time.time() - 86400
    ai_cost = db.q1("SELECT ROUND(COALESCE(SUM(cost_usd),0),2) v FROM ai_usage WHERE created_at>?", (since,))["v"]
    failed = db.q1("SELECT COUNT(*) n FROM invoices WHERE created_at>? AND status IN ('failed','rejected')", (since,))["n"]
    day = datetime.now(ZoneInfo("Pacific/Auckland")).strftime("%A %d %B %Y")
    to = db.get_setting("alert_email") or SUPERADMIN_EMAIL
    html = render("email/digest.html", s=s, trend=trend, ai_cost=ai_cost or 0, failed=failed, day=day)
    mailer.send(to, f"SecondOpinion digest: {s['visitors']} visitors, {s['uploaders']} uploaded, {s['registered']} registered",
                html, f"{s['visitors']} visitors, {s['uploaders']} uploaded bills, {s['registered']} registered.")
