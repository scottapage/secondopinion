"""Learning mode: every bill is audited, teaches a living format guide for its carrier,
and has its analysis reviewed by Claude. Once a carrier has enough audited bills, Claude
writes a declarative parsing recipe for it (data, applied by app.specparse - never executed code),
which is shadow-tested before it can take over."""
import base64
import json
import os
import subprocess
import sys
import time
import traceback

from . import ai, analysis, db
from .config import CARRIERS

# ---------- 1. Independent audit ----------

AUDIT_PROMPT = """You are auditing a structured extraction of a New Zealand telco invoice (mobile and/or broadband)
against the original PDF. Another model produced the extraction. Check it line by line against the document:
every mobile line present, numbers and names right, plan names and prices right, each charge in the right
category (non-mobile services such as broadband must be category non_mobile and listed in `services`),
discounts negative, totals and GST right, all amounts excluding GST.

Return:
- verdict: "correct" if nothing material is wrong, "corrected" if you found material errors, "uncertain" if the
  document is ambiguous.
- corrections: each material error (field path, old value, correct value, reason). Empty if correct.
- format_observations: short, reusable facts about how this carrier lays out its bills that would help the
  next extraction (where data usage appears, how discounts are shown, billing in advance vs arrears, quirks)."""

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "corrected", "uncertain"]},
        "corrections": {"type": "array", "items": {
            "type": "object",
            "properties": {"field": {"type": "string"}, "was": {"type": "string"}, "now": {"type": "string"},
                           "reason": {"type": "string"}},
            "required": ["field", "was", "now", "reason"], "additionalProperties": False}},
        "format_observations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "corrections", "format_observations"],
    "additionalProperties": False,
}


def audit(invoice_id, path, data):
    """Returns (data_to_use, audit_result)."""
    from . import extract
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": "Extraction to audit:\n" + json.dumps(data, separators=(",", ":"))},
    ]
    result, model = ai.claude_json(AUDIT_PROMPT + _guide_block(data.get("carrier")), content,
                                   AUDIT_SCHEMA, "audit", invoice_id, effort="high")
    applied = 0
    use = data
    if result["verdict"] == "corrected" and result["corrections"]:
        # Re-extract with the auditor's findings as instructions
        _, text = extract.read_pdf(path)
        fb = ("An independent audit of a previous extraction of this bill found these errors - apply them, and "
              "re-check everything else:\n" + json.dumps(result["corrections"]))
        fixed, _ = extract._run_extraction(path, text, data.get("carrier"), "claude", invoice_id, fb, effort="high")
        ok0, d0 = extract.reconcile(data)
        ok1, d1 = extract.reconcile(fixed)
        # Accept the corrected version unless it reconciles worse than the original
        if d1 is not None and (d0 is None or abs(d1) <= abs(d0) + 0.01):
            use, applied = fixed, 1
    db.x("INSERT INTO audits(invoice_id, corrections, applied, verdict, model, created_at) VALUES (?,?,?,?,?,?)",
         (invoice_id, json.dumps({"corrections": result["corrections"],
                                  "observations": result["format_observations"]}),
          applied, result["verdict"], model, time.time()))
    return use, result


# ---------- 2. Living format guide ----------

GUIDE_PROMPT = """You maintain a concise reference guide to how one New Zealand telco lays out its business
invoices, used to help extract future bills accurately. Update the guide with anything new learned from this
bill: page structure and section names, where each field appears, how plans, usage, discounts, credits,
broadband and hardware are presented, billing periods (advance vs arrears), GST conventions, recurring plan
names and prices, gotchas and past mistakes. Keep what is still true, correct what was wrong, remove
duplication. Markdown, organised by heading, under 900 words. Facts only - no speculation."""

GUIDE_SCHEMA = {
    "type": "object",
    "properties": {"guide": {"type": "string"}, "changed": {"type": "boolean"}, "summary": {"type": "string"}},
    "required": ["guide", "changed", "summary"],
    "additionalProperties": False,
}


def guide(carrier):
    row = db.q1("SELECT guide FROM format_guides WHERE carrier=?", (carrier,))
    return row["guide"] if row else ""


