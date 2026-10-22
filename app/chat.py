"""'Ask AI' - conversational Q&A over one user's bills.

Each question gets a private in-memory SQLite copy containing only that user's
data, which Claude queries through a read-only SQL tool.
"""
import json
import sqlite3
import time

from . import ai, analysis, db

SYSTEM = """You are the SecondOpinion assistant. You help a New Zealand business understand its mobile phone bills
(Spark, One NZ, 2degrees) - mobile and broadband - and how to control the cost.

Style:
- Conversational, direct and concise: lead with the answer in a sentence or two. Usually under 120 words.
- Money is NZD excluding GST; format like $1,234.50. Refer to people by name where known, else by number.
- Use a markdown table when comparing several items. When a chart genuinely helps, add ONE fenced block:
  ```chart
  {"type": "bar" | "line" | "pie" | "stacked", "title": "...", "labels": ["..."], "series": [{"name": "...", "data": [1, 2]}]}
  ```
  (stacked = stacked bars; pie uses one series.) Keep charts small and readable.
- Never invent numbers - query the data with run_sql. If something isn't in the bills, say so.
- If the user asks about switching, better pricing or offers, suggest the "Make me an offer" button.
- Only discuss the user's bills, mobile plans, usage and telco costs.

The user's data is in SQLite tables (one row per line per bill month; month is 'YYYY-MM'):
  bills(month, carrier, account_number, account_name, invoice_number, invoice_date, total_excl_gst, gst, total_incl_gst)
  lines(month, carrier, account_number, number, user_name, plan_name, plan_cost, total, data_used_gb,
        data_allowance_gb /* -1 = unlimited */, voice_minutes, sms_count, roaming_cost, out_of_plan_cost, addon_cost,
        hardware_cost, other_cost, discount)
  charges(month, number, user_name, description, category, amount)
     category in (plan, addon, usage, roaming, hardware, one_off, discount, account, other)
  devices(month, number, description, monthly_amount, payments_remaining, remaining_balance)
  services(month, carrier, service_type /* broadband|landline|other */, address, plan_name, speed, amount)
     -- non-mobile services on the same bills, net monthly amount
  plan_catalogue(provider /* spark|one|2degrees */, category /* mobile|broadband */, plan_name, price_excl_gst,
                 data_gb /* -1 unlimited */, speed, inclusions, conditions, source_url)
     -- current business plans from the providers' websites: use it to explain what a plan includes or to compare.
     Note team structures: a "Team" price usually requires one full-price "Plan Leader" line on the account.
  benefits(provider, category /* roaming|included|discount|support|device|other */, title, detail, value, conditions, source_url)
     -- what each provider offers beyond the plan price (roaming rates, included value, association discounts)."""

TOOLS = [{
    "name": "run_sql",
    "description": "Run a read-only SQLite SELECT query over the user's bill data and get rows back as JSON.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A single SELECT statement"}},
        "required": ["sql"],
        "additionalProperties": False,
    },
}]


