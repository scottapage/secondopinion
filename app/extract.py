"""Bill extraction pipeline: PDF -> structured billing records.

Accuracy improves over time through three mechanisms:
  1. Carrier knowledge base - plan names/prices and charge categorisations learned
     from every bill that reconciles are fed back into the extraction prompt.
  2. Verified examples - bills the superadmin marks as verified are used as
     reference extractions for later bills from the same carrier.
  3. Self-correction - if line items don't add up to the invoice total, the bill
     is re-extracted with the discrepancy pointed out.
"""
import base64
import json
import re
import time
import traceback

import pdfplumber

from . import ai, db
from .config import CARRIERS

CATEGORIES = ["plan", "addon", "usage", "roaming", "hardware", "one_off", "discount", "account", "non_mobile", "non_telco",
              "other"]

NUM = {"type": ["number", "null"]}
STR = {"type": "string"}  # empty string when not shown (union types are limited)


def _charge_schema():
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORIES},
            "amount": {"type": "number"},
        },
        "required": ["description", "category", "amount"],
        "additionalProperties": False,
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "is_mobile_bill": {"type": "boolean"},
        "carrier": {"type": "string", "enum": list(CARRIERS)},
        "carrier_name": STR,
        "rejection_reason": STR,
        "account_name": STR,
        "account_number": STR,
        "customer_name": STR,
        "invoice_number": STR,
        "invoice_date": STR,
        "period_start": STR,
        "period_end": STR,
        "total_excl_gst": {"type": "number"},
        "gst": {"type": "number"},
        "total_incl_gst": {"type": "number"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "user_name": STR,
                    "plan_name": STR,
                    "plan_cost": {"type": "number"},
                    "total_excl_gst": {"type": "number"},
                    "data_used_mb": NUM,
                    "data_allowance_mb": NUM,
                    "voice_minutes": NUM,
                    "sms_count": NUM,
                    "charges": {"type": "array", "items": _charge_schema()},
                },
                "required": ["number", "user_name", "plan_name", "plan_cost", "total_excl_gst", "data_used_mb",
                             "data_allowance_mb", "voice_minutes", "sms_count", "charges"],
                "additionalProperties": False,
            },
        },
        "account_charges": {"type": "array", "items": _charge_schema()},
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service_type": {"type": "string", "enum": ["broadband", "landline", "other"]},
                    "address": {"type": "string"},
                    "plan_name": {"type": "string"},
                    "speed": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["service_type", "address", "plan_name", "speed", "amount"],
                "additionalProperties": False,
            },
        },
        "devices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": STR,
                    "description": {"type": "string"},
                    "monthly_amount": NUM,
                    "payments_remaining": {"type": ["integer", "null"]},
                    "remaining_balance": NUM,
                },
                "required": ["number", "description", "monthly_amount", "payments_remaining", "remaining_balance"],
                "additionalProperties": False,
            },
        },
        "learned": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["plan", "charge", "note"]},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["kind", "key", "value"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["is_mobile_bill", "carrier", "carrier_name", "rejection_reason", "account_name", "account_number", "customer_name",
                 "invoice_number", "invoice_date", "period_start", "period_end", "total_excl_gst", "gst",
                 "total_incl_gst", "lines", "account_charges", "services", "devices", "learned", "notes"],
    "additionalProperties": False,
}

