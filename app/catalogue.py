"""Plan catalogue agent: Claude browses each provider's website and records their current business
mobile and broadband plans, including what's in them (included value), so analyses can compare like for like."""
import json
import re
import subprocess
import sys
import time
import traceback

from . import ai, db
from .config import CARRIERS, DATA_DIR

SITES = {
    "2degrees": "2degrees.nz (business plans under 2degrees.nz/business)",
    "spark": "spark.co.nz (business plans under spark.co.nz/business)",
    "one": "one.nz (business plans under one.nz/business)",
    "skinny": "skinny.co.nz (mobile and broadband; Skinny has no separate business range - record the standard plans)",
    "kogan": "koganmobile.co.nz and kogan.com/nz (mobile and broadband)",
    "slingshot": "slingshot.co.nz (broadband and mobile)",
    "orcon": "orcon.net.nz (broadband and mobile)",
    "contact": "contact.co.nz (broadband bundled with energy)",
    "mercury": "mercury.co.nz (broadband, including with energy)",
    "electric_kiwi": "electrickiwi.co.nz (broadband with power)",
    "sky": "sky.co.nz/broadband (Sky Broadband)",
    "now": "nownz.co.nz (NOW broadband)",
    "voyager": "voyager.nz (broadband and business plans)",
}
BENEFIT_SITES = {k: SITES[k] for k in ("2degrees", "spark", "one")}
DOMAIN_RE = r"^https?://([a-z0-9-]+\.)*(2degrees\.nz|spark\.co\.nz|one\.nz|skinny\.co\.nz|koganmobile\.co\.nz|kogan\.com|slingshot\.co\.nz|orcon\.net\.nz|contact\.co\.nz|mercury\.co\.nz|electrickiwi\.co\.nz|sky\.co\.nz|nownz\.co\.nz|voyager\.nz)(/|$)"

PROMPT = """You are cataloguing the CURRENT business {category} plans offered by {provider} in New Zealand, from the
provider's own website: {site}. Use web search and fetch the plan pages themselves - do not rely on memory or
third-party sites.

For every distinct business {category} plan currently on sale, record:
- plan_name exactly as marketed
- price_excl_gst: monthly price excluding GST (business prices are usually shown excl GST; if only incl GST is
  shown, divide by 1.15)
- data_gb: data allowance in GB (-1 = unlimited; for broadband use -1 if unlimited data)
- speed: broadband speed/technology (e.g. "Fibre 300", "Fibre Max", "Wireless"); empty for mobile
- inclusions: what's included - calls/texts, international minutes, roaming days or packs, data rollover,
  sharing/pooling, extra SIMs, hotspot, landline, router, static IP, support level, Microsoft 365 etc.
- conditions: term/contract, minimum number of lines, multi-line or bundle discounts, promo end dates, BYO vs device
- source_url: the page you got it from
- promo_text / promo_price_excl_gst / promo_months / term_months: any CURRENT promotion on the plan, e.g.
  "Half price for your first 6 months on a 12-month term" -> promo_price = half the price, promo_months = 6,
  term_months = 12; "3 months free on a 24 month term" -> promo_price = 0, promo_months = 3, term_months = 24.
  Use the business version of the plan. promo_months = 0 and promo_text = "" when there is no promotion.

Many provider plan pages draw their plan cards with JavaScript, so web_fetch returns a page with no prices. When
that happens, call render_page with the same URL: it loads the page in a real browser and returns the visible text,
including the cards, prices, strike-through list prices and promotion banners.

Include volume tiers / team plans as separate entries. When you have finished, call save_plans exactly once with
the complete list. If the site blocks you, save what you could verify and say so in notes."""

SAVE_TOOL = {
    "name": "save_plans",
    "description": "Save the complete list of current plans found on the provider's website.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "plans": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "plan_name": {"type": "string"},
                    "price_excl_gst": {"type": "number"},
                    "data_gb": {"type": "number"},
                    "speed": {"type": "string"},
                    "inclusions": {"type": "string"},
                    "conditions": {"type": "string"},
                    "source_url": {"type": "string"},
                    "promo_text": {"type": "string"},
                    "promo_price_excl_gst": {"type": "number"},
                    "promo_months": {"type": "integer"},
                    "term_months": {"type": "integer"},
                },
                "required": ["plan_name", "price_excl_gst", "data_gb", "speed", "inclusions", "conditions",
                             "source_url", "promo_text", "promo_price_excl_gst", "promo_months", "term_months"],
                "additionalProperties": False,
            }},
            "notes": {"type": "string"},
        },
        "required": ["plans", "notes"],
        "additionalProperties": False,
    },
}