def _user_db(email):
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE bills(month, carrier, account_number, account_name, invoice_number, invoice_date,
                           total_excl_gst, gst, total_incl_gst);
        CREATE TABLE lines(month, carrier, account_number, number, user_name, plan_name, plan_cost, total,
                           data_used_gb, data_allowance_gb, voice_minutes, sms_count, roaming_cost, out_of_plan_cost,
                           addon_cost, hardware_cost, other_cost, discount);
        CREATE TABLE charges(month, number, user_name, description, category, amount);
        CREATE TABLE devices(month, number, description, monthly_amount, payments_remaining, remaining_balance);
        CREATE TABLE services(month, carrier, service_type, address, plan_name, speed, amount);
        CREATE TABLE plan_catalogue(provider, category, plan_name, price_excl_gst, data_gb, speed, inclusions,
                                    conditions, source_url);
        CREATE TABLE benefits(provider, category, title, detail, value, conditions, source_url);
    """)
    mem.executemany("INSERT INTO benefits VALUES (?,?,?,?,?,?,?)", [
        (r["provider"], r["category"], r["title"], r["detail"], r["value"], r["conditions"], r["source_url"])
        for r in db.q("SELECT * FROM benefits WHERE active=1")])
    mem.executemany("INSERT INTO plan_catalogue VALUES (?,?,?,?,?,?,?,?,?)", [
        (r["provider"], r["category"], r["plan_name"], r["price_excl_gst"], r["data_gb"], r["speed"], r["inclusions"],
         r["conditions"], r["source_url"]) for r in db.q("SELECT * FROM plan_catalogue WHERE active=1")])
    invs = db.q("SELECT * FROM invoices WHERE owner_email=? AND status='done'", (email.lower(),))
    ids = [i["id"] for i in invs]
    if not ids:
        return mem
    ph = ",".join("?" * len(ids))
    mem.executemany("INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?)", [
        (i["period_month"], i["carrier"], i["account_number"], i["account_name"], i["invoice_number"],
         i["invoice_date"], i["total_excl_gst"], i["gst"], i["total_incl_gst"]) for i in invs])
    lines = db.q(f"SELECT * FROM lines WHERE invoice_id IN ({ph})", ids)
    names = {}
    rows = []
    for l in lines:
        names[l["id"]] = l["user_name"]
        al = l["data_allowance_mb"]
        rows.append((l["period_month"], l["carrier"], l["account_number"], l["number"], l["user_name"], l["plan_name"],
                     l["plan_cost"], l["total"], None if l["data_used_mb"] is None else round(l["data_used_mb"] / 1024, 3),
                     None if al is None else (-1 if al == -1 else round(al / 1024, 2)), l["voice_minutes"],
                     l["sms_count"], l["roaming_cost"], l["overage_cost"], l["addon_cost"], l["hardware_cost"],
                     l["other_cost"], l["discount"]))
    mem.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    month = {i["id"]: i["period_month"] for i in invs}
    mem.executemany("INSERT INTO charges VALUES (?,?,?,?,?,?)", [
        (month[c["invoice_id"]], c["number"], names.get(c["line_id"]), c["description"], c["category"], c["amount"])
        for c in db.q(f"SELECT * FROM charges WHERE invoice_id IN ({ph})", ids)])
    mem.executemany("INSERT INTO services VALUES (?,?,?,?,?,?,?)", [
        (s["period_month"], s["carrier"], s["service_type"], s["address"], s["plan_name"], s["speed"], s["amount"])
        for s in db.q(f"SELECT * FROM services WHERE invoice_id IN ({ph})", ids)])
    mem.executemany("INSERT INTO devices VALUES (?,?,?,?,?,?)", [
        (month[d["invoice_id"]], d["number"], d["description"], d["monthly_amount"], d["payments_remaining"],
         d["remaining_balance"]) for d in db.q(f"SELECT * FROM devices WHERE invoice_id IN ({ph})", ids)])
    return mem


def _run_sql(mem, sql):
    s = sql.strip().rstrip(";")
    if not s.lower().startswith(("select", "with")) or ";" in s:
        return {"error": "Only a single SELECT statement is allowed."}
    try:
        cur = mem.execute(s)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(200)
        return {"columns": cols, "rows": rows, "truncated": len(rows) == 200}
    except Exception as e:
        return {"error": str(e)}


def _overview(email):
    a = analysis.build(email=email)
    if not a.get("has_data"):
        return "The user has no analysed bills yet."
    return json.dumps({
        "months": a["months"], "kpi": a["kpi"], "accounts": a["accounts"],
        "insights": [{"title": i["title"], "headline": i["headline"], "monthly_saving": i["saving"]}
                     for i in a["insights"]],
        "lines": [{"label": l["label"], "number": l["number"], "plan": l["plan"], "avg": l["avg"],
                   "flags": l["flags"]} for l in a["lines"][:60]],
    }, default=str)


def ask(email, question):
    email = email.lower()
    db.x("INSERT INTO chat_messages(email, role, content, created_at) VALUES (?,?,?,?)",
         (email, "user", question, time.time()))
    db.log_event("chat", email)
    history = db.q("SELECT role, content FROM chat_messages WHERE email=? ORDER BY id DESC LIMIT 11", (email,))[::-1]
    messages = [{"role": "user", "content": "Overview of my bills (for context):\n" + _overview(email)},
                {"role": "assistant", "content": "Thanks - I have the overview. What would you like to know?"}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    mem = _user_db(email)
    model = db.get_setting("chat_model")
    cl = ai.client()
    answer = "Sorry, I couldn't work that out - please try rephrasing."
    for _ in range(8):
        resp = cl.messages.create(
            model=model, max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS, messages=messages, output_config={"effort": "low"},
        )
        inp, out = ai._usage_tokens(resp.usage)
        ai.log_usage("chat", "claude", model, inp, out)
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    res = _run_sql(mem, b.input.get("sql", ""))
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(res, default=str)[:30000], "is_error": "error" in res})
            messages.append({"role": "user", "content": results})
            continue
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text:
            answer = text
        break
    db.x("INSERT INTO chat_messages(email, role, content, created_at) VALUES (?,?,?,?)",
         (email, "assistant", answer, time.time()))
    return answer
