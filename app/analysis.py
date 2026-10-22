"""Turns stored billing rows into the views, charts and insights shown to users."""
import re
from collections import defaultdict

from . import db, recommend
from .config import carrier_label


def scope_sql(email=None, session_id=None, alias="i"):
    if email:
        return f"{alias}.owner_email = ?", (email.lower(),)
    return f"{alias}.owner_email IS NULL AND {alias}.session_id = ?", (session_id or "-",)


def invoices(email=None, session_id=None):
    where, args = scope_sql(email, session_id)
    return db.q(
        f"SELECT i.* FROM invoices i WHERE {where} ORDER BY COALESCE(i.period_month, '') , i.id", args
    )


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def fmt_money(v, dp=0):
    if v is None:
        return "-"
    s = f"{abs(v):,.{dp}f}"
    return ("-$" if v < 0 else "$") + s


def fmt_number(n):
    n = n or ""
    if len(n) >= 9 and n.startswith("0"):
        return f"{n[:3]} {n[3:6]} {n[6:]}"
    return n


def fmt_data(mb):
    if mb is None:
        return "-"
    if mb == -1:
        return "Unlimited"
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def build(email=None, session_id=None, account=None):
    """Everything the results and dashboard pages need, as plain JSON-able data."""
    where, args = scope_sql(email, session_id)
    invs = db.q(f"SELECT i.* FROM invoices i WHERE {where} ORDER BY i.created_at", args)
    all_done = [i for i in invs if i["status"] == "done"]
    done = [i for i in all_done if not account or i["account_number"] == account]
    ids = [i["id"] for i in done]
    if not ids:
        return {"invoices": invs, "has_data": False}
    ph = ",".join("?" * len(ids))
    lines = db.q(f"SELECT * FROM lines WHERE invoice_id IN ({ph})", ids)
    charges = db.q(f"SELECT * FROM charges WHERE invoice_id IN ({ph})", ids)
    svc_rows = db.q(f"SELECT * FROM services WHERE invoice_id IN ({ph})", ids)
    devices = db.q(f"SELECT d.*, i.period_month FROM devices d JOIN invoices i ON i.id=d.invoice_id"
                   f" WHERE invoice_id IN ({ph})", ids)

    months = sorted({i["period_month"] for i in done if i["period_month"]})
    inv_month = {i["id"]: i["period_month"] for i in done}
    cname = {i["carrier"]: i["carrier_name"] for i in invs if i.get("carrier_name")}
    lab = lambda code: carrier_label(code, cname.get(code))

    # Spend per month, by category
    cat_month = defaultdict(lambda: defaultdict(float))
    for c in charges:
        m = inv_month.get(c["invoice_id"])
        if m:
            cat_month[c["category"]][m] += c["amount"] or 0
    # Headline figures are mobile only; broadband and other telco services on the same bill are reported separately,
    # and non-telco charges (power, gas on a combined bill) are left out altogether
    non_mobile = cat_month.pop("non_mobile", {})
    cat_month.pop("non_telco", None)
    inv_nonmobile = defaultdict(float)
    acct_cat_month = defaultdict(lambda: defaultdict(float))  # account-level charges (no line)
    for c in charges:
        if c["category"] in ("non_mobile", "non_telco"):
            inv_nonmobile[c["invoice_id"]] += c["amount"] or 0
        elif c["line_id"] is None and inv_month.get(c["invoice_id"]):
            acct_cat_month[c["category"]][inv_month[c["invoice_id"]]] += c["amount"] or 0
    total_month = defaultdict(float)
    acct_month_total = defaultdict(dict)  # account -> month -> mobile total
    for i in done:
        if i["period_month"]:
            mobile = (i["total_excl_gst"] or 0) - inv_nonmobile[i["id"]]
            total_month[i["period_month"]] += mobile
            acct_month_total[i["account_number"]][i["period_month"]] = \
                acct_month_total[i["account_number"]].get(i["period_month"], 0) + mobile

    acct_name = {i["account_number"]: i["account_name"] for i in done}
    roam_by_line = defaultdict(list)
    for c in charges:
        if c["category"] == "roaming" and c["line_id"]:
            roam_by_line[c["line_id"]].append(c)
    # Per-line matrix (a "line" is a phone number; it may appear on several bills)
    per = {}
    for l in sorted(lines, key=lambda r: r["period_month"] or ""):
        key = l["number"] or f"line-{l['id']}"
        p = per.setdefault(key, {
            "number": key, "display": fmt_number(key), "user": None, "plan": None, "carrier": l["carrier"],
            "carrier_label": lab(l["carrier"]),
            "account": l["account_number"], "account_name": acct_name.get(l["account_number"]),
            "months": {}, "data": {}, "voice": {}, "allowance": None,
            "roaming": 0.0, "usage": 0.0, "addon": 0.0, "hardware": 0.0, "plan_cost": None, "plan_month": {},
            "roaming_items": [],
        })
        m = l["period_month"]
        p["user"] = l["user_name"] or p["user"]
        p["plan"] = l["plan_name"] or p["plan"]
        p["plan_cost"] = l["plan_cost"] if l["plan_cost"] else p["plan_cost"]
        p["allowance"] = l["data_allowance_mb"] if l["data_allowance_mb"] is not None else p["allowance"]
        if m:
            p["months"][m] = p["months"].get(m, 0) + (l["total"] or 0)
            p["plan_month"][m] = l["plan_cost"]
            if l["data_used_mb"] is not None:
                p["data"][m] = l["data_used_mb"]
            if l["voice_minutes"] is not None:
                p["voice"][m] = l["voice_minutes"]
        p["roaming"] += l["roaming_cost"] or 0
        p["usage"] += l["overage_cost"] or 0
        # Add-ons net of line discounts: a "100% International Discount" cancels the "International" add-on
        p["addon"] += max((l["addon_cost"] or 0) + (l["discount"] or 0), 0)
        p["hardware"] += l["hardware_cost"] or 0
        p["roaming_items"] += roam_by_line.get(l["id"], [])

    name_count = defaultdict(int)
    for p in per.values():
        if p["user"]:
            name_count[p["user"].strip().lower()] += 1
    line_list = []
    for p in per.values():
        n = max(len(p["months"]), 1)
        p["avg"] = sum(p["months"].values()) / n
        p["avg_data"] = _avg(list(p["data"].values()))
        p["avg_voice"] = _avg(list(p["voice"].values()))
        p["latest"] = p["months"].get(months[-1]) if months else None
        if p["allowance"] is None:
            p["allowance"] = recommend.infer_allowance(p["carrier"], p["plan"])
            p["allowance_inferred"] = p["allowance"] is not None
        p["util"] = (p["avg_data"] / p["allowance"]) if (p["allowance"] and p["allowance"] > 0
                                                          and p["avg_data"] is not None) else None
        shared = p["user"] and name_count[p["user"].strip().lower()] > 1
        p["label"] = f"{p['user']} \u00b7 {p['display']}" if shared else (p["user"] or p["display"])
        ini = "".join(w[0].upper() for w in (p["user"] or "").split()[:2])
        p["short"] = f"{ini} {p['display']}" if shared else (p["user"] or p["display"])
        p["flags"] = []
        p["roaming_m"], p["usage_m"], p["addon_net_m"] = p["roaming"] / n, p["usage"] / n, p["addon"] / n
        p["extras_m"] = p["roaming_m"] + p["usage_m"] + p["addon_net_m"]
        line_list.append(p)
    line_list.sort(key=lambda p: -p["avg"])

    svc_list = _services(svc_rows, months, lab)
    insights = _insights(line_list, devices, months, total_month, svc_list)
    latest = months[-1] if months else None
    prev = months[-2] if len(months) > 1 else None
    # Current run-rate: each account's most recent bill (accounts are billed on different cycles)
    acct_latest = {a: max(ms) for a, ms in acct_month_total.items() if ms}
    monthly_now = sum(acct_month_total[a][m] for a, m in acct_latest.items())
    monthly_prev = 0.0
    have_prev = False
    for a, ms in acct_month_total.items():
        ordered = sorted(ms)
        if len(ordered) > 1:
            monthly_prev += ms[ordered[-2]]
            have_prev = True
        elif ordered:
            monthly_prev += ms[ordered[-1]]
    active_lines = [p for p in line_list if acct_latest.get(p["account"]) in p["months"]] or line_list
    savings_month = sum(i.get("saving") or 0 for i in insights)

    accounts = {}
    for i in all_done:
        k = (i["carrier"], i["account_number"])
        a = accounts.setdefault(k, {"carrier": lab(i["carrier"]), "carrier_code": i["carrier"],
                                    "number": i["account_number"], "name": i["account_name"], "bills": 0,
                                    "lines": 0, "monthly": 0.0, "months": set()})
        a["bills"] += 1
        a["months"].add(i["period_month"])
    for p in line_list:
        a = accounts.get((p["carrier"], p["account"]))
        if a:
            a["lines"] += 1
            a["monthly"] += p["avg"]
    for a in accounts.values():
        a["months"] = len(a["months"] - {None})
        a["monthly"] = round(a["monthly"], 2)

    # Provider summary (a customer may be with several)
    providers = {}
    for p in line_list:
        pr = providers.setdefault(p["carrier"], {"code": p["carrier"], "name": lab(p["carrier"]),
                                                 "lines": 0, "services": 0, "monthly": 0.0, "accounts": set()})
        pr["lines"] += 1
        pr["monthly"] += p["avg"]
        pr["accounts"].add(p["account"])
    for sv in svc_list:
        pr = providers.setdefault(sv["carrier_code"], {"code": sv["carrier_code"], "name": sv["carrier"],
                                                       "lines": 0, "services": 0, "monthly": 0.0, "accounts": set()})
        pr["services"] += 1
        pr["monthly"] += sv["avg"]
    for pr in providers.values():
        pr["accounts"] = len(pr["accounts"] or {i["account_number"] for i in all_done if i["carrier"] == pr["code"]})
        pr["monthly"] = round(pr["monthly"], 2)

    # The bill decoded: what the money actually is, per month
    dec = {k: defaultdict(float) for k in ("plans", "addons", "extras", "devices", "fees")}
    for l in lines:
        m = l["period_month"]
        if not m:
            continue
        net = (l["addon_cost"] or 0) + (l["discount"] or 0)
        dec["plans"][m] += l["plan_cost"] or 0
        dec["addons"][m] += max(net, 0)
        dec["extras"][m] += (l["roaming_cost"] or 0) + (l["overage_cost"] or 0)
        dec["devices"][m] += l["hardware_cost"] or 0
        dec["fees"][m] += min(net, 0) + (l["other_cost"] or 0)
    for cat, per_m in acct_cat_month.items():
        bucket = {"plan": "plans", "addon": "addons", "roaming": "extras", "usage": "extras",
                  "hardware": "devices"}.get(cat, "fees")
        for m, v in per_m.items():
            dec[bucket][m] += v
    decoded = {
        "labels": ["Mobile plans", "Add-ons", "Roaming & extras", "Devices", "Broadband & other", "Fees & credits"],
        "series": [[round(dec[k].get(m, 0), 2) for m in months] for k in ("plans", "addons", "extras", "devices")]
                  + [[round(non_mobile.get(m, 0), 2) for m in months]]
                  + [[round(dec["fees"].get(m, 0), 2) for m in months]],
    }
    n_months = max(len(months), 1)
    switch = recommend.switch_plan(line_list, svc_list, _devices(devices, latest), n_months)
    mobile_m = sum(total_month.values()) / n_months
    fees_m = sum(cat_month["account"].values()) / n_months if "account" in cat_month else 0
    score = recommend.health_score(line_list, switch, sum(non_mobile.values()) / n_months, mobile_m,
                                   providers, fees_m)

    return {
        "has_data": True,
        "invoices": invs,
        "accounts": list(accounts.values()),
        "providers": sorted(providers.values(), key=lambda x: -x["monthly"]),
        "decoded": decoded,
        "switch": switch,
        "score": score,
        "months": months,
        "month_labels": [_mlabel(m) for m in months],
        "latest": latest,
        "kpi": {
            "monthly": round(monthly_now, 2),
            "telco_month": round(monthly_now + sum(s["avg"] for s in svc_list), 2),
            "monthly_prev": round(monthly_prev, 2) if have_prev else None,
            "lines": len(active_lines),
            "per_line": monthly_now / len(active_lines) if active_lines else 0,
            "annual": monthly_now * 12,
            "savings_month": savings_month,
            "savings_year": savings_month * 12,
            "bills": len(done),
            "other_services": round(sum(s["avg"] for s in svc_list), 2),
            "devices_month": round(sum(d["monthly"] or 0 for d in _devices(devices, latest)), 2),
        },
        "total_month": [round(total_month.get(m, 0), 2) for m in months],
        "category_month": {cat: [round(v.get(m, 0), 2) for m in months] for cat, v in cat_month.items()},
        "lines": [_line_json(p, months) for p in line_list],
        "insights": insights,
        "devices": _devices(devices, latest),
        "services": svc_list,
    }


