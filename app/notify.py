"""Outbound emails: analysis summaries, verification codes, sales hand-offs."""
import time
import traceback

from . import analysis, charts, db, mailer
from .config import BASE_URL
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
                "Thanks for your email. We couldn't find a PDF bill attached. Please reply with your Spark, One NZ or"
                " 2degrees bill attached as a PDF.")


def code(to, code_value, purpose):
    title = "Reset your password" if purpose == "reset" else "Confirm your email"
    html = render("email/code.html", code=code_value, title=title)
    mailer.send(to, f"{code_value} is your SecondOpinion code", html,
                f"Your SecondOpinion code is {code_value}. It expires in 15 minutes.")


def offer_request(req_id):
    req = db.q1("SELECT * FROM offer_requests WHERE id=?", (req_id,))
    offer = db.q1("SELECT * FROM offers WHERE id=?", (req["offer_id"],)) if req["offer_id"] else None
    a = analysis.build(email=req["email"])
    to = db.get_setting("sales_email")
    ctx = {"req": req, "offer": offer, "a": a,
           "spend_chart": charts.monthly_spend(a) if a.get("has_data") else None,
           "user": db.q1("SELECT * FROM users WHERE email=?", (req["email"],))}
    html = render("email/sales.html", **ctx)
    kinds = {"quote": "Quote request", "offer": "Offer claimed", "call": "Call requested"}
    mailer.send(to, f"{kinds.get(req['kind'], 'Request')}: {req['email']}", html, reply_to=req["email"])
    db.x("UPDATE offer_requests SET sent_to=? WHERE id=?", (to, req_id))
    # Confirmation to the customer
    mailer.send(req["email"], "We're on it - your SecondOpinion request",
                render("email/request_ack.html", req=req, offer=offer))