def _guide_block(carrier):
    g = guide(carrier) if carrier else ""
    return f"\n\nReference guide to {CARRIERS.get(carrier, carrier)} bill formats (built from earlier bills):\n{g}" if g else ""


def update_guide(invoice_id, carrier, text, data, audit_result):
    current = db.q1("SELECT * FROM format_guides WHERE carrier=?", (carrier,))
    msg = (f"Carrier: {CARRIERS.get(carrier, carrier)}\n\nCurrent guide:\n{current['guide'] if current else '(none yet)'}"
           f"\n\nAudit corrections on this bill: {json.dumps(audit_result.get('corrections', []))}"
           f"\nAuditor's format observations: {json.dumps(audit_result.get('format_observations', []))}"
           f"\n\nFinal extraction:\n{json.dumps(data, separators=(',', ':'))[:20000]}"
           f"\n\nBill text (text layer):\n{(text or '(scanned - no text layer)')[:20000]}")
    out, _ = ai.claude_json(GUIDE_PROMPT, [{"type": "text", "text": msg}], GUIDE_SCHEMA, "guide", invoice_id,
                            effort="medium")
    if not out["changed"] and current:
        db.x("UPDATE format_guides SET bills_seen=bills_seen+1 WHERE carrier=?", (carrier,))
        return
    version = (current["version"] + 1) if current else 1
    now = time.time()
    with db.tx() as c:
        c.execute("INSERT INTO format_guides(carrier, guide, bills_seen, version, updated_at) VALUES (?,?,1,?,?)"
                  " ON CONFLICT(carrier) DO UPDATE SET guide=excluded.guide, bills_seen=bills_seen+1,"
                  " version=excluded.version, updated_at=excluded.updated_at",
                  (carrier, out["guide"], version, now))
        c.execute("INSERT INTO guide_history(carrier, version, guide, invoice_id, created_at) VALUES (?,?,?,?,?)",
                  (carrier, version, out["guide"], invoice_id, now))


# ---------- 3. Analysis review: expert notes + proposed finding types ----------