def _mlabel(m):
    import calendar
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y[2:]}"


def _line_json(p, months):
    return {
        "number": p["number"], "display": p["display"], "label": p["label"], "short": p["short"], "user": p["user"],
        "plan": p["plan"],
        "carrier": p["carrier_label"], "carrier_code": p["carrier"], "account": p["account"],
        "account_name": p.get("account_name"), "plan_cost": p["plan_cost"],
        "allowance_inferred": bool(p.get("allowance_inferred")),
        "avg": round(p["avg"], 2), "latest": p["latest"],
        "spend": [round(p["months"][m], 2) if m in p["months"] else None for m in months],
        "data": [round(p["data"][m] / 1024, 2) if m in p["data"] else None for m in months],
        "voice": [p["voice"].get(m) for m in months],
        "allowance_gb": None if p["allowance"] is None else (-1 if p["allowance"] == -1 else round(p["allowance"] / 1024, 1)),
        "avg_data_gb": None if p["avg_data"] is None else round(p["avg_data"] / 1024, 2),
        "util": None if p["util"] is None else round(p["util"], 2),
        "roaming": round(p["roaming"], 2), "usage": round(p["usage"], 2), "addon": round(p["addon"], 2),
        "hardware": round(p["hardware"], 2), "flags": p["flags"],
    }


