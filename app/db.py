import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH, SETTING_DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    name TEXT,
    company TEXT,
    created_at REAL NOT NULL,
    verified_at REAL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS email_codes (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at REAL,
    created_at REAL NOT NULL
);

-- One web upload batch or one inbound email
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,            -- web | email
    email TEXT,
    session_id TEXT,
    message_id TEXT,
    subject TEXT,
    notified_at REAL,
    notify_error TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER REFERENCES submissions(id),
    owner_email TEXT,
    session_id TEXT,
    sha256 TEXT NOT NULL,
    filename TEXT,
    path TEXT NOT NULL,
    size_bytes INTEGER,
    pages INTEGER,
    has_text INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued | processing | done | rejected | failed | duplicate
    error TEXT,
    duplicate_of INTEGER,
    carrier TEXT,
    account_name TEXT,
    account_number TEXT,
    customer_name TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    period_start TEXT,
    period_end TEXT,
    period_month TEXT,                  -- YYYY-MM used for charts
    total_excl_gst REAL,
    gst REAL,
    total_incl_gst REAL,
    line_count INTEGER,
    recon_ok INTEGER,
    recon_diff REAL,
    confidence REAL,
    verified INTEGER NOT NULL DEFAULT 0,
    extraction_json TEXT,
    provider TEXT,
    model TEXT,
    duration_s REAL,
    created_at REAL NOT NULL,
    processed_at REAL
);
CREATE INDEX IF NOT EXISTS ix_invoices_owner ON invoices(owner_email);
CREATE INDEX IF NOT EXISTS ix_invoices_session ON invoices(session_id);
CREATE INDEX IF NOT EXISTS ix_invoices_status ON invoices(status);

CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    carrier TEXT,
    account_number TEXT,
    period_month TEXT,
    number TEXT,
    user_name TEXT,
    plan_name TEXT,
    plan_cost REAL,
    total REAL,
    data_used_mb REAL,
    data_allowance_mb REAL,             -- NULL unknown, -1 unlimited
    voice_minutes REAL,
    sms_count REAL,
    roaming_cost REAL,
    overage_cost REAL,
    addon_cost REAL,
    hardware_cost REAL,
    other_cost REAL,
    discount REAL
);
CREATE INDEX IF NOT EXISTS ix_lines_invoice ON lines(invoice_id);

CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    line_id INTEGER REFERENCES lines(id) ON DELETE CASCADE,
    number TEXT,
    description TEXT,
    category TEXT,                      -- plan | addon | usage | roaming | hardware | one_off | discount | account | other
    amount REAL
);
CREATE INDEX IF NOT EXISTS ix_charges_invoice ON charges(invoice_id);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    number TEXT,
    description TEXT,
    monthly_amount REAL,
    payments_remaining INTEGER,
    remaining_balance REAL,
    end_date TEXT
);

-- What the analyser has learned about each carrier's bills
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY,
    carrier TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- plan | charge | note
    key TEXT NOT NULL,
    value TEXT,
    seen_count INTEGER NOT NULL DEFAULT 1,
    approved INTEGER NOT NULL DEFAULT 1,
    source_invoice INTEGER,
    updated_at REAL NOT NULL,
    UNIQUE(carrier, kind, key)
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subtitle TEXT,
    details TEXT,
    badge TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS offer_requests (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- quote | offer | call
    offer_id INTEGER,
    phone TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    sent_to TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY,
    purpose TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    invoice_id INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    email TEXT,
    session_id TEXT,
    meta TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0:
            now = time.time()
            for i, (title, subtitle, badge) in enumerate([
                ("3 months free", "on every line you bring across on a 24-month business plan", "Popular"),
                ("6 months free", "for teams moving 10 or more lines", "Teams 10+"),
                ("$2,000 sign-on credit", "to help clear interest-free hardware on your current account", "Hardware"),
            ]):
                c.execute(
                    "INSERT INTO offers(title, subtitle, badge, sort, created_at) VALUES (?,?,?,?,?)",
                    (title, subtitle, badge, i, now),
                )


def q(sql, args=()):
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    with tx() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def get_setting(key):
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    if row and row["value"] not in (None, ""):
        return row["value"]
    return SETTING_DEFAULTS.get(key, "")


def set_setting(key, value):
    x("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def log_event(kind, email=None, session_id=None, **meta):
    x(
        "INSERT INTO events(kind, email, session_id, meta, created_at) VALUES (?,?,?,?,?)",
        (kind, (email or None) and email.lower(), session_id, json.dumps(meta) if meta else None, time.time()),
    )