REVIEW_PROMPT = """You are a senior New Zealand telco account manager reviewing an automated analysis of a business
customer's mobile (and broadband) bills. The analysis is being shown to the customer.

1. notes: up to 4 short, specific observations a telco expert would make that help this customer understand
   or control their spend - grounded strictly in the numbers given and the current plan catalogue. Mention
   names and dollar amounts. Where a current plan from the catalogue would clearly suit them better, say so
   and cite it. No generic advice. Amounts exclude GST.
2. new_finding_types: up to 2 general, reusable analysis rules the automated system is missing (that would
   apply to many customers, not just this one) - e.g. "lines on legacy plans that have since been repriced".
   Empty if the existing findings or the already-proposed finding types cover it."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                           "est_monthly_saving": {"type": "number"}},
            "required": ["title", "body", "est_monthly_saving"], "additionalProperties": False}},
        "new_finding_types": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "detail": {"type": "string"},
                           "example": {"type": "string"}, "est_monthly_saving": {"type": "number"}},
            "required": ["title", "detail", "example", "est_monthly_saving"], "additionalProperties": False}},
    },
    "required": ["notes", "new_finding_types"],
    "additionalProperties": False,
}


def catalogue_text(providers=None):
    rows = db.q("SELECT * FROM plan_catalogue WHERE active=1 ORDER BY provider, category, price_excl_gst")
    if providers:
        rows = [r for r in rows if r["provider"] in providers]
    if not rows:
        return "(no plan catalogue yet)"
    out = []
    for r in rows:
        size = ("unlimited" if r["data_gb"] == -1 else f"{r['data_gb']:g}GB") if r["data_gb"] is not None else ""
        out.append(f"- {CARRIERS.get(r['provider'], r['provider'])} {r['category']}: {r['plan_name']} "
                   f"${r['price_excl_gst'] or 0:.2f}/mth excl GST {size} {r['speed'] or ''}. "
                   f"Includes: {r['inclusions'] or '-'}. Conditions: {r['conditions'] or '-'}")
    return "\n".join(out)


def review_customer(owner_email, session_id, invoice_id):
    a = analysis.build(email=owner_email, session_id=session_id)
    if not a.get("has_data"):
        return
    carriers = {i["carrier"] for i in a["invoices"] if i.get("carrier")} | {"2degrees"}
    summary = {
        "months": a["month_labels"], "kpi": a["kpi"], "accounts": a["accounts"],
        "lines": [{k: l[k] for k in ("label", "plan", "carrier", "avg", "spend", "avg_data_gb", "allowance_gb",
                                     "roaming", "usage", "addon", "hardware", "flags")} for l in a["lines"][:80]],
        "services": a.get("services", []),
        "existing_findings": [{"title": i["title"], "headline": i["headline"]} for i in a["insights"]],
        "devices": a["devices"],
    }
    known = [r["title"] for r in db.q("SELECT title FROM suggestions")]
    msg = (f"Analysis:\n{json.dumps(summary, default=str)}\n\nCurrent plan catalogue (from provider websites):\n"
           f"{catalogue_text(carriers)}\n\nFinding types already proposed (do not repeat or rephrase these):\n"
           + "\n".join(f"- {t}" for t in known))
    out, _ = ai.claude_json(REVIEW_PROMPT, [{"type": "text", "text": msg}], REVIEW_SCHEMA, "review", invoice_id,
                            effort="medium")
    owner = owner_email or f"session:{session_id}"
    db.x("INSERT INTO customer_notes(owner, notes, updated_at) VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET"
         " notes=excluded.notes, updated_at=excluded.updated_at", (owner, json.dumps(out["notes"][:4]), time.time()))
    carrier = next(iter(carriers - {"2degrees"}), "2degrees")
    for f in out["new_finding_types"][:2]:
        if not db.q1("SELECT 1 FROM suggestions WHERE lower(title)=lower(?)", (f["title"],)):
            db.x("INSERT INTO suggestions(carrier, title, detail, example, est_monthly_saving, invoice_id, owner_email,"
                 " created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (carrier, f["title"], f["detail"], f["example"], f["est_monthly_saving"], invoice_id, owner_email,
                  time.time()))


def notes_for(owner_email=None, session_id=None):
    owner = owner_email or f"session:{session_id}"
    row = db.q1("SELECT notes FROM customer_notes WHERE owner=?", (owner,))
    return json.loads(row["notes"]) if row else []


# ---------- 4. Deterministic parsing recipes, shadow-tested ----------

PARSER_PROMPT = """Write a declarative parsing recipe ("spec") for one telco's invoice layout. A fixed interpreter
applies it to the PDF text layer (pdfplumber extract_text, pages joined by blank lines). It will replace AI
extraction for high volume, so it must reproduce the reference extractions exactly where they are derivable
from the text. The spec is data only: Python regular expressions (re.MULTILINE | re.IGNORECASE) and rules.

How the interpreter works:
- header.<field>: regex; the named group `v` (or group 1) is the value. Fields: account_name, account_number,
  invoice_number, invoice_date, period_start, period_end, total_excl_gst, gst, total_incl_gst. Dates are parsed
  with `date_format` (strptime format).
- identify: regex that must match for this layout (else the parser refuses).
- mobile_section_start / mobile_section_end: regexes bounding the mobile section.
- line_header: regex matching the start of each mobile line, with named groups `number` and optionally
  `user_name`. Each line's block runs to the next line_header.
- item: regex with named groups `description` and `amount`, matched repeatedly in each line block (and in the
  non-mobile section). skip_items: regexes for matches to ignore (subtotals, column headings, date ranges).
- category_rules: ordered [{pattern, category}] applied to item descriptions; first match wins, otherwise
  default_line_category. Categories: plan, addon, usage, roaming, hardware, one_off, discount, account, other.
  The first `plan` item sets the line's plan_name (plan_name_strip regex removed) and plan_cost.
- unlimited_pattern: regex on plan_name marking unlimited data.
- non_mobile_section_start / _end: section of broadband/landline items, recorded as non_mobile charges;
  credit_pattern marks items that are credits (made negative).
- account_charge_patterns: [{pattern (named groups description, amount), category}] for other account-level
  charges anywhere in the text.