def _services(rows, months, lab=carrier_label):
    by = {}
    for r in sorted(rows, key=lambda r: r["period_month"] or ""):
        key = ((r["address"] or "").lower().strip(), r["service_type"], r["carrier"])
        s = by.setdefault(key, {"type": r["service_type"], "address": r["address"], "carrier": lab(r["carrier"]),
                                "carrier_code": r["carrier"], "plan": None, "speed": None, "months": {},
                                "src": None})
        s["src"] = {"invoice_id": r["invoice_id"], "month": r["period_month"], "amount": r["amount"]}
        s["plan"] = r["plan_name"] or s["plan"]
        s["speed"] = r["speed"] or s["speed"]
        if r["period_month"]:
            s["months"][r["period_month"]] = s["months"].get(r["period_month"], 0) + (r["amount"] or 0)
    out = []
    for s in by.values():
        vals = list(s.pop("months").items())
        s["spend"] = [round(dict(vals).get(m), 2) if m in dict(vals) else None for m in months]
        s["avg"] = round(sum(v for _, v in vals) / max(len(vals), 1), 2)
        out.append(s)
    return sorted(out, key=lambda s: -s["avg"])


def _devices(devices, latest):
    seen = {}
    for d in sorted(devices, key=lambda r: r["period_month"] or ""):
        # The same handset is described slightly differently on each bill ("payment 6 of 36" etc.)
        norm = re.sub(r"\(.*?\)|payment \d+ of \d+|\$[\d,.]+ discount.*|apple ", "", (d["description"] or "").lower())
        norm = re.sub(r"[^a-z0-9]+", " ", norm).strip()
        seen[(d["number"], round(d["monthly_amount"] or 0))] = d
    out = []
    for d in seen.values():
        months_left = d["payments_remaining"]
        if months_left is None and d["remaining_balance"] and d["monthly_amount"]:
            months_left = round(d["remaining_balance"] / d["monthly_amount"])
        balance = d["remaining_balance"]
        if balance is None and months_left is not None and d["monthly_amount"]:
            balance = months_left * d["monthly_amount"]
        out.append({"number": fmt_number(d["number"] or ""), "description": d["description"],
                    "monthly": d["monthly_amount"], "months_left": months_left, "balance": balance})
    out.sort(key=lambda d: -(d["balance"] or 0))
    return out


