"""Maps a customer's current services to 2degrees business plans (the "switch scenario") and scores the
estate's mobile health. Deterministic and explainable; prices come from the plan catalogue with fallbacks.

Porting a number needs only: MSISDN, losing provider, losing-provider account number and account name -
all of which are on the bill. The user's own name and a delivery address (or an eSIM) are the only gaps."""
import re

from . import db
from .config import CARRIERS

GB = 1024.0

# Fallbacks if the catalogue hasn't been read yet (2degrees business, excl GST, Oct 2026)
FALLBACK = {
    "Business 10GB": (42.0, 10), "Business 100GB": (62.0, 100), "Business 100GB Team plan": (32.0, 100),
    "Business Unlimited": (75.0, -1), "Business Unlimited Team plan": (40.0, -1), "Business Unlimited Data": (35.0, -1),
    "Business Fibre 500": (111.0, -1), "Business Fibre 900": (120.0, -1), "Unlimited 4G Wireless": (56.52, -1),
    "Unlimited 5G Wireless": (68.7, -1), "300GB 4G Wireless": (47.83, 300),
}


def _cat(provider=None, category=None):
    rows = db.q("SELECT * FROM plan_catalogue WHERE active=1")
    return [r for r in rows if (not provider or r["provider"] == provider) and (not category or r["category"] == category)]


def price_of(name):
    for r in _cat("2degrees"):
        if r["plan_name"].lower() == name.lower() and r["price_excl_gst"]:
            return r["price_excl_gst"]
    return FALLBACK.get(name, (None, None))[0]


