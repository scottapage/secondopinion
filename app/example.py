"""A made-up business ("Harbourside Electrical Ltd") whose bills are seeded through the real storage and
analysis pipeline, so the public example page looks exactly like a real result. The example is a
two-provider estate (One NZ mobiles and broadband, plus a few Spark lines) so the switch scenario means something."""
import json
import random
import time

from . import db, extract

EMAIL = "example@secondopinion.co.nz"
MONTHS = ["2026-06", "2026-07", "2026-08", "2026-09"]

# carrier, account, number, user, plan, plan price, allowance MB (-1 unlimited), typical GB used, extras by month
ONE, SPARK = ("one", "401234567"), ("spark", "88 2211 3344")
LINES = [
    (ONE, "0211234001", "Mere Tane", "One Plan", 76.52, -1, 62, {}),
    (ONE, "0211234002", "James Wilson", "Companion One Plan", 36.52, -1, 48, {2: [("Daily roaming charge (USA) x6", "roaming", 52.17)]}),
    (ONE, "0211234003", "Priya Nair", "Companion One Plan", 36.52, -1, 21, {}),
    (ONE, "0211234004", "Tom Harris", "Companion One Plan", 36.52, -1, 3.1, {}),
    (ONE, "0211234005", "Aroha Smith", "Endless Starter", 43.47, 4096, 9.8,
     {i: [("Data Pack 5GB", "addon", 13.04)] for i in range(4)}),
    (ONE, "0211234006", "Ben Clarke", "Endless Starter", 43.47, 4096, 0.6, {}),
    (ONE, "0211234007", "Site van tablet", "One Data-Only", 40.0, -1, 0.05, {}),
    (SPARK, "0271234008", "Lucy Chen", "Business Endless 150GB - Team Up (You + Two)", 51.8, 153600, 7.2, {}),
    (SPARK, "0271234009", "Sam Patel", "Business Endless 150GB - Team Up (You + Two)", 51.8, 153600, 4.4,
     {1: [("International calls - India", "usage", 23.6)], 3: [("International calls - India", "usage", 18.9)]}),
    (SPARK, "0271234010", "Spare SIM (reception)", "Business Endless 8GB", 47.0, 8192, 0.0, {}),
]
DEVICES = {"0211234001": ("iPhone 17 Pro 256GB", 63.88, 22), "0211234002": ("Samsung Galaxy S26", 41.67, 9)}

NOTES = [
    {"title": "Two connections are paid for but unused",
     "body": "The reception spare SIM used no data or calls at all, and the site van tablet used about 50MB a month. "
             "Together they cost $87/mth. Cancelling both would save about $1,040 a year - and Ben Clarke (0.6GB a month "
             "on a $43 plan) is worth a look too.",
     "est_monthly_saving": 87},
    {"title": "Aroha buys a data pack every single month",
     "body": "Aroha Smith has bought a 5GB Data Pack ($13.04) every month on top of her Endless Starter plan - $56.51/mth "
             "all up for about 10GB. A 2degrees Business 100GB Team line at $32 covers her ten times over and saves $24/mth.",
     "est_monthly_saving": 24},
    {"title": "Two providers, two bills, no team price",
     "body": "Seven lines sit with One NZ and three with Spark, so the Spark lines only get a 'You + Two' discount and the "
             "One NZ lines a separate companion structure. On one 2degrees account all ten lines share one Plan Leader "
             "and nine Team lines, and the like-for-like total drops by about $50/mth before any right-sizing.",
     "est_monthly_saving": 50},
    {"title": "James's US trip cost $52 in roaming",
     "body": "James Wilson used six USA daily passes in August. On 2degrees, roaming is $8/day in 100+ countries and free in "
             "Australia; a travel pack bought before the trip is cheaper still.",
     "est_monthly_saving": 8},
    {"kind": "fact", "title": "Companion plans depend on one lead line",
     "body": "Three Companion One Plans at $36.52 only exist because Mere's One Plan ($76.52) is the primary. Cancel the "
             "primary and the companions reprice to full One Plans."},
    {"kind": "fact", "title": "Spark's Team Up discount is set by headcount",
     "body": "The three Spark lines get 30% off as a 'You + Two' group. Drop one line and the remaining two fall back to the "
             "'You + One' 20% tier - $7.40 more per line per month."},
    {"kind": "fact", "title": "Broadband is billed a month ahead of mobile",
     "body": "The One NZ bill charges Fibre Everyday for the month ahead while mobile plans are for the month just gone, so a "
             "cancellation date lands differently for each."},
]


