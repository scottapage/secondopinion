"""Fastmail IMAP polling and SMTP sending for mobile@secondopinion.co.nz."""
import email
import email.policy
import imaplib
import json
import smtplib
import ssl
import time
import traceback
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from . import db, ingest

PROCESSED_FOLDER = "Processed"


def send(to, subject, html, text=None, reply_to=None, attachments=None):
    msg = EmailMessage()
    sender = db.get_setting("mail_address")
    msg["From"] = formataddr(("SecondOpinion", sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg["Auto-Submitted"] = "auto-replied"
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text or "Please view this email in an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        maintype, subtype = mime.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    with smtplib.SMTP_SSL(db.get_setting("smtp_host"), 465, context=ssl.create_default_context(), timeout=60) as s:
        s.login(db.get_setting("mail_user"), db.get_setting("mail_password"))
        s.send_message(msg)
    db.log_event("email_sent", to, subject=subject)


def _status(**kw):
    kw["at"] = time.time()
    db.set_setting("mail_status", json.dumps(kw))


def _pdf_parts(msg):
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename() or ""
        ctype = part.get_content_type()
        if ctype == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True)
            if data:
                yield name or "invoice.pdf", data


def _skip(msg, sender):
    me = db.get_setting("mail_address").lower()
    if sender == me or not sender or "@" not in sender:
        return True
    if (msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or
            msg.get("Precedence", "").lower() in ("bulk", "junk", "auto_reply")):
        return True
    local = sender.split("@")[0]
    return local in ("mailer-daemon", "postmaster", "no-reply", "noreply", "do-not-reply")


def poll():
    """Fetch unseen mail, ingest PDF attachments, move messages to the Processed folder."""
    if db.get_setting("mail_polling") != "on" or not db.get_setting("mail_password"):
        _status(ok=False, error="Mail polling is off or the mail password isn't set")
        return 0
    count = 0
    try:
        m = imaplib.IMAP4_SSL(db.get_setting("imap_host"), 993, timeout=60)
        m.login(db.get_setting("mail_user"), db.get_setting("mail_password"))
        m.create(PROCESSED_FOLDER)  # harmless if it exists
        m.select("INBOX")
        typ, data = m.uid("search", None, "UNSEEN")
        for uid in (data[0] or b"").split():
            typ, raw = m.uid("fetch", uid, "(RFC822)")
            if not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1], policy=email.policy.default)
            sender = parseaddr(msg.get("Reply-To") or msg.get("From", ""))[1].lower()
            try:
                if not _skip(msg, sender):
                    handle_message(msg, sender)
                    count += 1
            except Exception:
                traceback.print_exc()
            m.uid("store", uid, "+FLAGS", "(\\Seen)")
            if m.uid("copy", uid, PROCESSED_FOLDER)[0] == "OK":
                m.uid("store", uid, "+FLAGS", "(\\Deleted)")
        m.expunge()
        m.logout()
        _status(ok=True, processed=count)
    except Exception as e:
        traceback.print_exc()
        _status(ok=False, error=f"{type(e).__name__}: {e}"[:300])
    return count


def handle_message(msg, sender):
    from . import notify

    subject = str(msg.get("Subject", ""))[:200]
    pdfs = list(_pdf_parts(msg))
    locked = db.get_setting("site_lock") == "on"
    db.log_event("email_received", sender, pdfs=len(pdfs), held=locked)
    if locked:
        # Site is password-locked: keep the bills (status 'held', released from Admin > Bills) and reply politely
        sub_id, _ = ingest.new_submission("email", email=sender, message_id=msg.get("Message-ID"), subject=subject)
        for name, data in pdfs:
            ingest.add_file(sub_id, data, name, owner_email=sender, hold=True)
        db.x("UPDATE submissions SET notified_at=?, notify_error='held' WHERE id=?", (time.time(), sub_id))
        notify.paused(sender, subject)
        return
    if not pdfs:
        notify.no_attachment(sender, subject)
        return
    sub_id, _ = ingest.new_submission("email", email=sender, message_id=msg.get("Message-ID"), subject=subject)
    for name, data in pdfs:
        ingest.add_file(sub_id, data, name, owner_email=sender)
