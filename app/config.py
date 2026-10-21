import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
INVOICE_DIR = DATA_DIR / "invoices"
CHART_DIR = DATA_DIR / "charts"
DB_PATH = DATA_DIR / "secondopinion.sqlite"
for d in (DATA_DIR, INVOICE_DIR, CHART_DIR):
    d.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "scott.page@me.com").lower()
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
SETUP_TOKEN = os.environ.get("SETUP_TOKEN", "")

# Defaults for settings that the superadmin can override in the admin console
# (stored in the settings table). Secrets fall back to environment variables.
SETTING_DEFAULTS = {
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    "mail_user": os.environ.get("MAIL_USER", "mobile@secondopinion.co.nz"),
    "mail_address": os.environ.get("MAIL_ADDRESS", "mobile@secondopinion.co.nz"),
    "mail_password": os.environ.get("MAIL_PASSWORD", ""),
    "imap_host": os.environ.get("IMAP_HOST", "imap.fastmail.com"),
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.fastmail.com"),
    "mail_polling": "on",
    "sales_email": SUPERADMIN_EMAIL,
    "booking_url": "",
    "ai_provider": "claude",  # claude | local | local_fallback
    "claude_model": "claude-opus-5-5",
    "chat_model": "claude-opus-5-5",
    "local_ai_url": "",  # e.g. http://macbook.tailnet.ts.net:1234/v1
    "local_ai_model": "qwen3.8-27b-splash",
}

SECRET_SETTINGS = {"anthropic_api_key", "mail_password"}

CARRIERS = {
    "spark": "Spark",
    "one": "One NZ",
    "2degrees": "2degrees",
}

MAX_UPLOAD_MB = 50