RENDER_TOOL = {
    "name": "render_page",
    "description": "Load a URL in a headless browser (JavaScript runs) and return the page's visible text, including "
                   "plan cards, prices and promotion banners that plain fetching misses.",
    "strict": True,
    "input_schema": {"type": "object", "properties": {"url": {"type": "string", "format": "uri"}},
                     "required": ["url"], "additionalProperties": False},
}

RENDER_SCRIPT = """
import sys, json
from playwright.sync_api import sync_playwright
url = sys.argv[1]
with sync_playwright() as p:
    import os
    exe = os.environ.get("SO_CHROMIUM") or None
    b = p.chromium.launch(args=["--no-sandbox"], executable_path=exe)
    pg = b.new_page(viewport={"width": 1280, "height": 2000}, user_agent="Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
    pg.goto(url, wait_until="networkidle", timeout=45000)
    pg.wait_for_timeout(2500)
    for _ in range(6):
        pg.mouse.wheel(0, 1600); pg.wait_for_timeout(400)
    print(pg.evaluate("document.body.innerText"))
    b.close()
"""


RENDER_LOCK = DATA_DIR / ".render.lock"


def render_page(url, tries=2):
    """Visible text of a provider page in a headless browser. One browser at a time across the web and worker
    processes (the small droplet can't run two), with a retry."""
    import fcntl
    if not re.match(DOMAIN_RE, url):
        return "Only provider websites can be rendered."
    last = "Could not render."
    for _ in range(tries):
        with open(RENDER_LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                r = subprocess.run([sys.executable, "-c", RENDER_SCRIPT, url], capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                last = "Timed out rendering the page."
                continue
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        if r.returncode != 0:
            last = "Could not render: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1][:300]
            continue
        return re.sub(r"\n{3,}", "\n\n", r.stdout)[:60000]
    return last


def _run_agent(prompt, save_tool, purpose, max_rounds=14):
    """Agentic loop with server web tools, our render_page tool, and a single save tool. Returns the save input."""
    model = db.get_setting("agent_model") or db.get_setting("claude_model")
    cl = ai.client()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 16},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 24}, RENDER_TOOL, save_tool]
    for _ in range(max_rounds):
        with cl.messages.stream(model=model, max_tokens=32000, messages=messages, tools=tools,
                                output_config={"effort": "medium"}) as stream:
            resp = stream.get_final_message()
        inp, out = ai._usage_tokens(resp.usage)
        ai.log_usage(purpose, "claude", model, inp, out)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        calls = [b for b in resp.content if b.type == "tool_use"]
        saved = next((b.input for b in calls if b.name == save_tool["name"]), None)
        if saved is not None:
            return saved
        if calls:
            results = []
            for b in calls:
                if b.name == "render_page":
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": render_page(b.input.get("url", ""))})
                else:
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": "Unknown tool", "is_error": True})
            messages.append({"role": "user", "content": results})
            continue
        messages.append({"role": "user", "content": f"Please call {save_tool['name']} now with everything you found."})
    raise RuntimeError("Agent finished without saving")


def refresh(provider, category):
    """Run the agent for one provider/category. Returns the number of plans saved."""
    saved = _run_agent(PROMPT.format(category=category, provider=CARRIERS[provider], site=SITES[provider]),
                       SAVE_TOOL, f"catalogue:{provider}:{category}")
    now = time.time()
    with db.tx() as c:
        if saved["plans"]:
            c.execute("UPDATE plan_catalogue SET active=0 WHERE provider=? AND category=?", (provider, category))
        for p in saved["plans"]:
            c.execute("INSERT INTO plan_catalogue(provider, category, plan_name, price_excl_gst, data_gb, speed,"
                      " inclusions, conditions, source_url, fetched_at, promo_text, promo_price, promo_months,"
                      " term_months) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (provider, category, p["plan_name"], p["price_excl_gst"], p["data_gb"], p["speed"],
                       p["inclusions"], p["conditions"], p["source_url"], now, p.get("promo_text") or None,
                       p.get("promo_price_excl_gst"), p.get("promo_months") or 0, p.get("term_months") or 0))
    db.set_setting(f"catalogue_note:{provider}:{category}", json.dumps({"at": now, "count": len(saved["plans"]),
                                                                       "notes": saved["notes"][:500]}))
    return len(saved["plans"])


