"""The NZ Overpay Meter: what the analyses have found so far, across every real customer, anonymised for the
front page. Recomputed by the worker every few minutes and cached in settings."""
import json
import re
import time

from . import analysis, db
from .config import carrier_label

REAL = "IFNULL(owner_email, '') <> 'example@secondopinion.co.nz'"


def _owners():
    emails = [r["owner_email"] for r in db.q(f"SELECT DISTINCT owner_email FROM invoices WHERE status='done' AND"
                                               f" owner_email IS NOT NULL AND {REAL}")]
    sessions = [r["session_id"] for r in db.q("SELECT DISTINCT session_id FROM invoices WHERE status='done' AND"
                                               " owner_email IS NULL AND session_id IS NOT NULL")]
    return [("email", e) for e in emails] + [("session", s) for s in sessions]


def compute():
    found, spend, bills, lines, services, customers = 0.0, 0.0, 0, 0, 0, 0
    prov = {}
    ticker = []
    for kind, key in _owners():
        a = analysis.build(email=key) if kind == "email" else analysis.build(session_id=key)
        if not a.get("has_data"):
            continue
        customers += 1
        k, sw = a["kpi"], a.get("switch") or {}
        bills += k["bills"]
        lines += k["lines"]
        services += len(a.get("services", []))
        spend += k["telco_month"] * 12
        ins = {i["key"]: i for i in a["insights"]}
        waste = max(sw.get("optimised_saving_year") or 0, sw.get("saving_year") or 0, 0)
        waste += 12 * sum((ins[x]["saving"] or 0) for x in ("idle", "leakage", "broadband") if x in ins)
        found += waste
        names = ", ".join(p["name"] for p in a["providers"]) or "their provider"
        if ins.get("idle", {}).get("items"):
            n, v = len(ins["idle"]["items"]), ins["idle"]["saving"]
            ticker.append({"text": f"{n} line{'s' if n != 1 else ''} nobody uses, costing {analysis.fmt_money(v * 12)} a year",
                           "prov": names})
        if ins.get("leakage", {}).get("saving", 0) >= 10:
            ticker.append({"text": f"{analysis.fmt_money(ins['leakage']['saving'] * 2)}/mth leaking into roaming, add-ons"
                                   f" and out-of-plan charges", "prov": names})
        if waste >= 200:
            ticker.append({"text": f"A {k['lines']}-line business paying {analysis.fmt_money(waste)} a year more than it needs to",
                           "prov": names})
        for it in ins.get("plan_fit", {}).get("items", [])[:2]:
            if "%" in it["value"]:
                ticker.append({"text": f"A line using {it['value'].split(' ')[0]} of its data allowance", "prov": names})
        if sw.get("hardware_balance", 0) >= 1000:
            ticker.append({"text": f"{analysis.fmt_money(sw['hardware_balance'])} still owing on phones - the number a provider"
                                   f" hopes you never add up", "prov": names})
        for pr in ins.get("broadband", {}).get("items", []):
            m = re.search(r"\(([^()]*)\)\s*$", pr["detail"])
            if m:
                ticker.append({"text": "On 2degrees business fibre right now: " + m.group(1), "prov": pr["number"]})
        for p in a["lines"]:
            s = prov.setdefault(p["carrier_code"], {"code": p["carrier_code"], "name": p["carrier"], "lines": 0,
                                                     "flagged": 0, "extras": 0.0, "above": 0.0, "current": 0.0})
            s["lines"] += 1
            s["flagged"] += 1 if any(f in p["flags"] for f in ("idle", "oversized", "undersized", "pricier plan")) else 0
            n = max(sum(1 for v in p["spend"] if v is not None), 1)
            s["extras"] += (p["roaming"] + p["usage"] + p["addon"]) / n
        for l in sw.get("lines", []):
            s = prov.get(l["carrier_code"])
            if s and not l["already"]:
                s["current"] += l["current"]
                s["above"] += max(l["current"] - l["price"], 0)
        for sv in a.get("services", []):
            s = prov.setdefault(sv["carrier_code"], {"code": sv["carrier_code"], "name": sv["carrier"], "lines": 0,
                                                      "flagged": 0, "extras": 0.0, "above": 0.0, "current": 0.0,
                                                      "services": 0})
            s["services"] = s.get("services", 0) + 1
    providers = []
    for s in prov.values():
        providers.append({"code": s["code"], "name": s["name"], "lines": s["lines"], "services": s.get("services", 0),
                          "flagged_pct": round(100 * s["flagged"] / s["lines"]) if s["lines"] else None,
                          "extras_line": round(s["extras"] / s["lines"], 2) if s["lines"] else None,
                          "above_pct": round(100 * s["above"] / s["current"]) if s["current"] else None,
                          "leak_pct": max(round(100 * s["flagged"] / s["lines"]) if s["lines"] else 0,
                                          round(100 * s["above"] / s["current"]) if s["current"] else 0)})
    providers.sort(key=lambda s: -(s["lines"] + s["services"]))
    seen, uniq = set(), []
    for t in ticker:
        if t["text"] not in seen:
            seen.add(t["text"])
            uniq.append(t)
    return {"found_year": round(found), "spend_year": round(spend), "bills": bills, "lines": lines,
            "services": services, "customers": customers, "providers": providers, "ticker": uniq[:14],
            "at": time.time()}


def refresh():
    data = compute()
    db.set_setting("meter_json", json.dumps(data))
    return data


def get(max_age=900):
    raw = db.get_setting("meter_json")
    if raw:
        data = json.loads(raw)
        if time.time() - data.get("at", 0) < max_age:
            return data
    try:
        return refresh()
    except Exception:
        return json.loads(raw) if raw else {"found_year": 0, "bills": 0, "lines": 0, "customers": 0, "providers": [],
                                            "ticker": [], "at": time.time()}
