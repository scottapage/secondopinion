import hashlib
import re
import secrets
import time

from . import db
from .config import INVOICE_DIR


def new_submission(source, email=None, session_id=None, message_id=None, subject=None):
    token = secrets.token_urlsafe(12)
    sid = db.x(
        "INSERT INTO submissions(token, source, email, session_id, message_id, subject, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (token, source, email and email.lower(), session_id, message_id, subject, time.time()),
    )
    return sid, token


def is_pdf(data):
    return data[:1024].lstrip().startswith(b"%PDF")


def add_file(submission_id, data, filename, owner_email=None, session_id=None):
    """Store a submitted file and queue it for analysis. Returns the invoice id."""
    sha = hashlib.sha256(data).hexdigest()
    folder = INVOICE_DIR / sha[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.pdf"
    if not path.exists():
        path.write_bytes(data)
    filename = re.sub(r"[^\w.\- ()]", "_", filename or "invoice.pdf")[:120]
    owner_email = owner_email and owner_email.lower()

    status, error, dup = "queued", None, None
    if not is_pdf(data):
        status, error = "rejected", "Only PDF files are supported."
    else:
        clause, val = ("owner_email=?", owner_email) if owner_email else ("session_id=?", session_id)
        prev = db.q1(f"SELECT id FROM invoices WHERE {clause} AND sha256=? AND status IN ('done','queued','processing')",
                     (val, sha)) if val else None
        if prev:
            status, error, dup = "duplicate", "This bill was already submitted.", prev["id"]
    return db.x(
        "INSERT INTO invoices(submission_id, owner_email, session_id, sha256, filename, path, size_bytes, status,"
        " error, duplicate_of, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (submission_id, owner_email, session_id, sha, filename, str(path), len(data), status, error, dup, time.time()),
    )


def claim_session(session_id, email):
    """Attach bills uploaded anonymously in this browser session to a verified email address."""
    if not session_id:
        return 0
    with db.tx() as c:
        cur = c.execute("UPDATE invoices SET owner_email=? WHERE session_id=? AND owner_email IS NULL",
                        (email.lower(), session_id))
        c.execute("UPDATE submissions SET email=? WHERE session_id=? AND email IS NULL", (email.lower(), session_id))
        n = cur.rowcount
    dedupe(email)
    return n


def dedupe(email):
    """Mark later copies of the same bill (same file, or same carrier/account/invoice number) as duplicates."""
    with db.tx() as c:
        c.execute("""
            UPDATE invoices SET status='duplicate', error='This bill was already submitted.'
            WHERE owner_email=? AND status='done' AND EXISTS (
                SELECT 1 FROM invoices o WHERE o.owner_email=invoices.owner_email AND o.id<invoices.id
                AND o.status='done' AND (o.sha256=invoices.sha256 OR (o.invoice_number IS NOT NULL
                AND o.invoice_number=invoices.invoice_number AND o.carrier=invoices.carrier
                AND IFNULL(o.account_number,'')=IFNULL(invoices.account_number,''))))""", (email.lower(),))
