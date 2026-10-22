"""Plan catalogue agent: Claude browses each provider's website and records their current business
mobile and broadband plans, including what's in them (included value), so analyses can compare like for like."""
import json
import re
import subprocess
import sys
import time
import traceback

from . import ai, db
from .config import CARRIERS

SITES = {
    "2degrees": "2degrees.nz (business plans under 2degrees.nz/business)",
    "spark": "spark.co.nz (business plans under spark.co.nz/business)",
    "one": "one.nz (business plans under one.nz/business)",
}

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


def render_page(url):
    if not re.match(r"^https?://(www\.)?(2degrees\.nz|spark\.co\.nz|one\.nz)(/|$)", url):
        return "Only provider websites can be rendered."
    try:
        r = subprocess.run([sys.executable, "-c", RENDER_SCRIPT, url], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return "Timed out rendering the page."
    if r.returncode != 0:
        return "Could not render: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1][:300]
    text = re.sub(r"\n{3,}", "\n\n", r.stdout)
    return text[:60000]


def _run_agent(prompt, save_tool, purpose, max_rounds=14):
    """Agentic loop with server web tools, our render_page tool, and a single save tool. Returns the save input."""
    model = db.get_setting("claude_model")
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


def refresh_all():
    results = {}
    for provider in SITES:
        for category in ("mobile", "broadband"):
            try:
                results[f"{provider}:{category}"] = refresh(provider, category)
            except Exception as e:
                traceback.print_exc()
                results[f"{provider}:{category}"] = f"error: {e}"[:200]
    db.set_setting("catalogue_refreshed_at", str(time.time()))
    db.set_setting("catalogue_last_result", json.dumps(results))
    return results


def due():
    last = float(db.get_setting("catalogue_refreshed_at") or 0)
    return time.time() - last > 7 * 24 * 3600


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
    for provider in SITES:
        try:
            results[provider] = refresh_benefits(provider)
        except Exception as e:
            traceback.print_exc()
            results[provider] = f"error: {e}"[:200]
    db.set_setting("benefits_last_result", json.dumps(results))
    return results