def _page_hash(url):
    import hashlib
    text = render_page(url)
    # Drop numbers that churn (dates, counters) only loosely: hash the visible text as-is
    return hashlib.sha1(text.encode()).hexdigest() if text and not text.startswith(("Only provider", "Timed out", "Could not")) else None


def pages_changed(provider, category):
    """Re-render the pages the last run read; True if any changed, or if nothing was ever recorded."""
    rows = db.q("SELECT DISTINCT source_url, page_hash FROM plan_catalogue WHERE provider=? AND category=? AND active=1",
                (provider, category))
    urls = {r["source_url"]: r["page_hash"] for r in rows if r["source_url"]}
    if not urls:
        return True
    for url, old in list(urls.items())[:6]:
        new = _page_hash(url)
        if new and new != old:
            return True
    return False


def stamp_hashes(provider, category):
    for r in db.q("SELECT DISTINCT source_url FROM plan_catalogue WHERE provider=? AND category=? AND active=1",
                  (provider, category)):
        if r["source_url"]:
            db.x("UPDATE plan_catalogue SET page_hash=? WHERE provider=? AND category=? AND source_url=?",
                 (_page_hash(r["source_url"]), provider, category, r["source_url"]))


def refresh_all(force=False):
    """The nightly run: every provider, both categories; the agent (which costs money) only runs when a provider's
    pages changed since last time or the catalogue is older than catalogue_days."""
    results = {}
    max_age = float(db.get_setting("catalogue_days") or 7) * 86400
    for provider in SITES:
        for category in ("mobile", "broadband"):
            key = f"{provider}:{category}"
            try:
                note = json.loads(db.get_setting(f"catalogue_note:{provider}:{category}") or "{}")
                stale = time.time() - float(note.get("at") or 0) > max_age
                if force or stale or pages_changed(provider, category):
                    results[key] = refresh(provider, category)
                    stamp_hashes(provider, category)
                else:
                    results[key] = "unchanged"
            except Exception as e:
                traceback.print_exc()
                results[key] = f"error: {e}"[:200]
    try:
        results["verify"] = verify_2degrees()
    except Exception as e:
        results["verify"] = f"error: {e}"[:200]
    db.set_setting("catalogue_refreshed_at", str(time.time()))
    db.set_setting("catalogue_last_result", json.dumps(results))
    return results


def due():
    last = float(db.get_setting("catalogue_refreshed_at") or 0)
    return time.time() - last > 7 * 24 * 3600


# ---------- verification: only offers we can see on 2degrees.nz right now are shown to customers ----------

def _tokens(text):
    """The checkable bits of an offer: percentages, dollar amounts, 'N months', and the longer words."""
    t = (text or "").lower()
    nums = re.findall(r"\d+\s*%|\$\s?\d+(?:\.\d+)?|\d+\s*months?|\d+\s*gb", t)
    words = [w for w in re.findall(r"[a-z]{5,}", t) if w not in ("business", "months", "month", "offer", "plans", "plan")]
    return [re.sub(r"\s+", "", n) for n in nums], words