def _broadband_insight(services):
    items, saving, now = [], 0.0, 0.0
    for s in services:
        fam, why = recommend.pick_broadband(s)
        name, price, promo = recommend.plan_for(fam)
        now += s["avg"]
        diff = s["avg"] - (price or 0)
        saving += max(diff, 0)
        items.append({"label": s["address"] or s["type"], "number": s["carrier"],
                      "value": fmt_money(s["avg"], 2) + "/mth",
                      "detail": f"{s['plan'] or s['type']} - 2degrees {name} is {fmt_money(price, 2)}"
                                + (f" ({promo['text']})" if promo else "")})
    n = len(services)
    return {"key": "broadband", "title": "Broadband",
            "headline": (f"Broadband could be {fmt_money(saving)}/mth cheaper" if saving >= 1 else
                         f"{fmt_money(now)}/mth on {n} broadband connection{'s' if n != 1 else ''}"),
            "body": "Each connection against the closest current 2degrees business broadband plan, with any promotion"
                    " running on 2degrees.nz right now.",
            "saving": round(saving, 2), "items": items}


def _insights(lines, devices, months, total_month, services=()):
    out = []
    n_months = max(len(months), 1)
    if not lines:
        return [_broadband_insight(services)] if services else []

    has_usage = any(p["avg_data"] is not None for p in lines)
    idle = [p for p in lines if (p["avg_data"] is not None and p["avg_data"] < 200)
            and (p["avg_voice"] is None or p["avg_voice"] < 10) and p["avg"] > 0]
    for p in idle:
        p["flags"].append("idle")
    out.append({
        "key": "idle", "title": "Idle lines",
        "headline": f"{len(idle)} line{'s' if len(idle) != 1 else ''} barely used" if idle else
        ("No idle lines found" if has_usage else "Usage isn't shown on these bills"),
        "body": "Numbers you're paying for that used almost no data or calls. Cancel, or move them to a cheaper"
                " standby plan." if has_usage else "Your carrier doesn't print data or call usage on this bill, so we"
                " can't spot idle lines from it. A usage report from your carrier's business portal would show them.",
        "saving": round(sum(p["avg"] for p in idle), 2),
        "items": [{"label": p["label"], "number": p["display"], "value": fmt_money(p["avg"], 2) + "/mth",
                   "detail": f"{fmt_data(p['avg_data'])} data avg"} for p in idle],
    })

    over = [p for p in lines if p["util"] is not None and p["util"] < 0.3 and (p["plan_cost"] or 0) > 0
            and "idle" not in p["flags"]]
    under = [p for p in lines if (p["util"] is not None and p["util"] > 1.0) or (p["usage_m"] + p["addon_net_m"]) > 10]
    for p in over:
        p["flags"].append("oversized")
    for p in under:
        p["flags"].append("undersized")
    fit_saving = sum((p["plan_cost"] or 0) * 0.3 for p in over) + sum((p["usage_m"] + p["addon_net_m"]) * 0.7
                                                                       for p in under)
    items = [{"label": p["label"], "number": p["display"], "value": f"{round(p['util'] * 100)}% of allowance used",
              "detail": f"{p['plan']} - consider a smaller plan"} for p in over]
    items += [{"label": p["label"], "number": p["display"],
               "value": fmt_money(p["usage_m"] + p["addon_net_m"], 2) + "/mth extras",
               "detail": f"{p['plan']} - a bigger plan may be cheaper"} for p in under]
    out.append({
        "key": "plan_fit", "title": "Plan fit",
        "headline": f"{len(over) + len(under)} line{'s' if len(over) + len(under) != 1 else ''} on the wrong-size plan"
        if (over or under) else ("Plans look well matched" if has_usage else "No usage data to check plan size"),
        "body": "Lines using a fraction of their allowance, or regularly paying for extra data and out-of-plan usage.",
        "saving": round(fit_saving, 2),
        "items": items,
    })

    # Same kind of plan (e.g. unlimited) at different prices on the same carrier
    groups = {}
    for p in lines:
        if p["plan_cost"] and p["allowance"] is not None:
            # Data-only plans (tablets, routers) aren't comparable with voice plans
            data_only = bool(re.search(r"\bdata\b", (p["plan"] or "").lower()))
            groups.setdefault((p["carrier"], p["allowance"], data_only), []).append(p)
    def family(name):
        n = re.sub(r"\(.*?\)|\bteam\b|\bplan leader\b|\bleader\b|\bcompanion\b|[-–]", " ", (name or "").lower())
        return re.sub(r"\s+", " ", n).strip()

    pricier = []
    for ps in groups.values():
        cheapest = min(ps, key=lambda p: p["plan_cost"])
        for p in ps:
            # A team plan's lead line legitimately costs more than its team lines
            leader_team = family(p["plan"]) == family(cheapest["plan"]) and \
                re.search(r"team|companion", (cheapest["plan"] or "").lower())
            if p["plan_cost"] - cheapest["plan_cost"] >= 5 and not leader_team:
                pricier.append((p, cheapest))
    for p, _ in pricier:
        p["flags"].append("pricier plan")
    out.append({
        "key": "plan_mix", "title": "Plan pricing",
        "headline": f"{len(pricier)} line{'s' if len(pricier) != 1 else ''} paying more for the same allowance"
        if pricier else "Lines with the same allowance pay the same",
        "body": "Lines with the same data allowance on different-priced plans. Moving them onto the cheaper plan,"
                " or a pooled team plan, usually costs nothing extra in usage.",
        "saving": round(sum(p["plan_cost"] - c["plan_cost"] for p, c in pricier), 2),
        "items": [{"label": p["label"], "number": p["display"],
                   "value": fmt_money(p["plan_cost"] - c["plan_cost"], 2) + "/mth more",
                   "detail": f"{p['plan']} {fmt_money(p['plan_cost'], 2)} vs {c['plan']} {fmt_money(c['plan_cost'], 2)}"}
                  for p, c in pricier],
    })

    leak_lines = sorted([p for p in lines if p["extras_m"] > 0.005], key=lambda p: -p["extras_m"])
    leak_total = sum(p["extras_m"] for p in leak_lines)
    out.append({
        "key": "leakage", "title": "Spend leakage",
        "headline": f"{fmt_money(leak_total)}/mth on roaming, add-ons and extras" if leak_total
        else "No roaming or out-of-plan charges",
        "body": "Charges outside the base plan: roaming, extra data packs, international and premium calls."
                " Most of this can be bundled into plans or controlled with spend caps.",
        "saving": round(leak_total * 0.5, 2),
        "items": [{"label": p["label"], "number": p["display"], "value": fmt_money(p["extras_m"], 2) + "/mth",
                   "detail": ", ".join(f"{k} {fmt_money(v, 2)}" for k, v in
                                       (("roaming", p["roaming_m"]), ("extras", p["usage_m"]),
                                        ("add-ons", p["addon_net_m"])) if v > 0.005)} for p in leak_lines[:12]],
    })
    if services:
        out.append(_broadband_insight(services))
    return out


