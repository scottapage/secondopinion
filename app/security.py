import hashlib
import hmac
import secrets
import time

from . import db
from .config import SECRET_KEY

CODE_TTL = 15 * 60
MAX_ATTEMPTS = 5


def hash_password(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw, stored):
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _code_hash(email, code):
    return hmac.new(SECRET_KEY.encode(), f"{email.lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(email, purpose):
    """Create a 6-digit code. Returns None if one was issued in the last 30 seconds."""
    email = email.lower()
    recent = db.q1("SELECT created_at FROM email_codes WHERE email=? AND used_at IS NULL ORDER BY id DESC LIMIT 1",
                   (email,))
    if recent and time.time() - recent["created_at"] < 30:
        return None
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.x("INSERT INTO email_codes(email, code_hash, purpose, expires_at, created_at) VALUES (?,?,?,?,?)",
         (email, _code_hash(email, code), purpose, time.time() + CODE_TTL, time.time()))
    return code


def verify_code(email, code):
    email = email.lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    row = db.q1("SELECT * FROM email_codes WHERE email=? AND used_at IS NULL ORDER BY id DESC LIMIT 1", (email,))
    if not row or row["expires_at"] < time.time() or row["attempts"] >= MAX_ATTEMPTS:
        return False
    if hmac.compare_digest(row["code_hash"], _code_hash(email, code)):
        db.x("UPDATE email_codes SET used_at=? WHERE id=?", (time.time(), row["id"]))
        return True
    db.x("UPDATE email_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
    return False
