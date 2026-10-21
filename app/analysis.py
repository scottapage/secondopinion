"""Turns stored billing rows into the views, charts and insights shown to users."""
from collections import defaultdict

from . import db
from .config import CARRIERS


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
    devices = db.q(f"SELECT d.*, i.period_month FROM devices d JOIN invoices i ON i.id=d.invoice_id"
                   f" WHERE invoice_id IN ({ph})", ids)

    months = sorted({i["period_month"] for i in done if i["period_month"]})
    inv_month = {i["id"]: i["period_month"] for i in done}

    # Spend per month, by category
    cat_month = defaultdict(lambda: defaultdict(float))
    for c in charges:
        m = inv_month.get(c["invoice_id"])
        if m:
            cat_month[c["category"]][m] += c["amount"] or 0
    total_month = defaultdict(float)
    for i in done:
        if i["period_month"]:
            total_month[i["period_month"]] += i["total_excl_gst"] or 0

    # Per-line matrix (a "line" is a phone number; it may appear on several bills)
    per = {}
    for l in sorted(lines, key=lambda r: r["period_month"] or ""):
        key = l["number"] or f"line-{l['id']}"
        p = per.setdefault(key, {
            "number": key, "display": fmt_number(key), "user": None, "plan": None, "carrier": l["carrier"],
            "account": l["account_number"], "months": {}, "data": {}, "voice": {}, "allowance": None,
            "roaming": 0.0, "usage": 0.0, "addon": 0.0, "hardware": 0.0, "plan_cost": None,
        })
        m = l["period_month"]
        p["user"] = l["user_name"] or p["user"]
        p["plan"] = l["plan_name"] or p["plan"]
        p["plan_cost"] = l["plan_cost"] if l["plan_cost"] else p["plan_cost"]
        p["allowance"] = l["data_allowance_mb"] if l["data_allowance_mb"] is not None else p["allowance"]
        if m:
            p["months"][m] = p["months"].get(m, 0) + (l["total"] or 0)
            if l["data_used_mb"] is not None:
                p["data"][m] = l["data_used_mb"]
            if l["voice_minutes"] is not None:
                p["voice"][m] = l["voice_minutes"]
        p["roaming"] += l["roaming_cost"] or 0
        p["usage"] += l["overage_cost"] or 0
        p["addon"] += l["addon_cost"] or 0
        p["hardware"] += l["hardware_cost"] or 0

    line_list = []
    for p in per.values():
        n = max(len(p["months"]), 1)
        p["avg"] = sum(p["months"].values()) / n
        p["avg_data"] = _avg(list(p["data"].values()))
        p["avg_voice"] = _avg(list(p["voice"].values()))
        p["latest"] = p["months"].get(months[-1]) if months else None
        p["util"] = (p["avg_data"] / p["allowance"]) if (p["allowance"] and p["allowance"] > 0
                                                          and p["avg_data"] is not None) else None
        p["label"] = p["user"] or p["display"]
        p["flags"] = []
        line_list.append(p)
    line_list.sort(key=lambda p: -p["avg"])

    insights = _insights(line_list, devices, months, total_month)
    latest = months[-1] if months else None
    prev = months[-2] if len(months) > 1 else None
    monthly_now = total_month.get(latest, 0) if latest else 0
    active_lines = [p for p in line_list if latest in p["months"]] if latest else line_list
    savings_month = sum(i.get("saving") or 0 for i in insights)

    accounts = {}
    for i in all_done:
        k = (i["carrier"], i["account_number"])
        a = accounts.setdefault(k, {"carrier": CARRIERS.get(i["carrier"], i["carrier"]), "number": i["account_number"],
                                    "name": i["account_name"], "bills": 0})
        a["bills"] += 1

    return {
        "has_data": True,
        "invoices": invs,
        "accounts": list(accounts.values()),
        "months": months,
        "month_labels": [_mlabel(m) for m in months],
        "latest": latest,
        "kpi": {
            "monthly": monthly_now,
            "monthly_prev": total_month.get(prev) if prev else None,
            "lines": len(active_lines),
            "per_line": monthly_now / len(active_lines) if active_lines else 0,
            "annual": monthly_now * 12,
            "savings_month": savings_month,
            "savings_year": savings_month * 12,
            "bills": len(done),
        },
        "total_month": [round(total_month.get(m, 0), 2) for m in months],
        "category_month": {cat: [round(v.get(m, 0), 2) for m in months] for cat, v in cat_month.items()},
        "lines": [_line_json(p, months) for p in line_list],
        "insights": insights,
        "devices": _devices(devices, latest),
    }