BASE_PROMPT = """You extract structured billing data from New Zealand business and consumer telco invoices: mobile and/or
broadband (fibre, wireless, satellite) and landlines, from ANY provider. Provider codes: spark (Spark, incl. Spark
Business), one (One NZ, formerly Vodafone NZ), 2degrees, skinny, mercury, contact (Contact Energy), electric_kiwi,
nova (Nova Energy), trustpower, genesis, kogan, slingshot, orcon, flip, now, voyager, sky (Sky Broadband), compass,
myrepublic, wireless_nation, starlink, zeronet, quic, bigpipe, vocus, rocket (Rocket Mobile), warehouse (Warehouse
Mobile). Anything else: carrier="other" with the provider's name in carrier_name.

Rules:
- is_mobile_bill is true for ANY invoice that bills a telecommunications service: mobile lines, broadband, wireless
  broadband, satellite or landlines - including an energy retailer's bill (Mercury, Contact, Electric Kiwi, Nova,
  Genesis, Trustpower...) that has a broadband line on it. A broadband-only bill is still true: `lines` is empty
  and `services` holds the broadband. Set is_mobile_bill=false (with rejection_reason) only when the document has
  no telecommunications service at all, or is not an invoice.
- All money is NZD and EXCLUDING GST. If the bill only shows GST-inclusive amounts, divide by 1.15.
- Discounts and credits are negative amounts with category "discount".
- Dates are ISO YYYY-MM-DD. period_start/period_end describe the main service period being billed.
- One entry in `lines` per mobile number/connection. `number` is the phone number as printed (digits, no spaces).
  user_name is the person/label attached to the number, if shown.
- Each line's `charges` must add up to its total_excl_gst. Charges not tied to a number go in account_charges.
- Charge categories: plan (recurring plan/access fee), addon (recurring bolt-ons, extra data packs, insurance),
  usage (pay-as-you-go calls/texts/data/premium/international calls from NZ), roaming (any usage or day passes overseas),
  hardware (device/handset repayments), one_off (connection, SIM, admin fees), discount, account (account-level fees,
  late payment fees), non_mobile (telecommunications services that are not mobile but are on the same bill -
  broadband, fibre, wireless broadband, satellite, landlines, phone systems, modem/router rental - INCLUDING discounts
  and credits tied to those services, as negative amounts), non_telco (electricity, gas, LPG, water, solar, EV
  charging, insurance and any other non-telecommunications charge on a combined bill, including their discounts and
  credits), other. Put non_mobile and non_telco charges in account_charges.
- services: one entry per non-mobile telecommunications service (each broadband connection, landline...) with its
  address, plan name, speed if stated (e.g. "Fibre 900", "Fibre Max", "Wireless", "Starlink"), and net monthly amount
  after the discounts tied to it. This summarises the non_mobile account_charges; it does not replace them. Energy
  and other non_telco charges never appear in services. Empty strings where not shown.
- total_excl_gst / gst / total_incl_gst are the invoice's own totals (they include any non_telco charges; the
  categories keep them apart).
- data in MB (1 GB = 1024 MB). data_allowance_mb = -1 for unlimited, null if not stated.
- voice_minutes: total call minutes used on the line. sms_count: texts sent.
- devices: any handset/hardware repayment plans, with remaining payments/balance where printed.
- `learned`: short reusable observations about this provider's bill format that would help extract the next bill
  from the same provider - e.g. kind="plan" key="<plan name>" value="<monthly price excl GST and allowance>",
  kind="charge" key="<charge description pattern>" value="<category>", kind="note" for layout tips. Max 15.
- Text fields that are not shown on the bill are empty strings. plan_cost is 0 if not itemised.
- Be exhaustive: include every line on the bill, even if it has no usage. Never invent numbers."""


CARRIER_PATTERNS = {
    "spark": r"spark new zealand|spark\.co\.nz|spark business",
    "one": r"one new zealand|one\.nz|vodafone",
    "2degrees": r"2degrees",
    "skinny": r"skinny\.co\.nz|skinny mobile|skinny broadband",
    "mercury": r"mercury\.co\.nz|mercury nz|mercury energy",
    "contact": r"contact\.co\.nz|contact energy",
    "electric_kiwi": r"electric kiwi|electrickiwi",
    "nova": r"nova energy|novaenergy",
    "trustpower": r"trustpower",
    "genesis": r"genesis energy|genesisenergy",
    "kogan": r"kogan",
    "slingshot": r"slingshot",
    "orcon": r"orcon",
    "flip": r"flip\.co\.nz",
    "now": r"nownz|now\.nz|now broadband",
    "voyager": r"voyager",
    "sky": r"sky broadband|sky\.co\.nz|sky network television",
    "compass": r"compass communications|compass\.net\.nz",
    "myrepublic": r"myrepublic",
    "wireless_nation": r"wireless nation",
    "starlink": r"starlink",
    "zeronet": r"zeronet",
    "quic": r"quic\.nz",
    "bigpipe": r"bigpipe",
    "vocus": r"vocus",
    "rocket": r"rocket mobile",
    "warehouse": r"warehouse mobile",
}