def _present(page_text, text):
    page = re.sub(r"\s+", "", (page_text or "").lower())
    nums, words = _tokens(text)
    if nums and not all(n in page for n in nums):
        return False
    hits = sum(1 for w in words if w in page)
    return bool(nums) or hits >= max(2, len(words) // 2) if words else bool(nums)


def verify_2degrees():
    """Render each 2degrees offer's source page and keep only the ones whose key terms are still on it."""
    now = time.time()
    cache = {}

    debug = {}

    def text_of(url):
        if url not in cache:
            cache[url] = render_page(url) if url and re.match(DOMAIN_RE, url) else ""
            debug[url] = cache[url][:80] if cache[url].startswith(("Only", "Timed", "Could")) else f"{len(cache[url])} chars"
        return cache[url]

    kept = dropped = 0
    for b in db.q("SELECT * FROM benefits WHERE provider='2degrees' AND active=1"):
        ok = _present(text_of(b["source_url"]), f"{b['title']} {b['value']}")
        db.x("UPDATE benefits SET verified_at=? WHERE id=?", (now if ok else None, b["id"]))
        kept += ok
        dropped += not ok
    for r in db.q("SELECT * FROM plan_catalogue WHERE provider='2degrees' AND active=1"):
        ok = True
        if r["promo_text"]:
            ok = _present(text_of(r["source_url"]), r["promo_text"])
        db.x("UPDATE plan_catalogue SET verified_at=? WHERE id=?", (now if ok else None, r["id"]))
        kept += ok
        dropped += not ok
    db.set_setting("offers_verified_at", str(now))
    result = {"verified": kept, "unconfirmed": dropped, "pages": debug, "at": now}
    db.set_setting("offers_verify_result", json.dumps(result))
    return result


# ---------- provider benefits (beyond plan price) ----------

BENEFITS_PROMPT = """You are cataloguing everything {provider} (New Zealand) offers BUSINESS mobile and broadband customers
beyond the plan price, from the provider's own website: {site}. Use web search and fetch the actual pages - do not
rely on memory. Cover:
- roaming: daily roaming price and what it includes, destinations covered, Australia treatment, roaming packs, pay-as-you-go
  rates, data roaming caps or fair use.
- included: value bundled into business plans - international minutes and destinations, calls/texts to Australia,
  data carryover or rollover, data pooling/sharing, hotspot, spend control, apps, streaming or security extras,
  Microsoft 365, SIM-only vs device discounts.
- discount: partnership, association or membership discounts for businesses - Chambers of Commerce (regional or NZ
  Chambers), BusinessNZ, EMA, Federated Farmers / Farm Source, NZ Trucking, franchise or industry schemes, Xero/MYOB
  partners, multi-line / team discounts, bundle (mobile + broadband) credits, loyalty or sign-on credits.
- support: NZ-based support, account management, business hub tools, SLAs, device replacement, insurance.
- device: interest-free terms, trade-in, upgrade programmes, sign-on credit to clear existing handset balances.
- other: anything else a business would weigh (network coverage claims, 5G, eSIM, number porting help, contract terms,
  early termination rules).
Record each as a separate item with a short title, the detail, a headline value (e.g. "$8/day", "20% off", "300 mins")
and conditions, plus the URL it came from. Be specific with numbers and destinations. If a page's content doesn't appear in
web_fetch (many plan pages render with JavaScript), call render_page with the same URL to read it in a browser.
When finished, call save_benefits exactly once with the complete list; if a page blocked you, say so in notes."""

SAVE_BENEFITS = {
    "name": "save_benefits",
    "description": "Save the complete list of business benefits found on the provider's website.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "benefits": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["roaming", "included", "discount", "support", "device", "other"]},
                    "title": {"type": "string"}, "detail": {"type": "string"}, "value": {"type": "string"},
                    "conditions": {"type": "string"}, "source_url": {"type": "string"},
                },
                "required": ["category", "title", "detail", "value", "conditions", "source_url"],
                "additionalProperties": False,
            }},
            "notes": {"type": "string"},
        },
        "required": ["benefits", "notes"],
        "additionalProperties": False,
    },
}


def refresh_benefits(provider):
    saved = _run_agent(BENEFITS_PROMPT.format(provider=CARRIERS[provider], site=SITES[provider]), SAVE_BENEFITS,
                       f"benefits:{provider}")
    now = time.time()
    with db.tx() as c:
        if saved["benefits"]:
            c.execute("UPDATE benefits SET active=0 WHERE provider=?", (provider,))
        for b in saved["benefits"]:
            c.execute("INSERT INTO benefits(provider, category, title, detail, value, conditions, source_url, fetched_at)"
                      " VALUES (?,?,?,?,?,?,?,?)", (provider, b["category"], b["title"], b["detail"], b["value"],
                                                    b["conditions"], b["source_url"], now))
    db.set_setting(f"benefits_note:{provider}", json.dumps({"at": now, "count": len(saved["benefits"]),
                                                          "notes": saved["notes"][:500]}))
    return len(saved["benefits"])


def refresh_all_benefits():
    results = {}
    for provider in BENEFIT_SITES:
        try:
            results[provider] = refresh_benefits(provider)
        except Exception as e:
            traceback.print_exc()
            results[provider] = f"error: {e}"[:200]
    db.set_setting("benefits_last_result", json.dumps(results))
    return results
