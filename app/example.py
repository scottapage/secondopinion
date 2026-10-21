"""A made-up business ("Harbourside Electrical Ltd") whose bills are seeded through the real storage and
analysis pipeline, so the public example page looks exactly like a real result."""
import json
import random
import time

from . import db, extract

EMAIL = "example@secondopinion.co.nz"
MONTHS = ["2026-06", "2026-07", "2026-08", "2026-09"]

# number, user, plan, plan price, allowance MB (-1 unlimited), typical GB used, extras by month index
LINES = [
    ("0211234001", "Mere Tane", "Business Unlimited (Plan Leader)", 75.0, -1, 62, {}),
    ("0211234002", "James Wilson", "Business Unlimited Team", 40.0, -1, 48, {2: [("Roaming - USA daily pass x6", "roaming", 48.0)]}),
    ("0211234003", "Priya Nair", "Business Unlimited Team", 40.0, -1, 21, {}),
    ("0211234004", "Tom Harris", "Business Unlimited Team", 40.0, -1, 3.1, {}),
    ("0211234005", "Aroha Smith", "Business 10GB", 42.0, 10240, 16.5,
     {0: [("Extra 10GB data pack", "addon", 15.0)], 1: [("Extra 10GB data pack", "addon", 15.0)],
      2: [("Extra 10GB data pack", "addon", 15.0)], 3: [("Extra 10GB data pack", "addon", 15.0)]}),
    ("0211234006", "Ben Clarke", "Business 10GB", 42.0, 10240, 0.6, {}),
    ("0211234007", "Site van tablet", "Business Unlimited Data", 35.0, -1, 0.05, {}),
    ("0211234008", "Lucy Chen", "Business 100GB (Plan Leader)", 62.0, 102400, 7.2, {}),
    ("0211234009", "Sam Patel", "Business 10GB", 42.0, 10240, 4.4,
     {1: [("International calls - India", "usage", 23.6)], 3: [("International calls - India", "usage", 18.9)]}),
    ("0211234010", "Spare SIM (reception)", "Business 10GB", 42.0, 10240, 0.0, {}),
]
DEVICES = {"0211234001": ("iPhone 17 Pro 256GB", 63.88, 22), "0211234002": ("Samsung Galaxy S26", 41.67, 9)}

NOTES = [
    {"title": "Two connections are paid for but unused",
     "body": "The reception spare SIM used no data or calls at all, and the site van tablet used about 50MB a month. "
             "Together they cost $77/mth. Cancelling both would save about $920 a year - and Ben Clarke (0.6GB a month) "
             "is worth a check too.",
     "est_monthly_saving": 77},
    {"title": "Aroha needs a bigger plan, not more data packs",
     "body": "Aroha Smith bought an extra 10GB data pack every month ($15) on top of her $42 Business 10GB plan - $57/mth "
             "in total. Business Unlimited Team at $40/mth would cover her ~16.5GB use and save $17/mth.",
     "est_monthly_saving": 17},
    {"title": "James's US trip cost $48 in roaming",
     "body": "James Wilson used six USA daily passes in August. If US travel is regular, an international roaming pack or "
             "travel add-on bought before the trip is usually cheaper than daily passes.",
     "est_monthly_saving": 8},
    {"title": "Unlimited may be more than Tom and Priya need",
     "body": "Tom Harris averages 3GB a month and Priya Nair 21GB, both on Business Unlimited Team ($40). Moving them to "
             "Business 100GB Team ($32 each) would keep plenty of headroom and save $16/mth.",
     "est_monthly_saving": 16},
]


def _bill(month_idx):
    rnd = random.Random(month_idx)
    lines, total = [], 0.0
    for number, user, plan, price, allowance, gb, extras in LINES:
        charges = [{"description": f"{plan} (Mobile Plan)", "category": "plan", "amount": price}]
        if number in DEVICES:
            name, monthly, left = DEVICES[number]
            charges.append({"description": f"{name} interest free repayment", "category": "hardware", "amount": monthly})
        for desc, cat, amt in extras.get(month_idx, []):
            charges.append({"description": desc, "category": cat, "amount": amt})
        used = max(gb * rnd.uniform(0.85, 1.15), 0) * 1024 if gb else 0
        line_total = round(sum(c["amount"] for c in charges), 2)
        total += line_total
        lines.append({"number": number, "user_name": user, "plan_name": plan, "plan_cost": price,
                      "total_excl_gst": line_total, "data_used_mb": round(used), "data_allowance_mb": allowance,
                      "voice_minutes": round(rnd.uniform(40, 600)) if "Spare" not in user and "tablet" not in user else 0,
                      "sms_count": None, "charges": charges})
    account = [{"description": "Business Fibre 900 - 12 Quay Street", "category": "non_mobile", "amount": 120.0},
               {"description": "Mobile Bundle Discount (credit)", "category": "non_mobile", "amount": -10.0}]
    total += 110.0
    m = MONTHS[month_idx]
    return {
        "is_mobile_bill": True, "carrier": "2degrees", "rejection_reason": None,
        "account_name": "Harbourside Electrical Ltd (example)", "account_number": "40000001",
        "customer_name": "Harbourside Electrical Ltd", "invoice_number": f"EX-{m.replace('-', '')}",
        "invoice_date": f"{m}-28", "period_start": f"{m}-01", "period_end": f"{m}-28",
        "total_excl_gst": round(total, 2), "gst": round(total * 0.15, 2), "total_incl_gst": round(total * 1.15, 2),
        "lines": lines, "account_charges": account,
        "services": [{"service_type": "broadband", "address": "12 Quay Street, Auckland", "plan_name": "Business Fibre 900",
                      "speed": "Fibre 900", "amount": 110.0}],
        "devices": [{"number": n, "description": d[0], "monthly_amount": d[1], "payments_remaining": d[2] - month_idx,
                     "remaining_balance": round(d[1] * (d[2] - month_idx), 2)} for n, d in DEVICES.items()],
        "learned": [], "notes": "example",
    }


def seed(force=False):
    """Create the example business once (or rebuild it with force=True)."""
    exists = db.q1("SELECT COUNT(*) n FROM invoices WHERE owner_email=?", (EMAIL,))["n"]
    if exists and not force:
        return
    with db.tx() as c:
        c.execute("DELETE FROM invoices WHERE owner_email=?", (EMAIL,))
    now = time.time()
    for i in range(len(MONTHS)):
        iid = db.x("INSERT INTO invoices(owner_email, sha256, filename, path, status, has_text, recon_ok, confidence,"
                   " provider, model, created_at, processed_at) VALUES (?,?,?,?,'done',1,1,1,'example','example',?,?)",
                   (EMAIL, f"example-{i}", f"example-{MONTHS[i]}.pdf", "-", now, now))
        extract.store(iid, _bill(i))
    db.x("INSERT INTO customer_notes(owner, notes, updated_at) VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET"
         " notes=excluded.notes, updated_at=excluded.updated_at", (EMAIL, json.dumps(NOTES), now))