def _mlabel(m):
    import calendar
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y[2:]}"


def _line_json(p, months):
    return {
        "number": p["number"], "display": p["display"], "label": p["label"], "user": p["user"], "plan": p["plan"],
        "carrier": CARRIERS.get(p["carrier"], p["carrier"]), "account": p["account"],
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


def _devices(devices, latest):
    seen = {}
    for d in sorted(devices, key=lambda r: r["period_month"] or ""):
        seen[(d["number"], d["description"])] = d
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


def _insights(lines, devices, months, total_month):
    out = []
    n_months = max(len(months), 1)

    idle = [p for p in lines if (p["avg_data"] is not None and p["avg_data"] < 200)
            and (p["avg_voice"] is None or p["avg_voice"] < 10) and p["avg"] > 0]
    for p in idle:
        p["flags"].append("idle")
    out.append({
        "key": "idle", "title": "Idle lines",
        "headline": f"{len(idle)} line{'s' if len(idle) != 1 else ''} barely used" if idle else "No idle lines found",
        "body": "Numbers you're paying for that used almost no data or calls. Cancel, or move them to a cheaper"
                " standby plan.",
        "saving": round(sum(p["avg"] for p in idle), 2),
        "items": [{"label": p["label"], "number": p["display"], "value": fmt_money(p["avg"], 2) + "/mth",
                   "detail": f"{fmt_data(p['avg_data'])} data avg"} for p in idle],
    })

    over = [p for p in lines if p["util"] is not None and p["util"] < 0.3 and (p["plan_cost"] or 0) > 0
            and "idle" not in p["flags"]]
    under = [p for p in lines if (p["util"] is not None and p["util"] > 1.0) or (p["usage"] + p["addon"]) / n_months > 10]
    for p in over:
        p["flags"].append("oversized")
    for p in under:
        p["flags"].append("undersized")
    fit_saving = sum((p["plan_cost"] or 0) * 0.3 for p in over) + sum((p["usage"] + p["addon"]) / n_months * 0.7
                                                                       for p in under)
    items = [{"label": p["label"], "number": p["display"], "value": f"{round(p['util'] * 100)}% of allowance used",
              "detail": f"{p['plan']} - consider a smaller plan"} for p in over]
    items += [{"label": p["label"], "number": p["display"],
               "value": fmt_money((p["usage"] + p["addon"]) / n_months, 2) + "/mth extras",
               "detail": f"{p['plan']} - a bigger plan may be cheaper"} for p in under]
    out.append({
        "key": "plan_fit", "title": "Plan fit",
        "headline": f"{len(over) + len(under)} line{'s' if len(over) + len(under) != 1 else ''} on the wrong-size plan"
        if (over or under) else "Plans look well matched",
        "body": "Lines using a fraction of their allowance, or regularly paying for extra data and out-of-plan usage.",
        "saving": round(fit_saving, 2),
        "items": items,
    })

    leak_lines = [p for p in lines if p["roaming"] + p["usage"] + p["addon"] > 0]
    leak_lines.sort(key=lambda p: -(p["roaming"] + p["usage"] + p["addon"]))
    leak_total = sum(p["roaming"] + p["usage"] + p["addon"] for p in leak_lines)
    out.append({
        "key": "leakage", "title": "Spend leakage",
        "headline": f"{fmt_money(leak_total / n_months)}/mth on roaming, add-ons and extras" if leak_total
        else "No roaming or out-of-plan charges",
        "body": "Charges outside the base plan: roaming, extra data packs, international and premium calls."
                " Most of this can be bundled into plans or controlled with spend caps.",
        "saving": round(leak_total / n_months * 0.5, 2),
        "items": [{"label": p["label"], "number": p["display"],
                   "value": fmt_money((p["roaming"] + p["usage"] + p["addon"]) / n_months, 2) + "/mth",
                   "detail": ", ".join(f"{k} {fmt_money(v / n_months, 2)}" for k, v in
                                       (("roaming", p["roaming"]), ("extras", p["usage"]), ("add-ons", p["addon"]))
                                       if v)} for p in leak_lines[:12]],
    })
    return out


def admin_stats():
    s = {}
    s["users"] = db.q1("SELECT COUNT(*) n FROM users WHERE verified_at IS NOT NULL")["n"]
    s["emails_known"] = db.q1("SELECT COUNT(DISTINCT owner_email) n FROM invoices WHERE owner_email IS NOT NULL")["n"]
    s["invoices"] = db.q1("SELECT COUNT(*) n FROM invoices")["n"]
    s["by_status"] = db.q("SELECT status, COUNT(*) n FROM invoices GROUP BY status ORDER BY n DESC")
    s["by_carrier"] = db.q("SELECT COALESCE(carrier,'unknown') carrier, COUNT(*) n,"
                           " ROUND(AVG(recon_ok)*100) recon_pct, ROUND(AVG(duration_s)) secs"
                           " FROM invoices WHERE status IN ('done','duplicate') GROUP BY carrier")
    s["by_source"] = db.q("SELECT s.source, COUNT(i.id) n FROM invoices i JOIN submissions s ON s.id=i.submission_id"
                          " GROUP BY s.source")
    s["ai_cost"] = db.q1("SELECT ROUND(COALESCE(SUM(cost_usd),0),2) usd, COUNT(*) calls FROM ai_usage")
    s["ai_by_purpose"] = db.q("SELECT purpose, provider, COUNT(*) calls, ROUND(SUM(cost_usd),2) usd FROM ai_usage"
                              " GROUP BY purpose, provider")
    s["offer_requests"] = db.q1("SELECT COUNT(*) n FROM offer_requests")["n"]
    s["lines_tracked"] = db.q1("SELECT COUNT(DISTINCT number) n FROM lines")["n"]
    s["spend_tracked"] = db.q1("SELECT ROUND(COALESCE(SUM(total_excl_gst),0)) v FROM invoices WHERE status='done'")["v"]
    s["daily"] = db.q(
        "SELECT date(created_at,'unixepoch','localtime') d,"
        " SUM(kind='upload') uploads, SUM(kind='email_received') emails, SUM(kind='registered') registrations,"
        " SUM(kind='offer_request') offers, SUM(kind='page_view') views"
        " FROM events WHERE created_at > strftime('%s','now','-30 days') GROUP BY d ORDER BY d")
    funnel = db.q1(
        "SELECT"
        " (SELECT COUNT(DISTINCT COALESCE(email, session_id)) FROM events WHERE kind IN ('upload','email_received')) submitters,"
        " (SELECT COUNT(*) FROM events WHERE kind='results_viewed') results_views,"
        " (SELECT COUNT(*) FROM events WHERE kind='registered') registered,"
        " (SELECT COUNT(DISTINCT email) FROM events WHERE kind='dashboard_viewed') dashboard_users,"
        " (SELECT COUNT(DISTINCT email) FROM events WHERE kind='chat') chat_users,"
        " (SELECT COUNT(*) FROM events WHERE kind='offer_request') offer_requests")
    s["funnel"] = funnel
    s["knowledge"] = db.q("SELECT carrier, COUNT(*) n FROM knowledge GROUP BY carrier")
    s["verified"] = db.q1("SELECT COUNT(*) n FROM invoices WHERE verified=1")["n"]
    return s
