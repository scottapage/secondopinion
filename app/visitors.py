"""Who is using the site and how: each visitor's story (device, referrer, pages in order, uploads, what they saw),
built from the events table. Used by Admin > Visitors and the daily digest email."""
import json
import time
import urllib.request

from . import analysis, db
from .config import SUPERADMIN_EMAIL

SKIP = {"error", "email_sent", "bills_moved", "site_version", "catalogue", "benefits", "ai_fallback"}
LABELS = {
    "upload_intent": "touched the upload box", "files_selected": "chose files", "upload": "uploaded bills",
    "email_received": "emailed bills in", "results_viewed": "viewed results", "example_viewed": "viewed the example",
    "example_cta": "clicked 'see an example'", "email_form_shown": "saw the email form",
    "email_captured": "asked for the summary by email", "email_verified_by_link": "verified email via link",
    "registered": "created an account", "dashboard_viewed": "opened the dashboard", "chat": "asked the AI",
    "offer_request": "requested an offer", "invoice_processed": "bill analysed", "share_created": "made a share card",
    "share_viewed": "share card viewed", "evidence_viewed": "checked a figure on the bill",
}


def geo(ip, lookups):
    """City/country/network for an IP, cached. `lookups` caps live lookups per page render (ip-api.com, free tier)."""
    if not ip or ip.startswith(("127.", "10.", "192.168.", "::1")):
        return None
    row = db.q1("SELECT * FROM ip_geo WHERE ip=?", (ip,))
    if row:
        return row
    if lookups[0] <= 0:
        return None
    lookups[0] -= 1
    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=status,city,regionName,country,org,isp", timeout=3) as r:
            j = json.loads(r.read())
        if j.get("status") != "success":
            return None
        row = {"ip": ip, "city": j.get("city"), "region": j.get("regionName"), "country": j.get("country"),
               "org": j.get("org") or j.get("isp"), "fetched_at": time.time()}
        db.x("INSERT OR REPLACE INTO ip_geo(ip, city, region, country, org, fetched_at) VALUES (?,?,?,?,?,?)",
             (ip, row["city"], row["region"], row["country"], row["org"], row["fetched_at"]))
        return row
    except Exception:
        return None


def own_emails():
    extra = {e.strip().lower() for e in (db.get_setting("own_emails") or "").split(",") if e.strip()}
    return {SUPERADMIN_EMAIL, "example@secondopinion.co.nz"} | extra


def is_stranger(email):
    """True when activity isn't the superadmin's own testing."""
    return (email or "").lower() not in own_emails()


def stories(hours=24 * 7, limit=150, with_analysis=True):
    since = time.time() - hours * 3600
    ev = db.q("SELECT * FROM events WHERE created_at>? ORDER BY id", (since,))
    # First pass: which email does each browser session belong to?
    sess_email = {}
    for e in ev:
        if e["session_id"] and e["email"]:
            sess_email[e["session_id"]] = e["email"]
    visitors = {}
    for e in ev:
        if e["kind"] in SKIP:
            continue
        email = e["email"] or sess_email.get(e["session_id"] or "")
        key = email or e["session_id"]
        if not key:
            continue
        meta = json.loads(e["meta"] or "{}")
        v = visitors.setdefault(key, {"key": key, "email": email, "sessions": set(), "first": e["created_at"],
                                      "last": e["created_at"], "device": None, "ref": None, "views": 0,
                                      "steps": [], "kinds": set(), "ips": []})
        v["email"] = v["email"] or email
        if e["session_id"]:
            v["sessions"].add(e["session_id"])
        v["first"], v["last"] = min(v["first"], e["created_at"]), max(v["last"], e["created_at"])
        v["device"] = meta.get("device") or v["device"]
        if meta.get("ip") and meta["ip"] not in v["ips"]:
            v["ips"].append(meta["ip"])
        v["ref"] = meta.get("ref") or v["ref"]
        v["kinds"].add(e["kind"])
        if e["kind"] == "page_view":
            if not meta.get("js"):
                continue
            v["views"] += 1
            label = meta.get("path") or "/"
        else:
            label = LABELS.get(e["kind"], e["kind"].replace("_", " "))
            if e["kind"] == "invoice_processed":
                label += f" ({meta.get('carrier') or '?'})"
            if e["kind"] == "upload" and meta.get("files"):
                label += f" ({meta['files']} file{'s' if meta['files'] != 1 else ''})"
        if v["steps"] and v["steps"][-1]["label"] == label:
            v["steps"][-1]["n"] += 1
            v["steps"][-1]["at"] = e["created_at"]
        else:
            v["steps"].append({"label": label, "at": e["created_at"], "n": 1, "kind": e["kind"]})
    out = []
    own = own_emails()
    lookups = [25]
    for v in visitors.values():
        v["geo"] = [g for g in (geo(ip, lookups) for ip in v["ips"][:3]) if g]
        v["where"] = "; ".join(", ".join(x for x in (g["city"], g["country"]) if x) + (f" ({g['org']})" if g.get("org") else "")
                              for g in v["geo"])
        sessions = list(v["sessions"])
        ph = ",".join("?" * len(sessions)) or "''"
        bills = db.q(f"SELECT i.id, i.filename, i.status, i.error, i.carrier, i.account_name, i.total_excl_gst,"
                     f" i.line_count, i.period_month, s.token FROM invoices i LEFT JOIN submissions s ON s.id=i.submission_id"
                     f" WHERE (i.owner_email=? OR i.session_id IN ({ph})) AND i.created_at>? ORDER BY i.id",
                     [v["email"] or "-"] + sessions + [since])
        v["bills"] = bills
        v["sessions"] = len(sessions)
        v["kinds"] = sorted(v["kinds"])
        v["own"] = (v["email"] or "").lower() in own
        v["results_url"] = next((f"/results/{b['token']}" for b in bills if b["token"]), None)
        v["headline"] = None
        done = [b for b in bills if b["status"] == "done"]
        if done and with_analysis:
            try:
                a = analysis.build(email=v["email"]) if v["email"] else analysis.build(session_id=sessions[0])
                if a.get("has_data"):
                    best = max(a["insights"], key=lambda i: i.get("saving") or 0)
                    sw = a.get("switch") or {}
                    v["headline"] = {"monthly": a["kpi"]["monthly"] + a["kpi"]["other_services"], "lines": a["kpi"]["lines"],
                                     "providers": ", ".join(p["name"] for p in a["providers"]) or ", ".join(
                                         {s["carrier"] for s in a.get("services", [])}),
                                     "finding": best["headline"] if best.get("saving") else None,
                                     "switch_saving": max(sw.get("saving_year") or 0, sw.get("optimised_saving_year") or 0),
                                     "score": a["score"]["score"]}
            except Exception:
                pass
        out.append(v)
    out.sort(key=lambda v: -v["last"])
    return out[:limit]


def summary(hours=24):
    """Counts for the digest: strangers only (the superadmin's own testing is left out)."""
    vs = stories(hours, limit=500, with_analysis=False)
    strangers = [v for v in vs if not v["own"]]
    return {
        "visitors": len([v for v in strangers if v["views"]]),
        "uploaders": len([v for v in strangers if any(b["status"] != "duplicate" for b in v["bills"])]),
        "bills": sum(len(v["bills"]) for v in strangers),
        "registered": len([v for v in strangers if "registered" in v["kinds"]]),
        "offers": len([v for v in strangers if "offer_request" in v["kinds"]]),
        "example": len([v for v in strangers if "example_viewed" in v["kinds"]]),
        "emails": len([v for v in strangers if "email_captured" in v["kinds"]]),
        "stories": strangers,
    }
