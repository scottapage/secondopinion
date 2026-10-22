"""Model access. Claude (Anthropic SDK) is the default; any OpenAI-compatible chat API can read bills and run the
learning passes instead (OpenAI, Gemini, OpenRouter, Groq, xAI, Mistral, Ollama, LM Studio...). The research agents
and Ask AI use Claude's tool-use loop and stay on Claude."""
import base64
import json
import time
import urllib.error
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
    if provider == "openai":
        pin = float(db.get_setting("openai_price_in") or 0)
        pout = float(db.get_setting("openai_price_out") or 0)
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


# ---------- Claude ----------

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


# ---------- OpenAI-compatible chat completions ----------

def _page_images(pdf_path, max_pages=8, scale=1.3):
    """JPEG data URLs of the first pages, for vision-capable models reading scanned bills."""
    import io
    import pypdfium2 as pdfium
    out = []
    pdf = pdfium.PdfDocument(pdf_path)
    for i in range(min(len(pdf), max_pages)):
        im = pdf[i].render(scale=scale).to_pil().convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        out.append("data:image/jpeg;base64," + base64.standard_b64encode(buf.getvalue()).decode())
    return out


def openai_json(system, text, schema, purpose, invoice_id=None, images=None, base=None, key=None, model=None,
                provider="openai", timeout=900):
    """JSON from any OpenAI-compatible /chat/completions endpoint (structured output via response_format; a plain
    JSON instruction as fallback for servers that don't support json_schema)."""
    base = (base or db.get_setting("openai_base_url")).rstrip("/")
    key = key if key is not None else db.get_setting("openai_api_key")
    model = model or db.get_setting("openai_model")
    if not base:
        raise AIError("No OpenAI-compatible API URL is configured")
    user_content = [{"type": "text", "text": text}]
    for url in images or []:
        user_content.append({"type": "image_url", "image_url": {"url": url}})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    def call(b):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(b).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise AIError(f"{base} returned {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise AIError(f"Could not reach {base}: {e.reason}")

    try:
        resp = call(body)
    except AIError as e:
        # Some servers reject json_schema response formats; retry with a plain JSON instruction
        if "400" not in str(e) and "response_format" not in str(e):
            raise
        body.pop("response_format")
        body["messages"][0]["content"] += "\n\nRespond with a single JSON object only, matching this JSON schema:\n" + \
            json.dumps(schema)
        resp = call(body)
    usage = resp.get("usage") or {}
    log_usage(purpose, provider, model, usage.get("prompt_tokens"), usage.get("completion_tokens"), invoice_id)
    content = resp["choices"][0]["message"]["content"] or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content)
    start, end = content.find("{"), content.rfind("}")
    if start < 0:
        raise AIError("The model returned no JSON")
    return json.loads(content[start:end + 1]), model


def local_json(system, text, schema, purpose, invoice_id=None):
    """LM Studio / Ollama over Tailscale: an OpenAI-compatible server with no key. Text-only."""
    base = db.get_setting("local_ai_url").rstrip("/")
    if not base:
        raise AIError("Local AI URL is not configured")
    return openai_json(system, text, schema, purpose, invoice_id, base=base, key="", model=db.get_setting("local_ai_model"),
                       provider="local")


def local_available():
    base = db.get_setting("local_ai_url").rstrip("/")
    if not base:
        return False
    try:
        with urllib.request.urlopen(base + "/models", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def openai_test():
    """A tiny round trip, for the settings page."""
    t0 = time.time()
    out, model = openai_json("You answer with JSON.", "Return {\"ok\": true, \"model\": \"<your model name>\"}.",
                             {"type": "object", "properties": {"ok": {"type": "boolean"}, "model": {"type": "string"}},
                              "required": ["ok", "model"], "additionalProperties": False}, "test", timeout=60)
    return f"OK in {time.time() - t0:.1f}s - {model} said {json.dumps(out)[:80]}"


# ---------- one entry point for the JSON passes ----------

def json_call(system, instruction, schema, purpose, invoice_id=None, pdf_path=None, pdf_text=None, effort="medium",
              provider=None):
    """Run a JSON pass on the configured provider. With a PDF: Claude reads the document itself; an OpenAI-compatible
    model gets the text layer (and page images when vision is on). Returns (data, model)."""
    provider = provider or db.get_setting("learning_provider") or "claude"
    if provider == "openai":
        text = instruction
        if pdf_text:
            text += "\n\nInvoice text:\n" + pdf_text[:150000]
        images = None
        if pdf_path and db.get_setting("openai_vision") == "on" and (not pdf_text or len(pdf_text) < 200 or
                                                                      db.get_setting("openai_vision_always") == "on"):
            images = _page_images(pdf_path)
        return openai_json(system, text, schema, purpose, invoice_id, images=images)
    content = []
    if pdf_path:
        with open(pdf_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
    content.append({"type": "text", "text": instruction})
    return claude_json(system, content, schema, purpose, invoice_id, effort=effort)