Line totals are the sum of their items, so make sure items capture every charge and nothing else."""

STR_ = {"type": "string"}
PARSER_SCHEMA = {
    "type": "object",
    "properties": {
        "header": {"type": "object", "properties": {k: STR_ for k in (
            "account_name", "account_number", "invoice_number", "invoice_date", "period_start", "period_end",
            "total_excl_gst", "gst", "total_incl_gst")},
            "required": ["account_name", "account_number", "invoice_number", "invoice_date", "period_start",
                         "period_end", "total_excl_gst", "gst", "total_incl_gst"], "additionalProperties": False},
        "date_format": STR_, "identify": STR_,
        "mobile_section_start": STR_, "mobile_section_end": STR_, "line_header": STR_, "item": STR_,
        "skip_items": {"type": "array", "items": STR_},
        "category_rules": {"type": "array", "items": {
            "type": "object", "properties": {"pattern": STR_, "category": {"type": "string", "enum": [
                "plan", "addon", "usage", "roaming", "hardware", "one_off", "discount", "account", "other"]}},
            "required": ["pattern", "category"], "additionalProperties": False}},
        "default_line_category": STR_, "plan_name_strip": STR_, "unlimited_pattern": STR_,
        "non_mobile_section_start": STR_, "non_mobile_section_end": STR_, "credit_pattern": STR_,
        "account_charge_patterns": {"type": "array", "items": {
            "type": "object", "properties": {"pattern": STR_, "category": STR_},
            "required": ["pattern", "category"], "additionalProperties": False}},
    },
    "required": ["header", "date_format", "identify", "mobile_section_start", "mobile_section_end", "line_header",
                 "item", "skip_items", "category_rules", "default_line_category", "plan_name_strip",
                 "unlimited_pattern", "non_mobile_section_start", "non_mobile_section_end", "credit_pattern",
                 "account_charge_patterns"],
    "additionalProperties": False,
}

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_parser(spec_json, text):
    """Apply a parsing spec with the fixed interpreter, in a subprocess with a timeout."""
    spec = json.loads(spec_json)
    r = subprocess.run([sys.executable, "-m", "app.specparse"], input=json.dumps({"spec": spec, "text": text}),
                       capture_output=True, text=True, timeout=20, cwd=APP_ROOT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "parser failed")
    return json.loads(r.stdout)


def compare(parsed, truth):
    """Return a list of differences that matter (empty list = match)."""
    diffs = []
    close = lambda a, b: a is not None and b is not None and abs(float(a) - float(b)) < 0.011
    for k in ("total_excl_gst", "gst", "total_incl_gst"):
        if not close(parsed.get(k), truth.get(k)):
            diffs.append(f"{k}: {parsed.get(k)} vs {truth.get(k)}")
    for k in ("account_number", "invoice_number"):
        if (parsed.get(k) or "") != (truth.get(k) or ""):
            diffs.append(f"{k}: {parsed.get(k)} vs {truth.get(k)}")
    norm = lambda n: "".join(ch for ch in (n or "") if ch.isdigit())
    pl = {norm(l.get("number")): l for l in parsed.get("lines", [])}
    tl = {norm(l.get("number")): l for l in truth.get("lines", [])}
    if set(pl) != set(tl):
        diffs.append(f"line numbers differ: {sorted(set(pl) ^ set(tl))}")
    for n in set(pl) & set(tl):
        if not close(pl[n].get("total_excl_gst"), tl[n].get("total_excl_gst")):
            diffs.append(f"line {n} total: {pl[n].get('total_excl_gst')} vs {tl[n].get('total_excl_gst')}")
    nm = lambda d: sum(c.get("amount") or 0 for c in d.get("account_charges", []) if c.get("category") == "non_mobile")
    if abs(nm(parsed) - nm(truth)) > 0.011:
        diffs.append(f"non-mobile total: {nm(parsed):.2f} vs {nm(truth):.2f}")
    return diffs


def _audited_examples(carrier, limit=None):
    from . import extract
    rows = db.q("SELECT DISTINCT i.id, i.path, i.extraction_json FROM invoices i JOIN audits a ON a.invoice_id=i.id"
                " WHERE i.carrier=? AND i.status IN ('done','duplicate') AND i.has_text=1 AND i.recon_ok=1"
                " ORDER BY i.id DESC" + (f" LIMIT {int(limit)}" if limit else ""), (carrier,))
    out = []
    for r in rows:
        _, text = extract.read_pdf(r["path"])
        out.append((r["id"], text, json.loads(r["extraction_json"])))
    return out


def maybe_generate_parser(carrier):
    if db.q1("SELECT 1 FROM parsers WHERE carrier=? AND status IN ('shadow','active')", (carrier,)):
        return
    examples = _audited_examples(carrier)
    if len(examples) < int(db.get_setting("parser_min_bills") or 10):
        return
    shown = examples[:3]
    msg = f"Carrier: {carrier}\n\nFormat guide:\n{guide(carrier)}\n\n" + "\n\n".join(
        f"=== Reference {k + 1}: text layer ===\n{t[:15000]}\n=== Reference {k + 1}: correct extraction ===\n"
        f"{json.dumps(d, separators=(',', ':'))[:12000]}" for k, (_, t, d) in enumerate(shown))
    spec, _ = ai.claude_json(PARSER_PROMPT, [{"type": "text", "text": msg}], PARSER_SCHEMA, "parser", None,
                             effort="high")
    spec["carrier"] = carrier
    pid = db.x("INSERT INTO parsers(carrier, code, created_at) VALUES (?,?,?)", (carrier, json.dumps(spec),
                                                                                 time.time()))
    backtest(pid)


def backtest(parser_id):
    p = db.q1("SELECT * FROM parsers WHERE id=?", (parser_id,))
    matches, fails = 0, []
    for iid, text, truth in _audited_examples(p["carrier"]):
        try:
            d = compare(run_parser(p["code"], text), truth)
        except Exception as e:
            d = [f"error: {e}"[:200]]
        if d:
            fails.append({"invoice_id": iid, "diffs": d[:5]})
        else:
            matches += 1
    db.x("UPDATE parsers SET matches=?, mismatches=?, streak=?, last_result=? WHERE id=?",
         (matches, len(fails), 0 if fails else matches, json.dumps({"backtest_failures": fails[:10]}), parser_id))


def shadow(invoice_id, carrier, text, truth):
    p = db.q1("SELECT * FROM parsers WHERE carrier=? AND status IN ('shadow','active') ORDER BY id DESC LIMIT 1",
              (carrier,))
    if not p or not text:
        return
    try:
        diffs = compare(run_parser(p["code"], text), truth)
    except Exception as e:
        diffs = [f"error: {e}"[:200]]
    if diffs:
        db.x("UPDATE parsers SET mismatches=mismatches+1, streak=0, last_result=? WHERE id=?",
             (json.dumps({"invoice_id": invoice_id, "diffs": diffs[:5]}), p["id"]))
    else:
        db.x("UPDATE parsers SET matches=matches+1, streak=streak+1, last_result=? WHERE id=?",
             (json.dumps({"invoice_id": invoice_id, "diffs": []}), p["id"]))


def active_parse(carrier, text):
    """Use an activated recipe if there is one and its output reconciles; else None."""
    from . import extract
    p = db.q1("SELECT * FROM parsers WHERE carrier=? AND status='active' ORDER BY id DESC LIMIT 1", (carrier,))
    if not p or not text:
        return None
    try:
        data = run_parser(p["code"], text)
        ok, _ = extract.reconcile(data)
        return data if ok else None
    except Exception:
        return None


# ---------- orchestration ----------

def learn_from(invoice_id, path, text, data):
    """Audit one processed bill and update its carrier's guide. Returns the (possibly corrected) extraction."""
    if db.get_setting("learning_mode") != "on":
        return data
    carrier = data.get("carrier")
    try:
        data, result = audit(invoice_id, path, data)
    except Exception:
        traceback.print_exc()
        result = {"corrections": [], "format_observations": []}
    try:
        update_guide(invoice_id, carrier, text, data, result)
    except Exception:
        traceback.print_exc()
    return data


def after_store(invoice_id, carrier, text, data, owner_email, session_id):
    if db.get_setting("learning_mode") != "on":
        return
    for step in (lambda: shadow(invoice_id, carrier, text, data),
                 lambda: review_customer(owner_email, session_id, invoice_id),
                 lambda: maybe_generate_parser(carrier)):
        try:
            step()
        except Exception:
            traceback.print_exc()