def _words(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - {"plan", "business", "the", "a", "simo", "mobile"}


def infer_allowance(carrier, plan_name):
    """Data allowance (MB, -1 unlimited) for a plan name, from the carrier's catalogue when the bill didn't say."""
    best, score = None, 0
    pw = _words(plan_name)
    if not pw:
        return None
    for r in _cat(carrier, "mobile"):
        cw = _words(r["plan_name"])
        s = len(pw & cw) / max(len(pw | cw), 1)
        if s > score:
            best, score = r, s
    if best and score >= 0.4 and best["data_gb"] is not None and best["data_gb"] != 0:
        return -1 if best["data_gb"] == -1 else best["data_gb"] * GB
    return None


def is_data_only(plan_name):
    n = (plan_name or "").lower()
    return bool(re.search(r"\bdata[- ]only\b|\bunlimited data\b|\bdata only\b|data-only|\bsharer\b.*data", n)) or \
        (re.search(r"\bdata\b", n) and not re.search(r"endless|one plan|voice|team|companion", n))


def pick_mobile(line):
    """line: dict with plan, allowance (MB), avg_data (MB or None). Returns (family, reason)."""
    if is_data_only(line.get("plan")):
        return "data", "data-only connection"
    used = line.get("avg_data")
    if used is not None:
        if used <= 8 * GB:
            return "10", f"uses about {used / GB:.1f}GB a month"
        if used <= 80 * GB:
            return "100", f"uses about {used / GB:.0f}GB a month"
        return "unl", f"uses about {used / GB:.0f}GB a month"
    al = line.get("allowance")
    if al is None:
        return "unl", "usage not shown - like-for-like"
    if al == -1:
        return "unl", "unlimited now; usage not shown - verify"
    if al <= 10 * GB:
        return "10", f"current allowance {al / GB:.0f}GB"
    if al <= 100 * GB:
        return "100", f"current allowance {al / GB:.0f}GB"
    return "unl", f"current allowance {al / GB:.0f}GB"


FAMILY = {
    "10": ("Business 10GB", "Business 10GB"),
    "100": ("Business 100GB", "Business 100GB Team plan"),
    "unl": ("Business Unlimited", "Business Unlimited Team plan"),
    "data": ("Business Unlimited Data", "Business Unlimited Data"),
}


def pick_broadband(service):
    s = f"{service.get('plan') or ''} {service.get('speed') or ''}".lower()
    if re.search(r"hyper|2000|4000|8000", s):
        return "Business Hyperfibre 2", "hyperfibre"
    if re.search(r"900|max|boost|1000|gig", s):
        return "Business Fibre 900", "fastest standard fibre"
    if re.search(r"fibre|fiber|everyday|300|500", s):
        return "Business Fibre 500", "fibre, standard speed"
    if re.search(r"5g", s):
        return "Unlimited 5G Wireless", "wireless 5G"
    if re.search(r"wireless|4g|rural", s):
        return "Unlimited 4G Wireless", "wireless 4G"
    return "Business Fibre 500", "fibre"


def switch_plan(lines, services, devices, n_months):
    """Build the switch scenario. `lines` are the internal per-line dicts from analysis.build."""
    out_lines, families = [], {}
    for p in sorted(lines, key=lambda p: -(p["plan_cost"] or p["avg"] or 0)):
        fam, why = pick_mobile(p)
        families.setdefault(fam, []).append((p, why))
    for fam, members in families.items():
        lead, team = FAMILY[fam]
        for i, (p, why) in enumerate(members):
            role = "Plan Leader" if (i == 0 and lead != team) else ("Team" if lead != team else "")
            name = lead if i == 0 else team
            price = price_of(name) or 0
            current = (p["plan_cost"] or 0) + max(p["addon_net_m"], 0)  # plan + add-ons actually paid
            out_lines.append({
                "number": p["number"], "display": p["display"], "label": p["label"], "user": p["user"],
                "carrier": CARRIERS.get(p["carrier"], p["carrier"]), "carrier_code": p["carrier"],
                "account": p["account"], "account_name": p.get("account_name"),
                "plan": p["plan"], "current": round(current, 2), "extras": round(p["extras_m"], 2),
                "rec": name, "role": role, "price": price, "why": why, "saving": round(current - price, 2),
                "already": p["carrier"] == "2degrees",
            })
    out_services = []
    for s in services:
        name, why = pick_broadband(s)
        price = price_of(name) or 0
        out_services.append({"type": s["type"], "address": s["address"], "carrier": s["carrier"], "plan": s["plan"],
                             "current": s["avg"], "rec": name, "price": price, "why": why,
                             "saving": round(s["avg"] - price, 2)})
    # Right-sized scenario: unlimited lines whose usage isn't shown may fit the 100GB tier
    opt_lines = 0.0
    seen_100_leader = any(l["rec"] == "Business 100GB" for l in out_lines)
    for l in out_lines:
        if l["rec"].startswith("Business Unlimited") and l["rec"] != "Business Unlimited Data" and "usage not shown" in l["why"]:
            name = "Business 100GB Team plan" if seen_100_leader else "Business 100GB"
            seen_100_leader = True
            l["alt"], l["alt_price"] = name, price_of(name) or 0
            opt_lines += l["alt_price"]
        else:
            opt_lines += l["price"]
    cur_m = sum(l["current"] for l in out_lines) + sum(s["current"] for s in out_services)
    fut_m = sum(l["price"] for l in out_lines) + sum(s["price"] for s in out_services)
    extras_m = sum(l["extras"] for l in out_lines)
    hw_balance = sum((d.get("balance") or 0) for d in devices)
    roaming_au = 0.0  # roaming to Australia is included on 2degrees business plans; we can't tell destinations yet
    return {
        "lines": out_lines, "services": out_services,
        "current_month": round(cur_m, 2), "future_month": round(fut_m, 2),
        "saving_month": round(cur_m - fut_m, 2), "saving_year": round((cur_m - fut_m) * 12, 2),
        "optimised_month": round(opt_lines + sum(s["price"] for s in out_services), 2),
        "optimised_saving_year": round((cur_m - opt_lines - sum(s["price"] for s in out_services)) * 12, 2),
        "extras_month": round(extras_m, 2), "hardware_balance": round(hw_balance, 2), "roaming_au": roaming_au,
        "gains": ["Unlimited calls and texts to NZ and Australia on every plan",
                  "300 international minutes a month on 100GB and Unlimited plans",
                  "Daily roaming in Australia at no extra cost; $8/day in 100+ other countries",
                  "Unused data carries over for up to 12 months on capped plans",
                  "One bill, one account, one team price"],
        "porting": {
            "have": ["Phone numbers (MSISDN)", "Current provider", "Current account number", "Account name"],
            "need": ["Each user's name for the new SIM", "A delivery address - or go eSIM and skip the post"],
        },
    }


def health_score(lines, switch, non_mobile_m, mobile_m, providers, account_fees_m):
    """0-100 with four explainable parts. Higher is better."""
    n = max(len(lines), 1)
    # Plan fit (25): share of lines not flagged
    flagged = sum(1 for p in lines if any(f in p["flags"] for f in ("idle", "oversized", "undersized")))
    fit = 25 * (1 - flagged / n)
    # Pricing (25): how much the current plan+addon spend exceeds the 2degrees like-for-like price
    cur = sum(l["current"] for l in switch["lines"]) or 1
    fut = sum(l["price"] for l in switch["lines"])
    over = max(cur - fut, 0) / cur
    pricing = 25 * max(0, 1 - over / 0.3)
    # Leakage (25): roaming + out-of-plan + net add-ons as a share of mobile spend
    leak = sum(p["roaming_m"] + p["usage_m"] + max(p["addon_net_m"], 0) for p in lines)
    share = leak / mobile_m if mobile_m else 0
    leakage = 25 * max(0, 1 - share / 0.15)
    # Housekeeping (25): idle lines, late fees, more than one provider, duplicate pricing
    hk = 25
    idle = sum(1 for p in lines if "idle" in p["flags"])
    hk -= min(10, 5 * idle)
    if len(providers) > 1:
        hk -= 6
    if account_fees_m > 0:
        hk -= 5
    if any("pricier plan" in p["flags"] for p in lines):
        hk -= 4
    hk = max(hk, 0)
    total = round(fit + pricing + leakage + hk)
    grade = "Excellent" if total >= 85 else "Good" if total >= 70 else "Room to improve" if total >= 50 else "Needs attention"
    return {
        "score": total, "grade": grade,
        "parts": [
            {"key": "fit", "label": "Plan fit", "score": round(fit), "max": 25,
             "note": f"{flagged} of {n} lines on the wrong-size plan or idle" if flagged else "Every line on a sensible plan"},
            {"key": "price", "label": "Pricing", "score": round(pricing), "max": 25,
             "note": f"Paying {over * 100:.0f}% above a like-for-like 2degrees setup" if over > 0.005 else "At or below current market pricing"},
            {"key": "leak", "label": "Leakage", "score": round(leakage), "max": 25,
             "note": f"{share * 100:.0f}% of mobile spend is roaming, extras and add-ons" if share > 0.005 else "No roaming or out-of-plan charges"},
            {"key": "hk", "label": "Housekeeping", "score": round(hk), "max": 25,
             "note": ", ".join(x for x in [f"{idle} idle line{'s' if idle != 1 else ''}" if idle else "",
                                           f"{len(providers)} providers" if len(providers) > 1 else "",
                                           "late fees" if account_fees_m > 0 else ""] if x) or "Tidy"},
        ],
    }
