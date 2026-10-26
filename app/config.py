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
    "ai_provider": "claude",  # claude | openai | openai_fallback | local | local_fallback  (bill reader)
    "learning_provider": "claude",  # claude | openai  (audit, format guide, expert review, parser recipes)
    "openai_base_url": "",  # any OpenAI-compatible API, e.g. https://api.openai.com/v1, https://openrouter.ai/api/v1
    "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
    "openai_model": "",
    "openai_temperature": "",  # blank = let the model default (Kimi K3 only accepts 1)
    "openai_vision": "on",  # send page images for scanned bills (model must accept image_url)
    "openai_vision_always": "off",  # send page images for every bill, not just scanned ones
    "openai_price_in": "0",  # USD per million input tokens, for the cost log
    "openai_price_out": "0",
    "claude_model": "claude-sonnet-5-5",  # bill reading on Claude (fallback when the OpenAI-compatible reader fails)
    "chat_model": "claude-sonnet-5-5",  # Ask AI
    "agent_model": "claude-sonnet-5-5",  # plan catalogue / benefits research agents (tool use)
    "audit_rounds": "3",  # learning: re-audit a bill until the auditor finds nothing, up to this many rounds
    "local_ai_url": "",  # e.g. http://macbook.tailnet.ts.net:1234/v1
    "local_ai_model": "qwen3.8-27b-splash",
    "learning_mode": "on",  # audit + format guide + analysis review on every bill
    "catalogue_days": "7",  # re-read a provider's plan pages at least this often; every night if the pages changed
    "catalogue_hour": "2",  # NZ hour for the nightly catalogue run
    "parser_min_bills": "10",  # audited bills needed before generating a deterministic parser
    "parser_activate_streak": "10",  # consecutive shadow matches before a parser can be activated
    "site_lock": "off",  # front-end password: visitors see a blurred page and a password prompt; emails are held
    "site_password": "scottpage",
    "alerts": "on",  # email the superadmin the moment a stranger uploads a bill, registers or asks for an offer
    "digest": "on",  # 7am NZ daily digest email
    "alert_email": "",  # defaults to the superadmin
    "own_emails": "s.page@centastone.co.nz",  # comma-separated: the superadmin's own test addresses (no alerts)
}

SECRET_SETTINGS = {"anthropic_api_key", "mail_password", "openai_api_key"}

# Providers we recognise on bills. The big three have plan catalogues and benefits research; the rest are
# read the same way (mobile and/or broadband), and anything else arrives as "other" with the name from the bill.
CARRIERS = {
    "spark": "Spark",
    "one": "One NZ",
    "2degrees": "2degrees",
    "skinny": "Skinny",
    "mercury": "Mercury",
    "contact": "Contact",
    "electric_kiwi": "Electric Kiwi",
    "nova": "Nova Energy",
    "trustpower": "Trustpower",
    "genesis": "Genesis",
    "kogan": "Kogan",
    "slingshot": "Slingshot",
    "orcon": "Orcon",
    "flip": "Flip",
    "now": "NOW",
    "voyager": "Voyager",
    "sky": "Sky Broadband",
    "compass": "Compass",
    "myrepublic": "MyRepublic",
    "wireless_nation": "Wireless Nation",
    "starlink": "Starlink",
    "zeronet": "Zeronet",
    "quic": "Quic",
    "bigpipe": "Bigpipe",
    "vocus": "Vocus",
    "rocket": "Rocket Mobile",
    "warehouse": "Warehouse Mobile",
    "other": "Other provider",
}
BIG3 = ("spark", "one", "2degrees")


def carrier_label(code, name=None):
    """Display name for a provider code; `name` is the free-text name read off an "other" bill."""
    if code in CARRIERS and code != "other":
        return CARRIERS[code]
    if name:
        return name
    return CARRIERS.get(code) or (code or "Unknown").replace("_", " ").title()

MAX_UPLOAD_MB = 50
