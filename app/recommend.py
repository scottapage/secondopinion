"""Maps a customer's current services to 2degrees business plans (the "switch scenario") and scores the
estate's mobile health. Deterministic and explainable; prices come from the plan catalogue with fallbacks.

Porting a number needs only: MSISDN, losing provider, losing-provider account number and account name -
all of which are on the bill. The user's own name and a delivery address (or an eSIM) are the only gaps."""
import re

from . import db
from .config import carrier_label

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


def find_plan(family, team=False):
    """The current 2degrees catalogue row for a plan family, matched on attributes (names change between refreshes):
    family in 10 | 100 | unl | data | fibre500 | fibre900 | hyper | wireless4g | wireless5g."""
    rows = _cat("2degrees", "broadband" if family.startswith(("fibre", "hyper", "wireless")) else "mobile")
    team_re = re.compile(r"team|additional|companion", re.I)
    cands = []
    for r in rows:
        name, speed = (r["plan_name"] or ""), (r["speed"] or "")
        txt = f"{name} {speed}".lower()
        is_team = bool(team_re.search(name))
        if family in ("10", "100", "unl"):
            if re.search(r"fair share|sharer|data only|data-only|watch|wearable", txt):
                continue
            want = {"10": 10, "100": 100, "unl": -1}[family]
            if r["data_gb"] != want or is_team != team:
                continue
        elif family == "data":
            if not re.search(r"data only|data-only|unlimited data", txt) or re.search(r"sharer", txt):
                continue
        elif family == "hyper":
            if not re.search(r"hyper", txt):
                continue
        elif family == "fibre900":
            if not re.search(r"fibre|fiber", txt) or re.search(r"hyper|wireless", txt):
                continue
            if re.search(r"500|300", txt) and not re.search(r"900|max|gig", txt):
                continue
        elif family == "fibre500":
            if not re.search(r"fibre|fiber", txt) or re.search(r"hyper|wireless", txt):
                continue
        elif family == "wireless5g":
            if not re.search(r"wireless", txt) or not re.search(r"5g", txt) or re.search(r"rural|starlink", txt):
                continue
        elif family == "wireless4g":
            if not re.search(r"wireless", txt) or re.search(r"5g|rural|starlink", txt):
                continue
        if r["price_excl_gst"]:
            cands.append(r)
    if not cands:
        return None
    return sorted(cands, key=lambda r: (-r["price_excl_gst"] if family == "fibre900" else r["price_excl_gst"]))[0]


FALLBACK_NAME = {
    ("10", False): "Business 10GB", ("100", False): "Business 100GB", ("100", True): "Business 100GB Team plan",
    ("unl", False): "Business Unlimited", ("unl", True): "Business Unlimited Team plan",
    ("data", False): "Business Unlimited Data", ("fibre500", False): "Business Fibre 500",
    ("fibre900", False): "Business Fibre 900", ("hyper", False): "Business Hyperfibre 2",
    ("wireless4g", False): "Unlimited 4G Wireless", ("wireless5g", False): "Unlimited 5G Wireless",
}


def plan_for(family, team=False):
    """(display name, price, promo dict or None) for a 2degrees plan family."""
    r = find_plan(family, team)
    if r:
        promo = None
        if (r.get("promo_months") or 0) > 0 and r.get("verified_at"):
            pm, pp, price = min(int(r["promo_months"]), 12), float(r.get("promo_price") or 0), r["price_excl_gst"]
            promo = {"text": r.get("promo_text") or "", "months": pm, "promo_price": pp, "term": r.get("term_months") or 0,
                     "first_year": round(pp * pm + price * (12 - pm), 2), "saving_year": round((price - pp) * pm, 2)}
        return r["plan_name"], r["price_excl_gst"], promo
    name = FALLBACK_NAME.get((family, team)) or FALLBACK_NAME.get((family, False))
    return name, FALLBACK.get(name, (0, None))[0], None


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


def pick_broadband(service):
    s = f"{service.get('plan') or ''} {service.get('speed') or ''}".lower()
    if re.search(r"hyper|2000|4000|8000", s):
        return "hyper", "hyperfibre"
    if re.search(r"900|max|boost|1000|gig", s):
        return "fibre900", "fastest standard fibre"
    if re.search(r"fibre|fiber|everyday|300|500", s):
        return "fibre500", "fibre, standard speed"
    if re.search(r"5g", s):
        return "wireless5g", "wireless 5G"
    if re.search(r"wireless|4g|rural", s):
        return "wireless4g", "wireless 4G"
    return "fibre500", "fibre"


ROAM_FALLBACK = {"2degrees": 8.0, "one": 8.70, "spark": 7.0}