REAL = "IFNULL(owner_email, '') <> 'example@secondopinion.co.nz'"  # leave the public example out of stats


def admin_stats():
    s = {}
    s["users"] = db.q1("SELECT COUNT(*) n FROM users WHERE verified_at IS NOT NULL")["n"]
    s["emails_known"] = db.q1("SELECT COUNT(DISTINCT owner_email) n FROM invoices WHERE owner_email IS NOT NULL"
                              " AND " + REAL)["n"]
    s["invoices"] = db.q1("SELECT COUNT(*) n FROM invoices WHERE " + REAL)["n"]
    s["by_status"] = db.q("SELECT status, COUNT(*) n FROM invoices WHERE " + REAL + " GROUP BY status ORDER BY n DESC")
    s["by_carrier"] = db.q("SELECT COALESCE(carrier,'unknown') carrier, COUNT(*) n,"
                           " ROUND(AVG(recon_ok)*100) recon_pct, ROUND(AVG(duration_s)) secs"
                           " FROM invoices WHERE status IN ('done','duplicate') AND " + REAL + " GROUP BY carrier")
    s["by_source"] = db.q("SELECT s.source, COUNT(i.id) n FROM invoices i JOIN submissions s ON s.id=i.submission_id"
                          " GROUP BY s.source")
    s["ai_cost"] = db.q1("SELECT ROUND(COALESCE(SUM(cost_usd),0),2) usd, COUNT(*) calls FROM ai_usage")
    s["ai_by_purpose"] = db.q("SELECT purpose, provider, COUNT(*) calls, ROUND(SUM(cost_usd),2) usd FROM ai_usage"
                              " GROUP BY purpose, provider")
    s["offer_requests"] = db.q1("SELECT COUNT(*) n FROM offer_requests")["n"]
    s["lines_tracked"] = db.q1("SELECT COUNT(DISTINCT number) n FROM lines WHERE invoice_id IN"
                               " (SELECT id FROM invoices WHERE " + REAL + ")")["n"]
    s["spend_tracked"] = db.q1("SELECT ROUND(COALESCE(SUM(total_excl_gst),0)) v FROM invoices WHERE status='done'"
                               " AND " + REAL)["v"]
    s["daily"] = db.q(
        "SELECT date(created_at,'unixepoch','localtime') d,"
        " SUM(kind='upload') uploads, SUM(kind='email_received') emails, SUM(kind='registered') registrations,"
        " SUM(kind='offer_request') offers, SUM(kind='page_view') views"
        " FROM events WHERE created_at > strftime('%s','now','-30 days') GROUP BY d ORDER BY d")
    funnel = db.q1(
        "SELECT"
        " (SELECT COUNT(DISTINCT COALESCE(email, session_id)) FROM events WHERE kind IN ('upload','email_received')) submitters,"
        " (SELECT COUNT(DISTINCT session_id) FROM events WHERE kind='page_view' AND meta LIKE '%\"js\": 1%') visitors,"
        " (SELECT COUNT(DISTINCT session_id) FROM events WHERE kind='upload_intent') upload_intent,"
        " (SELECT COUNT(*) FROM events WHERE kind='example_viewed') example_views,"
        " (SELECT COUNT(*) FROM events WHERE kind='results_viewed') results_views,"
        " (SELECT COUNT(*) FROM events WHERE kind='email_captured') emails_captured,"
        " (SELECT COUNT(*) FROM events WHERE kind='registered') registered,"
        " (SELECT COUNT(DISTINCT email) FROM events WHERE kind='dashboard_viewed') dashboard_users,"
        " (SELECT COUNT(DISTINCT email) FROM events WHERE kind='chat') chat_users,"
        " (SELECT COUNT(*) FROM events WHERE kind='offer_request') offer_requests")
    s["funnel"] = funnel
    s["knowledge"] = db.q("SELECT carrier, COUNT(*) n FROM knowledge GROUP BY carrier")
    s["verified"] = db.q1("SELECT COUNT(*) n FROM invoices WHERE verified=1")["n"]
    return s
