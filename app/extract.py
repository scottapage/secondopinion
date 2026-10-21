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

CATEGORIES = ["plan", "addon", "usage", "roaming", "hardware", "one_off", "discount", "account", "other"]

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
        "carrier": {"type": "string", "enum": ["spark", "one", "2degrees", "other"]},
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
    "required": ["is_mobile_bill", "carrier", "rejection_reason", "account_name", "account_number", "customer_name",
                 "invoice_number", "invoice_date", "period_start", "period_end", "total_excl_gst", "gst",
                 "total_incl_gst", "lines", "account_charges", "devices", "learned", "notes"],
    "additionalProperties": False,
}

BASE_PROMPT = """You extract structured billing data from New Zealand business mobile phone invoices.
Supported carriers: Spark ("spark"), One NZ - formerly Vodafone NZ ("one"), and 2degrees ("2degrees").

Rules:
- If the document is not a mobile/telco invoice, set is_mobile_bill=false and explain in rejection_reason.
- If it is a telco invoice from another provider, set carrier="other".
- All money is NZD and EXCLUDING GST. If the bill only shows GST-inclusive amounts, divide by 1.15.
- Discounts and credits are negative amounts with category "discount".
- Dates are ISO YYYY-MM-DD. period_start/period_end describe the main service period being billed.
- One entry in `lines` per mobile number/connection. `number` is the phone number as printed (digits, no spaces).
  user_name is the person/label attached to the number, if shown.
- Each line's `charges` must add up to its total_excl_gst. Charges not tied to a number go in account_charges.
- Charge categories: plan (recurring plan/access fee), addon (recurring bolt-ons, extra data packs, insurance),
  usage (pay-as-you-go calls/texts/data/premium/international calls from NZ), roaming (any usage or day passes overseas),
  hardware (device/handset repayments), one_off (connection, SIM, admin fees), discount, account (account-level fees),
  other.
- data in MB (1 GB = 1024 MB). data_allowance_mb = -1 for unlimited, null if not stated.
- voice_minutes: total call minutes used on the line. sms_count: texts sent.
- devices: any handset/hardware repayment plans, with remaining payments/balance where printed.
- `learned`: short reusable observations about this carrier's bill format that would help extract the next bill
  from the same carrier - e.g. kind="plan" key="<plan name>" value="<monthly price excl GST and allowance>",
  kind="charge" key="<charge description pattern>" value="<category>", kind="note" for layout tips. Max 15.
- Text fields that are not shown on the bill are empty strings. plan_cost is 0 if not itemised.
- Be exhaustive: include every line on the bill, even if it has no usage. Never invent numbers."""


def detect_carrier(text):
    t = (text or "").lower()
    scores = {
        "spark": len(re.findall(r"spark new zealand|spark\.co\.nz|spark business", t)),
        "one": len(re.findall(r"one new zealand|one\.nz|vodafone", t)),
        "2degrees": len(re.findall(r"2degrees", t)),
    }
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
    out = []
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
    for group in ("lines", "devices"):
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
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": instr},
    ]
    data, model = ai.claude_json(system, content, SCHEMA, "extract", invoice_id, effort=effort)
    return normalise(data), model


def extract(path, text, carrier_hint, invoice_id):
    setting = db.get_setting("ai_provider")
    provider = "claude"
    if setting in ("local", "local_fallback") and text and len(text) > 200 and ai.local_available():
        provider = "local"
    try:
        data, model = _run_extraction(path, text, carrier_hint, provider, invoice_id)
    except Exception:
        if provider != "local":
            raise
        provider = "claude"
        data, model = _run_extraction(path, text, carrier_hint, provider, invoice_id)

    ok, diff = reconcile(data)
    if data.get("is_mobile_bill") and data.get("carrier") != "other" and not ok and diff is not None:
        # Self-correction pass with the discrepancy pointed out (always on Claude, higher effort)
        fb = (f"A previous extraction of this bill produced line totals plus account charges that differ from the "
              f"invoice total (excl GST) by {diff:+.2f}. Re-check every line and charge carefully - look for missed "
              f"lines, account-level charges, credits, or GST-inclusive figures.")
        try:
            data2, model2 = _run_extraction(path, text, carrier_hint, "claude", invoice_id, fb, effort="high")
            ok2, diff2 = reconcile(data2)
            if diff2 is not None and abs(diff2) < abs(diff):
                data, model, ok, diff, provider = data2, model2, ok2, diff2, "claude"
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
            "UPDATE invoices SET carrier=?, account_name=?, account_number=?, customer_name=?, invoice_number=?,"
            " invoice_date=?, period_start=?, period_end=?, period_month=?, total_excl_gst=?, gst=?, total_incl_gst=?,"
            " line_count=?, extraction_json=? WHERE id=?",
            (data.get("carrier"), data.get("account_name"), data.get("account_number"), data.get("customer_name"),
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

        if not data.get("is_mobile_bill"):
            reason = data.get("rejection_reason") or "This doesn't look like a mobile phone bill."
            db.x("UPDATE invoices SET status='rejected', error=?, processed_at=?, provider=?, model=? WHERE id=?",
                 (reason, time.time(), provider, model, invoice_id))
            return
        if data.get("carrier") == "other":
            db.x("UPDATE invoices SET status='rejected', error=?, processed_at=?, provider=?, model=? WHERE id=?",
                 ("We currently analyse Spark, One NZ and 2degrees bills only.", time.time(), provider, model,
                  invoice_id))
            return

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
    except Exception as e:
        traceback.print_exc()
        db.x("UPDATE invoices SET status='failed', error=?, processed_at=? WHERE id=?",
             (f"{type(e).__name__}: {e}"[:500], time.time(), invoice_id))