def detect_carrier(text):
    t = (text or "").lower()
    scores = {c: len(re.findall(pat, t)) for c, pat in CARRIER_PATTERNS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def read_pdf(path):
    pages, text = 0, ""
    try:
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            parts = []
            for p in pdf.pages[:150]:
                parts.append(p.extract_text() or "")
            text = "\n\n".join(parts)
    except Exception:
        pass
    return pages, text


def knowledge_prompt(carrier):
    if not carrier:
        return ""
    rows = db.q(
        "SELECT kind, key, value, seen_count FROM knowledge WHERE carrier=? AND approved=1"
        " ORDER BY seen_count DESC LIMIT 120",
        (carrier,),
    )
    from . import learning
    out = [learning._guide_block(carrier)]
    if rows:
        out.append(f"\nWhat we have learned from previous {carrier} bills (seen N times):")
        for r in rows:
            out.append(f"- [{r['kind']}] {r['key']}: {r['value']} (x{r['seen_count']})")
    ex = db.q1(
        "SELECT extraction_json FROM invoices WHERE carrier=? AND verified=1 AND extraction_json IS NOT NULL"
        " ORDER BY processed_at DESC LIMIT 1",
        (carrier,),
    )
    if ex:
        data = json.loads(ex["extraction_json"])
        data["lines"] = data.get("lines", [])[:3]
        out.append("\nA human-verified extraction of an earlier bill from this carrier (first lines only)."
                   " Match its conventions:\n" + json.dumps(data, separators=(",", ":"))[:6000])
    return "\n".join(out)


def normalise(data):
    """Turn the empty-string placeholders the schema requires back into None."""
    for k, v in list(data.items()):
        if v == "":
            data[k] = None
    for group in ("lines", "devices", "services"):
        for item in data.get(group, []):
            for k, v in list(item.items()):
                if v == "":
                    item[k] = None
    return data


def reconcile(data):
    lines_total = sum((l.get("total_excl_gst") or 0) for l in data.get("lines", []))
    acct = sum((c.get("amount") or 0) for c in data.get("account_charges", []))
    total = data.get("total_excl_gst")
    if total is None:
        return False, None
    diff = round(lines_total + acct - total, 2)
    tol = max(1.0, abs(total) * 0.01)
    return abs(diff) <= tol, diff


def _run_extraction(path, text, carrier_hint, provider, invoice_id, feedback=None, effort="medium"):
    """provider: claude | local | openai."""
    system = BASE_PROMPT + knowledge_prompt(carrier_hint)
    instr = "Extract this invoice."
    if carrier_hint:
        instr += f" It appears to be a {carrier_hint} bill."
    if feedback:
        instr += "\n\n" + feedback
    if provider == "local":
        data, model = ai.local_json(system, instr + "\n\nInvoice text:\n" + text[:150000], SCHEMA, "extract",
                                    invoice_id)
        return normalise(data), model
    data, model = ai.json_call(system, instr, SCHEMA, "extract", invoice_id, pdf_path=path, pdf_text=text,
                               effort=effort, provider=provider)
    return normalise(data), model


def extract(path, text, carrier_hint, invoice_id):
    from . import learning
    if carrier_hint:
        parsed = learning.active_parse(carrier_hint, text)
        if parsed:
            ok, diff = reconcile(parsed)
            return parsed, "parser", f"parser:{carrier_hint}", ok, diff
    setting = db.get_setting("ai_provider")
    provider = "claude"
    if setting in ("local", "local_fallback") and text and len(text) > 200 and ai.local_available():
        provider = "local"
    elif setting in ("openai", "openai_fallback"):
        provider = "openai"
    try:
        data, model = _run_extraction(path, text, carrier_hint, provider, invoice_id)
    except Exception:
        if provider == "claude" or setting in ("local", "openai"):
            raise
        traceback.print_exc()
        provider = "claude"
        data, model = _run_extraction(path, text, carrier_hint, provider, invoice_id)

    ok, diff = reconcile(data)
    if data.get("is_mobile_bill") and not ok and diff is not None:
        # Self-correction pass with the discrepancy pointed out (higher effort; Claude unless the reader is pinned)
        fb = (f"A previous extraction of this bill produced line totals plus account charges that differ from the "
              f"invoice total (excl GST) by {diff:+.2f}. Re-check every line and charge carefully - look for missed "
              f"lines, account-level charges, credits, or GST-inclusive figures.")
        try:
            p2 = "openai" if setting == "openai" else "claude"
            data2, model2 = _run_extraction(path, text, carrier_hint, p2, invoice_id, fb, effort="high")
            ok2, diff2 = reconcile(data2)
            if diff2 is not None and abs(diff2) < abs(diff):
                data, model, ok, diff, provider = data2, model2, ok2, diff2, p2
        except Exception:
            traceback.print_exc()
    return data, provider, model, ok, diff


def _month(data):
    for k in ("period_end", "invoice_date", "period_start"):
        v = data.get(k)
        if v and re.match(r"\d{4}-\d{2}", v):
            return v[:7]
    return None


def _norm_charge(desc):
    d = re.sub(r"\d[\d/:.,-]*", "#", desc or "").strip()
    return re.sub(r"\s+", " ", d)[:120]


def learn(carrier, data, invoice_id):
    now = time.time()
    items = []
    for l in data.get("lines", []):
        if l.get("plan_name") and l.get("plan_cost") is not None:
            allowance = l.get("data_allowance_mb")
            al = "unlimited data" if allowance == -1 else (f"{allowance / 1024:.0f}GB data" if allowance else "")
            items.append(("plan", l["plan_name"].strip()[:120], f"${l['plan_cost']:.2f}/mth excl GST {al}".strip()))
        for c in l.get("charges", []):
            items.append(("charge", _norm_charge(c["description"]), c["category"]))
    for c in data.get("account_charges", []):
        items.append(("charge", _norm_charge(c["description"]), c["category"]))
    for li in data.get("learned", [])[:15]:
        items.append((li["kind"], li["key"].strip()[:160], li["value"].strip()[:300]))
    seen = set()
    with db.tx() as c:
        for kind, key, value in items:
            if not key or (kind, key) in seen:
                continue
            seen.add((kind, key))
            c.execute(
                "INSERT INTO knowledge(carrier, kind, key, value, source_invoice, updated_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(carrier, kind, key) DO UPDATE SET seen_count=seen_count+1, value=excluded.value,"
                " updated_at=excluded.updated_at",
                (carrier, kind, key, value, invoice_id, now),
            )


def store(invoice_id, data):
    """Replace the line/charge/device rows for an invoice from extracted data."""
    inv = db.q1("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    month = _month(data)
    with db.tx() as c:
        c.execute("DELETE FROM charges WHERE invoice_id=?", (invoice_id,))
        c.execute("DELETE FROM lines WHERE invoice_id=?", (invoice_id,))
        c.execute("DELETE FROM devices WHERE invoice_id=?", (invoice_id,))
        c.execute("DELETE FROM services WHERE invoice_id=?", (invoice_id,))
        for sv in data.get("services", []):
            c.execute("INSERT INTO services(invoice_id, carrier, period_month, service_type, address, plan_name, speed,"
                      " amount) VALUES (?,?,?,?,?,?,?,?)",
                      (invoice_id, data.get("carrier"), month, sv.get("service_type"), sv.get("address"),
                       sv.get("plan_name"), sv.get("speed"), sv.get("amount")))
        for l in data.get("lines", []):
            sums = {k: 0.0 for k in CATEGORIES}
            for ch in l.get("charges", []):
                sums[ch["category"]] = sums.get(ch["category"], 0) + (ch["amount"] or 0)
            cur = c.execute(
                "INSERT INTO lines(invoice_id, carrier, account_number, period_month, number, user_name, plan_name,"
                " plan_cost, total, data_used_mb, data_allowance_mb, voice_minutes, sms_count, roaming_cost,"
                " overage_cost, addon_cost, hardware_cost, other_cost, discount)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (invoice_id, data.get("carrier"), data.get("account_number"), month,
                 re.sub(r"\s+", "", l.get("number") or ""), l.get("user_name"), l.get("plan_name"),
                 l.get("plan_cost"), l.get("total_excl_gst"), l.get("data_used_mb"), l.get("data_allowance_mb"),
                 l.get("voice_minutes"), l.get("sms_count"), sums["roaming"], sums["usage"], sums["addon"],
                 sums["hardware"], sums["one_off"] + sums["other"] + sums["account"], sums["discount"]),
            )
            for ch in l.get("charges", []):
                c.execute(
                    "INSERT INTO charges(invoice_id, line_id, number, description, category, amount) VALUES (?,?,?,?,?,?)",
                    (invoice_id, cur.lastrowid, l.get("number"), ch["description"], ch["category"], ch["amount"]),
                )
        for ch in data.get("account_charges", []):
            c.execute(
                "INSERT INTO charges(invoice_id, description, category, amount) VALUES (?,?,?,?)",
                (invoice_id, ch["description"], ch["category"], ch["amount"]),
            )
        for d in data.get("devices", []):
            c.execute(
                "INSERT INTO devices(invoice_id, number, description, monthly_amount, payments_remaining,"
                " remaining_balance) VALUES (?,?,?,?,?,?)",
                (invoice_id, d.get("number"), d.get("description"), d.get("monthly_amount"),
                 d.get("payments_remaining"), d.get("remaining_balance")),
            )
        c.execute(
            "UPDATE invoices SET carrier=?, carrier_name=?, account_name=?, account_number=?, customer_name=?, invoice_number=?,"
            " invoice_date=?, period_start=?, period_end=?, period_month=?, total_excl_gst=?, gst=?, total_incl_gst=?,"
            " line_count=?, extraction_json=? WHERE id=?",
            (data.get("carrier"), data.get("carrier_name"), data.get("account_name"), data.get("account_number"), data.get("customer_name"),
             data.get("invoice_number"), data.get("invoice_date"), data.get("period_start"), data.get("period_end"),
             month, data.get("total_excl_gst"), data.get("gst"), data.get("total_incl_gst"),
             len(data.get("lines", [])), json.dumps(data), invoice_id),
        )
    return inv


def process_invoice(invoice_id):
    inv = db.q1("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not inv:
        return
    t0 = time.time()
    db.x("UPDATE invoices SET status='processing', error=NULL WHERE id=?", (invoice_id,))
    try:
        pages, text = read_pdf(inv["path"])
        db.x("UPDATE invoices SET pages=?, has_text=? WHERE id=?", (pages, int(len(text) > 200), invoice_id))
        hint = detect_carrier(text)
        data, provider, model, ok, diff = extract(inv["path"], text, hint, invoice_id)

        if not data.get("is_mobile_bill") or not (data.get("lines") or data.get("services")):
            reason = data.get("rejection_reason") or "We couldn't find a mobile or broadband service on this bill."
            db.x("UPDATE invoices SET status='rejected', error=?, processed_at=?, provider=?, model=? WHERE id=?",
                 (reason, time.time(), provider, model, invoice_id))
            _alert(invoice_id)
            return
        if data.get("carrier") not in CARRIERS:
            data["carrier"] = "other"

        # Same bill already analysed for this owner?
        owner_clause, owner_val = ("owner_email=?", inv["owner_email"]) if inv["owner_email"] else \
            ("session_id=?", inv["session_id"])
        dup = None
        if data.get("invoice_number") and owner_val:
            dup = db.q1(
                f"SELECT id FROM invoices WHERE {owner_clause} AND id<>? AND status='done' AND carrier=?"
                " AND invoice_number=? AND IFNULL(account_number,'')=IFNULL(?, '')",
                (owner_val, invoice_id, data.get("carrier"), data.get("invoice_number"), data.get("account_number")),
            )
        store(invoice_id, data)
        confidence = 0.97 if ok else max(0.3, 0.8 - min(abs(diff or 0) / max(abs(data.get("total_excl_gst") or 1), 1), 0.5))
        status = "duplicate" if dup else "done"
        db.x(
            "UPDATE invoices SET status=?, duplicate_of=?, recon_ok=?, recon_diff=?, confidence=?, provider=?, model=?,"
            " duration_s=?, processed_at=? WHERE id=?",
            (status, dup["id"] if dup else None, int(ok), diff, confidence, provider, model, time.time() - t0,
             time.time(), invoice_id),
        )
        if ok:
            learn(data["carrier"], data, invoice_id)
        db.log_event("invoice_processed", inv["owner_email"], inv["session_id"], invoice_id=invoice_id,
                     carrier=data.get("carrier"), recon_ok=ok)
        _alert(invoice_id)
        run_learning(invoice_id, inv, text, data)
    except Exception as e:
        traceback.print_exc()
        db.x("UPDATE invoices SET status='failed', error=?, processed_at=? WHERE id=?",
             (f"{type(e).__name__}: {e}"[:500], time.time(), invoice_id))
        _alert(invoice_id)


def _alert(invoice_id):
    from . import notify
    try:
        notify.bill_alert(invoice_id)
    except Exception:
        traceback.print_exc()


def run_learning(invoice_id, inv, text, data):
    """Learning mode: audit, update the format guide, review the analysis. The bill is already visible to the
    customer; summary emails wait for this to finish (learning_state)."""
    from . import learning
    if db.get_setting("learning_mode") != "on":
        return
    db.x("UPDATE invoices SET learning_state='running' WHERE id=?", (invoice_id,))
    try:
        fixed = learning.learn_from(invoice_id, inv["path"], text, data)
        if fixed is not data:
            store(invoice_id, fixed)
            ok, diff = reconcile(fixed)
            db.x("UPDATE invoices SET recon_ok=?, recon_diff=?, confidence=? WHERE id=?",
                 (int(ok), diff, 0.98 if ok else 0.6, invoice_id))
            if ok:
                learn(fixed["carrier"], fixed, invoice_id)
            data = fixed
        learning.after_store(invoice_id, data.get("carrier"), text, data, inv["owner_email"], inv["session_id"])
    finally:
        db.x("UPDATE invoices SET learning_state='done' WHERE id=?", (invoice_id,))
