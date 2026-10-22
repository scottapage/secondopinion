"""Shareable cards: one finding, one number, one sentence - anonymised (no names, numbers, companies or emails),
with an image for link previews. People forward these; that's how word spreads."""
import secrets
import time
from io import BytesIO

from . import analysis, db, learning
from .config import BASE_URL, DATA_DIR

CARD_DIR = DATA_DIR / "cards"
CARD_DIR.mkdir(parents=True, exist_ok=True)


def _text(a, kind, key):
    k, sw = a["kpi"], a.get("switch") or {}
    names = [p["name"] for p in a["providers"]]
    provs = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else (names[0] if names else "my provider")
    n = k["lines"]
    if kind == "insight":
        ins = next((i for i in a["insights"] if i["key"] == key), None)
        if not ins:
            return None
        m = len(ins["items"])
        if key == "idle":
            return ("Paying for phones nobody uses", analysis.fmt_money(ins["saving"] * 12) + " a year",
                    f"{m} of my {n} business mobile line{'s' if n != 1 else ''} on {provs} used almost no data or calls."
                    f" I only found out by getting a second opinion on the bill.")
        if key == "leakage":
            return ("Where the bill really leaks", analysis.fmt_money(ins["saving"] * 2 * 12) + " a year",
                    f"Roaming, add-ons and out-of-plan charges on my {provs} bills - outside the plans we actually chose.")
        if key == "plan_fit":
            return ("Wrong-size plans", analysis.fmt_money(ins["saving"] * 12) + " a year",
                    f"{m} of {n} lines on plans that don't match how they're used. {ins['body']}")
        if key == "plan_mix":
            return ("Same allowance, different prices", analysis.fmt_money(ins["saving"] * 12) + " a year",
                    f"{m} line{'s' if m != 1 else ''} paying more than others for the same data on {provs}.")
        if key == "broadband":
            return ("Broadband, compared", ins["headline"], ins["body"])
        return (ins["title"], ins["headline"], ins["body"])
    if kind == "switch":
        best = max(sw.get("optimised_saving_year") or 0, sw.get("saving_year") or 0)
        if best <= 0:
            return ("One bill instead of " + str(len(a["accounts"])), analysis.fmt_money(sw.get("future_month") or 0) + "/mth",
                    f"What my {n} lines with {provs} would cost on 2degrees, like for like.")
        return ("What switching would save", analysis.fmt_money(best) + " a year",
                f"My {n} business mobile line{'s' if n != 1 else ''} with {provs}: {analysis.fmt_money(sw['current_month'])}/mth"
                f" today, {analysis.fmt_money(sw['optimised_month'] if (sw.get('optimised_saving_year') or 0) > (sw.get('saving_year') or 0) else sw['future_month'])}/mth on 2degrees.")
    if kind == "score":
        s = a["score"]
        worst = min(s["parts"], key=lambda p: p["score"] / p["max"]) if s["parts"] else None
        return ("My mobile health score", f"{s['score']}/100", f"{s['grade']}. " + (worst["note"] if worst else ""))
    if kind == "fact":
        notes = [x for x in learning.notes_for(a.get("_owner_email"), a.get("_session_id")) if x.get("kind") == "fact"]
        if notes:
            f = notes[0]
            return ("Did you know?", f["title"], f["body"])
    return None


def create(kind, key, owner_email=None, session_id=None):
    a = analysis.build(email=owner_email, session_id=session_id)
    if not a.get("has_data"):
        return None
    a["_owner_email"], a["_session_id"] = owner_email, session_id
    t = _text(a, kind, key)
    if not t:
        return None
    title, big, body = t
    token = secrets.token_urlsafe(8)
    prov = ", ".join(p["name"] for p in a["providers"])[:60]
    db.x("INSERT INTO share_cards(token, owner_email, session_id, kind, title, big, body, provider, created_at)"
         " VALUES (?,?,?,?,?,?,?,?,?)", (token, owner_email, session_id, kind, title[:80], big[:60], body[:400],
                                         prov, time.time()))
    db.log_event("share_created", owner_email, session_id, card=kind, key=key)
    return token


def get(token):
    return db.q1("SELECT * FROM share_cards WHERE token=?", (token,))


def _font(size, bold=True):
    from PIL import ImageFont
    import matplotlib
    from pathlib import Path
    base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    for name in (["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]):
        p = base / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def image(card):
    """1200x630 PNG for link previews (cached)."""
    out = CARD_DIR / f"{card['token']}.png"
    if out.exists():
        return out
    from PIL import Image, ImageDraw
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), "#003660")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 18, H], fill="#F1600D")
    d.ellipse([W - 320, -160, W + 120, 280], fill="#0A4A7A")
    d.text((70, 58), card["title"].upper(), font=_font(26), fill="#4DBBE4")
    big = card["big"]
    size = 112 if len(big) <= 12 else 84 if len(big) <= 20 else 60
    d.text((66, 110), big, font=_font(size), fill="#FFFFFF")
    y = 110 + size + 40
    for line in _wrap(d, card["body"], _font(34, bold=False), W - 160)[:4]:
        d.text((70, y), line, font=_font(34, bold=False), fill="#DCEFF8")
        y += 46
    d.text((70, H - 90), "secondopinion.co.nz", font=_font(34), fill="#FFFFFF")
    d.text((70 + d.textlength("secondopinion.co.nz", font=_font(34)) + 24, H - 84),
           "free second opinion on NZ business telco bills", font=_font(24, bold=False), fill="#B3E2F3")
    if card.get("provider"):
        d.text((W - 70 - d.textlength("bills from " + card["provider"], font=_font(24, bold=False)), 70),
               "bills from " + card["provider"], font=_font(24, bold=False), fill="#B3E2F3")
    buf = BytesIO()
    im.save(out, "PNG", optimize=True)
    return out


def url(token):
    return f"{BASE_URL}/s/{token}"