def benefits(provider=None, category=None):
    """Active benefits; 2degrees rows only once the nightly check has confirmed them on 2degrees.nz."""
    rows = db.q("SELECT * FROM benefits WHERE active=1 AND (provider<>'2degrees' OR verified_at IS NOT NULL)"
                " ORDER BY category, id")
    return [r for r in rows if (not provider or r["provider"] == provider) and (not category or r["category"] == category)]


def daily_roaming_rate(provider):
    """Headline daily roaming price for a provider, from the researched benefits (fallback to known rates)."""
    for b in benefits(provider, "roaming"):
        text = f"{b['value']} {b['title']}"
        if re.search(r"australia|aussie", text, re.I):
            continue
        m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s*(?:/|per|a)\s*day", text, re.I)
        if m and float(m.group(1)) > 0:
            return float(m.group(1))
    return ROAM_FALLBACK.get(provider, 8.0)


def roaming_comparison(lines, n_months):
    """Estimate what the customer's roaming would cost on 2degrees: $0 in Australia, $8/day elsewhere."""
    days_au, days_other, spend = 0.0, 0.0, 0.0
    for p in lines:
        for r in p.get("roaming_items", []):
            amt = r["amount"] or 0
            if amt <= 0:
                continue
            spend += amt
            desc = (r["description"] or "").lower()
            m = re.search(r"(\d+)\s*(?:days?|x)\b|x\s*(\d+)", desc)
            days = float(m.group(1) or m.group(2)) if m else amt / daily_roaming_rate(p["carrier"])
            if re.search(r"australia|aussie|\bau\b|sydney|melbourne|brisbane|perth|gold coast", desc):
                days_au += days
            else:
                days_other += days
    if spend <= 0:
        return None
    rate = daily_roaming_rate("2degrees")
    future = days_other * rate
    return {"now_month": round(spend / n_months, 2), "future_month": round(future / n_months, 2),
            "days_au": round(days_au), "days_other": round(days_other),
            "saving_year": round((spend - future) / n_months * 12, 2), "rate": rate}


def chamber_offer():
    """Chambers of Commerce member pricing from the research (mobile % off for life, months free; fibre likewise)."""
    out = {"mobile_pct": 15, "mobile_free_months": 3, "fibre_pct": 10, "fibre_free_months": 3, "source": None}
    for b in benefits("2degrees", "discount"):
        title = (b["title"] or "").lower()
        if "chamber" not in title or re.search(r"wireless|power", title):
            continue
        text = f"{b['value']} {b['detail']}".lower()
        pct = re.search(r"(\d+)\s*%\s*off", text)
        free = re.search(r"(\d+)\s*months?\s*free", text)
        key = "fibre" if re.search(r"fibre|broadband", title) else "mobile"
        if pct:
            out[f"{key}_pct"] = int(pct.group(1))
        if free:
            out[f"{key}_free_months"] = int(free.group(1))
        out["source"] = out["source"] or b["source_url"]
        if key == "mobile":
            out["conditions"] = (b["conditions"] or "")[:200]
    return out


def gains_list():
    """What a customer gains on 2degrees, from the researched benefits; falls back to the known basics."""
    rows = benefits("2degrees")
    out = []
    for cat in ("included", "roaming", "discount", "device", "support"):
        for b in [r for r in rows if r["category"] == cat][:3]:
            out.append({"category": cat, "title": b["title"], "value": b["value"], "detail": b["detail"],
                        "conditions": b["conditions"]})
    if not out:
        out = [{"category": "included", "title": t, "value": "", "detail": "", "conditions": ""} for t in (
            "Unlimited calls and texts to NZ and Australia on every plan",
            "300 international minutes a month on 100GB and Unlimited plans",
            "Daily roaming in Australia at no extra cost; $8/day in 100+ other countries",
            "Unused data carries over for up to 12 months on capped plans")]
    return out


def _promo_summary(out_lines, out_services, cur_m, fut_m):
    """First-year cost of the like-for-like setup with the current promotions applied."""
    items = [x for x in out_lines + out_services if x.get("promo")]
    if not items:
        return None
    promo_saving = sum(x["promo"]["saving_year"] for x in items)
    first_year = fut_m * 12 - promo_saving
    return {"items": [{"name": x.get("label") or x.get("address") or x.get("type"), "plan": x["rec"],
                       "text": x["promo"]["text"], "saving": x["promo"]["saving_year"], "term": x["promo"]["term"]}
                      for x in items],
            "first_year_total": round(first_year, 2), "promo_saving": round(promo_saving, 2),
            "first_year_saving": round(cur_m * 12 - first_year, 2)}


