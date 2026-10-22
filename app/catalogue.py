"""Plan catalogue agent: Claude browses each provider's website and records their current business
mobile and broadband plans, including what's in them (included value), so analyses can compare like for like."""
import json
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
                },
                "required": ["plan_name", "price_excl_gst", "data_gb", "speed", "inclusions", "conditions",
                             "source_url"],
                "additionalProperties": False,
            }},
            "notes": {"type": "string"},
        },
        "required": ["plans", "notes"],
        "additionalProperties": False,
    },
}


def refresh(provider, category):
    """Run the agent for one provider/category. Returns the number of plans saved."""
    model = db.get_setting("claude_model")
    cl = ai.client()
    messages = [{"role": "user", "content": PROMPT.format(category=category, provider=CARRIERS[provider],
                                                          site=SITES[provider])}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 20}, SAVE_TOOL]
    saved = None
    for _ in range(12):
        with cl.messages.stream(model=model, max_tokens=32000, messages=messages, tools=tools,
                                output_config={"effort": "medium"}) as stream:
            resp = stream.get_final_message()
        inp, out = ai._usage_tokens(resp.usage)
        ai.log_usage(f"catalogue:{provider}:{category}", "claude", model, inp, out)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        call = next((b for b in resp.content if b.type == "tool_use" and b.name == "save_plans"), None)
        if call:
            saved = call.input
            break
        if resp.stop_reason == "tool_use":
            # Only server tools and save_plans exist; anything else gets a nudge
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": "Unknown tool", "is_error": True}
                for b in resp.content if b.type == "tool_use"]})
            continue
        messages.append({"role": "user", "content": "Please call save_plans now with everything you found."})
    if not saved:
        raise RuntimeError("Catalogue agent finished without saving plans")
    now = time.time()
    with db.tx() as c:
        if saved["plans"]:
            c.execute("UPDATE plan_catalogue SET active=0 WHERE provider=? AND category=?", (provider, category))
        for p in saved["plans"]:
            c.execute("INSERT INTO plan_catalogue(provider, category, plan_name, price_excl_gst, data_gb, speed,"
                      " inclusions, conditions, source_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (provider, category, p["plan_name"], p["price_excl_gst"], p["data_gb"], p["speed"],
                       p["inclusions"], p["conditions"], p["source_url"], now))
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
and conditions, plus the URL it came from. Be specific with numbers and destinations. When finished, call
save_benefits exactly once with the complete list; if a page blocked you, say so in notes."""

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
    model = db.get_setting("claude_model")
    cl = ai.client()
    messages = [{"role": "user", "content": BENEFITS_PROMPT.format(provider=CARRIERS[provider], site=SITES[provider])}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 16},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 24}, SAVE_BENEFITS]
    saved = None
    for _ in range(14):
        with cl.messages.stream(model=model, max_tokens=32000, messages=messages, tools=tools,
                                output_config={"effort": "medium"}) as stream:
            resp = stream.get_final_message()
        inp, out = ai._usage_tokens(resp.usage)
        ai.log_usage(f"benefits:{provider}", "claude", model, inp, out)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        call = next((b for b in resp.content if b.type == "tool_use" and b.name == "save_benefits"), None)
        if call:
            saved = call.input
            break
        if resp.stop_reason == "tool_use":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": "Unknown tool", "is_error": True}
                for b in resp.content if b.type == "tool_use"]})
            continue
        messages.append({"role": "user", "content": "Please call save_benefits now with everything you found."})
    if not saved:
        raise RuntimeError("Benefits agent finished without saving")
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
