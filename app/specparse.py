"""Fixed interpreter for declarative bill-parsing recipes ("specs").

Claude writes a spec (regular expressions and category rules, as JSON data) for a carrier's bill layout; this
module applies it. Generated content is only ever data - never executed code. Run as a subprocess
(`python -m app.specparse`) so a pathological regex can be killed by a timeout.
"""
import json
import re
import sys
from datetime import datetime

FLAGS = re.MULTILINE | re.IGNORECASE


def _money(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    neg = s.startswith("-") or s.endswith("CR") or s.startswith("(")
    v = float(re.sub(r"[^\d.]", "", s) or 0)
    return -v if neg else v


def _first(pattern, text):
    if not pattern:
        return None
    m = re.search(pattern, text, FLAGS)
    if not m:
        return None
    return (m.groupdict().get("v") or (m.group(1) if m.groups() else m.group(0))).strip()


def _date(s, fmt):
    if not s:
        return None
    for f in [fmt] if fmt else ["%d %b %Y", "%d/%m/%Y", "%d %B %Y"]:
        try:
            return datetime.strptime(s.strip(), f).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return None


def _section(text, start, end):
    if not start:
        return text
    m = re.search(start, text, FLAGS)
    if not m:
        return ""
    rest = text[m.end():]
    if end:
        e = re.search(end, rest, FLAGS)
        if e:
            rest = rest[:e.start()]
    return rest


def _category(desc, rules, default):
    for r in rules:
        if re.search(r["pattern"], desc, re.IGNORECASE):
            return r["category"]
    return default


def apply(spec, text):
    h = spec.get("header", {})
    out = {
        "is_mobile_bill": True, "carrier": spec["carrier"], "rejection_reason": None, "learned": [], "notes": "parser",
        "account_name": _first(h.get("account_name"), text), "account_number": _first(h.get("account_number"), text),
        "customer_name": None, "invoice_number": _first(h.get("invoice_number"), text),
        "invoice_date": _date(_first(h.get("invoice_date"), text), spec.get("date_format")),
        "period_start": _date(_first(h.get("period_start"), text), spec.get("date_format")),
        "period_end": _date(_first(h.get("period_end"), text), spec.get("date_format")),
        "lines": [], "account_charges": [], "services": [], "devices": [],
    }
    for k in ("total_excl_gst", "gst", "total_incl_gst"):
        v = _first(h.get(k), text)
        out[k] = _money(v) if v is not None else None
    if spec.get("identify") and not re.search(spec["identify"], text, FLAGS):
        raise ValueError("text does not match this layout")

    rules = spec.get("category_rules", [])
    skip = [re.compile(p, FLAGS) for p in spec.get("skip_items", [])]
    item_re = re.compile(spec["item"], FLAGS)

    mobile = _section(text, spec.get("mobile_section_start"), spec.get("mobile_section_end"))
    heads = list(re.finditer(spec["line_header"], mobile, FLAGS))
    for i, hm in enumerate(heads):
        block = mobile[hm.end(): heads[i + 1].start() if i + 1 < len(heads) else len(mobile)]
        g = hm.groupdict()
        line = {"number": re.sub(r"\D", "", g.get("number") or ""), "user_name": (g.get("user_name") or "").strip()
                or None, "plan_name": None, "plan_cost": 0.0, "data_used_mb": None, "data_allowance_mb": None,
                "voice_minutes": None, "sms_count": None, "charges": []}
        for im in item_re.finditer(block):
            raw = im.group(0)
            if any(s.search(raw) for s in skip):
                continue
            desc = im.group("description").strip()
            amt = _money(im.group("amount"))
            cat = _category(desc, rules, spec.get("default_line_category", "usage"))
            if cat == "discount" and amt > 0:
                amt = -amt
            line["charges"].append({"description": desc, "category": cat, "amount": amt})
            if cat == "plan" and line["plan_name"] is None:
                line["plan_name"] = re.sub(spec.get("plan_name_strip", r"\s*\(.*?\)\s*$"), "", desc).strip()
                line["plan_cost"] = amt
        if spec.get("unlimited_pattern") and line["plan_name"] and re.search(spec["unlimited_pattern"],
                                                                             line["plan_name"], re.IGNORECASE):
            line["data_allowance_mb"] = -1
        line["total_excl_gst"] = round(sum(c["amount"] for c in line["charges"]), 2)
        out["lines"].append(line)

    if spec.get("non_mobile_section_start"):
        nm = _section(text, spec["non_mobile_section_start"], spec.get("non_mobile_section_end"))
        credit = spec.get("credit_pattern")
        for im in item_re.finditer(nm):
            raw = im.group(0)
            if any(s.search(raw) for s in skip):
                continue
            amt = _money(im.group("amount"))
            if credit and re.search(credit, raw, re.IGNORECASE) and amt > 0:
                amt = -amt
            out["account_charges"].append({"description": im.group("description").strip(),
                                           "category": "non_mobile", "amount": amt})
    for p in spec.get("account_charge_patterns", []):
        for im in re.finditer(p["pattern"], text, FLAGS):
            out["account_charges"].append({"description": im.group("description").strip(),
                                           "category": p["category"], "amount": _money(im.group("amount"))})
    return out


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read())
    print(json.dumps(apply(payload["spec"], payload["text"])))
