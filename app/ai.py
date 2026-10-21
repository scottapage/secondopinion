"""Thin wrapper over Claude (Anthropic SDK) and an optional local LM Studio model."""
import json
import time
import urllib.request

import anthropic

from . import db

# USD per million tokens (input, output)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class AIError(Exception):
    pass


def client():
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise AIError("Anthropic API key is not configured")
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)


def log_usage(purpose, provider, model, input_tokens, output_tokens, invoice_id=None):
    pin, pout = PRICES.get(model, (0, 0))
    cost = (input_tokens or 0) / 1e6 * pin + (output_tokens or 0) / 1e6 * pout
    db.x(
        "INSERT INTO ai_usage(purpose, provider, model, input_tokens, output_tokens, cost_usd, invoice_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (purpose, provider, model, input_tokens, output_tokens, cost, invoice_id, time.time()),
    )


def _usage_tokens(usage):
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) // 10 \
        + int((getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25)
    return inp, usage.output_tokens or 0


def claude_json(system, content, schema, purpose, invoice_id=None, model=None, effort="medium"):
    """Ask Claude for JSON matching `schema`. `content` is a list of content blocks."""
    model = model or db.get_setting("claude_model")
    with client().messages.stream(
        model=model,
        max_tokens=64000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        msg = stream.get_final_message()
    inp, out = _usage_tokens(msg.usage)
    log_usage(purpose, "claude", model, inp, out, invoice_id)
    if msg.stop_reason == "refusal":
        raise AIError("Claude declined to process this document")
    if msg.stop_reason == "max_tokens":
        raise AIError("Bill was too large to extract in one pass")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text), model


def local_json(system, text, schema, purpose, invoice_id=None):
    """Call LM Studio's OpenAI-compatible endpoint over Tailscale. Text-only."""
    base = db.get_setting("local_ai_url").rstrip("/")
    model = db.get_setting("local_ai_model")
    if not base:
        raise AIError("Local AI URL is not configured")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        resp = json.loads(r.read())
    usage = resp.get("usage") or {}
    log_usage(purpose, "local", model, usage.get("prompt_tokens"), usage.get("completion_tokens"), invoice_id)
    content = resp["choices"][0]["message"]["content"]
    # Some local models wrap JSON in prose or code fences
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1]), model


def local_available():
    base = db.get_setting("local_ai_url").rstrip("/")
    if not base:
        return False
    try:
        with urllib.request.urlopen(base + "/models", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False