def _chamber_scenario(out_lines, out_services, cur_m):
    """What the like-for-like 2degrees setup costs for a Chambers of Commerce member."""
    ch = chamber_offer()
    mobile = sum(l["price"] for l in out_lines)
    fibre = sum(s["price"] for s in out_services if re.search(r"fibre|fiber|hyper", (s["rec"] or ""), re.I))
    wireless = sum(s["price"] for s in out_services if not re.search(r"fibre|fiber|hyper", (s["rec"] or ""), re.I))
    month = mobile * (1 - ch["mobile_pct"] / 100) + fibre * (1 - ch["fibre_pct"] / 100) + wireless
    first_year = month * 12 - mobile * (1 - ch["mobile_pct"] / 100) * ch["mobile_free_months"] \
        - fibre * (1 - ch["fibre_pct"] / 100) * ch["fibre_free_months"]
    return {**ch, "month": round(month, 2), "saving_year": round((cur_m - month) * 12, 2),
            "first_year_total": round(first_year, 2), "first_year_saving": round(cur_m * 12 - first_year, 2)}


def switch_plan(lines, services, devices, n_months):
    """Build the switch scenario. `lines` are the internal per-line dicts from analysis.build."""
    out_lines, families = [], {}
    for p in sorted(lines, key=lambda p: -(p["plan_cost"] or p["avg"] or 0)):
        fam, why = pick_mobile(p)
        families.setdefault(fam, []).append((p, why))
    for fam, members in families.items():
        has_team = fam in ("100", "unl")
        for i, (p, why) in enumerate(members):
            role = "Plan Leader" if (i == 0 and has_team) else ("Team" if has_team else "")
            name, price, promo = plan_for(fam, team=(i > 0 and has_team))
            current = (p["plan_cost"] or 0) + max(p["addon_net_m"], 0)  # plan + add-ons actually paid
            out_lines.append({
                "number": p["number"], "display": p["display"], "label": p["label"], "user": p["user"],
                "carrier": p.get("carrier_label") or carrier_label(p["carrier"]), "carrier_code": p["carrier"],
                "account": p["account"], "account_name": p.get("account_name"),
                "plan": p["plan"], "current": round(current, 2), "extras": round(p["extras_m"], 2),
                "rec": name, "role": role, "price": price, "why": why, "saving": round(current - price, 2),
                "already": p["carrier"] == "2degrees", "promo": promo, "family": fam,
            })
    out_services = []
    for s in services:
        fam, why = pick_broadband(s)
        name, price, promo = plan_for(fam)
        out_services.append({"type": s["type"], "address": s["address"], "carrier": s["carrier"],
                             "carrier_code": s.get("carrier_code"), "plan": s["plan"], "current": s["avg"], "rec": name,
                             "price": price, "why": why, "saving": round(s["avg"] - price, 2), "promo": promo})
    # Right-sized scenario: unlimited lines whose usage isn't shown may fit the 100GB tier
    opt_lines = 0.0
    seen_100_leader = any(l["family"] == "100" and l["role"] == "Plan Leader" for l in out_lines)
    for l in out_lines:
        if l["family"] == "unl" and "usage not shown" in l["why"]:
            name, price, _ = plan_for("100", team=seen_100_leader)
            seen_100_leader = True
            l["alt"], l["alt_price"] = name, price or 0
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
        "roaming": roaming_comparison(lines, n_months),
        "chamber": _chamber_scenario(out_lines, out_services, cur_m),
        "promos": _promo_summary(out_lines, out_services, cur_m, fut_m),
        "benefits": gains_list(),
        "gains": [(b["title"] + (f" ({b['value']})" if b["value"] else "")) for b in gains_list()][:6]
                 + ["One bill, one account, one team price"],
        "porting": {
            "have": ["Phone numbers (MSISDN)", "Current provider", "Current account number", "Account name"],
            "need": ["Each user's name for the new SIM", "A delivery address - or go eSIM and skip the post"],
        },
    }


def health_score(lines, switch, non_mobile_m, mobile_m, providers, account_fees_m):
    """0-100 with four explainable parts. Higher is better."""
    if not lines and switch["services"]:
        cur = sum(s["current"] for s in switch["services"]) or 1
        fut = sum(s["price"] for s in switch["services"])
        over = max(cur - fut, 0) / cur
        total = round(100 * max(0, 1 - over / 0.3))
        grade = "Excellent" if total >= 85 else "Good" if total >= 70 else "Room to improve" if total >= 50 else "Needs attention"
        return {"score": total, "grade": grade, "parts": [
            {"key": "price", "label": "Broadband pricing", "score": total, "max": 100,
             "note": f"Paying {over * 100:.0f}% above the closest 2degrees business plan" if over > 0.005
             else "At or below current 2degrees pricing"}]}
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