def _bill(carrier, account, month_idx):
    rnd = random.Random(month_idx * 7 + len(account))
    lines, total = [], 0.0
    for (car, acc), number, user, plan, price, allowance, gb, extras in LINES:
        if (car, acc) != (carrier, account):
            continue
        charges = [{"description": f"{plan}", "category": "plan", "amount": price}]
        if number in DEVICES:
            name, monthly, left = DEVICES[number]
            charges.append({"description": f"{name} interest free payment", "category": "hardware", "amount": monthly})
        for desc, cat, amt in extras.get(month_idx, []):
            charges.append({"description": desc, "category": cat, "amount": amt})
        used = max(gb * rnd.uniform(0.85, 1.15), 0) * 1024 if gb else 0
        line_total = round(sum(c["amount"] for c in charges), 2)
        total += line_total
        lines.append({"number": number, "user_name": user, "plan_name": plan, "plan_cost": price,
                      "total_excl_gst": line_total, "data_used_mb": round(used), "data_allowance_mb": allowance,
                      "voice_minutes": round(rnd.uniform(40, 600)) if "Spare" not in user and "tablet" not in user else 0,
                      "sms_count": None, "charges": charges})
    account_charges, services = [], []
    if carrier == "one":
        account_charges = [{"description": "Fibre Everyday (Unlimited Broadband) - 12 Quay Street", "category": "non_mobile",
                            "amount": 87.83}]
        services = [{"service_type": "broadband", "address": "12 Quay Street, Auckland", "plan_name": "Fibre Everyday",
                     "speed": "Fibre 300", "amount": 87.83}]
        total += 87.83
    m = MONTHS[month_idx]
    name = {"one": "One NZ", "spark": "Spark"}[carrier]
    return {
        "is_mobile_bill": True, "carrier": carrier, "rejection_reason": None,
        "account_name": "Harbourside Electrical Ltd (example)", "account_number": account,
        "customer_name": "Harbourside Electrical Ltd", "invoice_number": f"EX-{carrier}-{m.replace('-', '')}",
        "invoice_date": f"{m}-28", "period_start": f"{m}-01", "period_end": f"{m}-28",
        "total_excl_gst": round(total, 2), "gst": round(total * 0.15, 2), "total_incl_gst": round(total * 1.15, 2),
        "lines": lines, "account_charges": account_charges, "services": services,
        "devices": [{"number": n, "description": d[0], "monthly_amount": d[1], "payments_remaining": d[2] - month_idx,
                     "remaining_balance": round(d[1] * (d[2] - month_idx), 2)}
                    for n, d in DEVICES.items() if any(l["number"] == n for l in lines)],
        "learned": [], "notes": f"example {name}",
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
        for carrier, account in (ONE, SPARK):
            iid = db.x("INSERT INTO invoices(owner_email, sha256, filename, path, status, has_text, recon_ok, confidence,"
                       " provider, model, created_at, processed_at) VALUES (?,?,?,?,'done',1,1,1,'example','example',?,?)",
                       (EMAIL, f"example-{carrier}-{i}", f"example-{carrier}-{MONTHS[i]}.pdf", "-", now, now))
            extract.store(iid, _bill(carrier, account, i))
    db.x("INSERT INTO customer_notes(owner, notes, updated_at) VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET"
         " notes=excluded.notes, updated_at=excluded.updated_at", (EMAIL, json.dumps(NOTES), now))
    db.set_setting("example_version", "3")
