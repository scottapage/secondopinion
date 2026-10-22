"""Click any number, see it on the bill: finds the words on the original PDF that a figure came from and renders
the page images the viewer highlights them on."""
import re

import pdfplumber

from . import db
from .config import DATA_DIR

PAGE_DIR = DATA_DIR / "pages"
PAGE_DIR.mkdir(parents=True, exist_ok=True)
SCALE = 1.6


def page_png(inv, page_no):
    """Path to a rendered PNG of one page (cached)."""
    out = PAGE_DIR / f"{inv['id']}-{page_no}.png"
    if out.exists():
        return out
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(inv["path"])
    if page_no < 0 or page_no >= len(pdf):
        return None
    page = pdf[page_no]
    page.render(scale=SCALE).to_pil().save(out, optimize=True)
    return out


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _amount(s):
    return re.sub(r"[^\d.]", "", (s or "").replace(",", ""))


def _is_phone(term):
    d = _digits(term)
    return len(d) >= 8 and not re.search(r"[.]", term)


def _amount_forms(term):
    """The figure as printed, plus GST-inclusive and negative-looking variants."""
    try:
        v = float(_amount(term))
    except ValueError:
        return []
    forms = [(f"{v:.2f}", "excl GST")]
    forms.append((f"{v * 1.15:.2f}", "incl GST"))
    forms.append((f"{round(v * 1.15 + 0.004, 2):.2f}", "incl GST"))
    forms.append((f"{v / 1.15:.2f}", "excl GST"))
    return forms


def find(inv, terms):
    """[{page, x, y, w, h, text, term, note}] in page percentages, for each term found (first 6 hits per term)."""
    hits = []
    if not inv or inv["path"] == "-":
        return hits
    terms = [t for t in terms if t]
    try:
        with pdfplumber.open(inv["path"]) as pdf:
            for pno, page in enumerate(pdf.pages[:40]):
                W, H = float(page.width), float(page.height)
                words = page.extract_words(keep_blank_chars=False, extra_attrs=[])
                for i, w in enumerate(words):
                    for k in (1, 2, 3, 4):
                        grp = words[i:i + k]
                        if len(grp) < k:
                            break
                        text = " ".join(g["text"] for g in grp)
                        if k > 1 and (grp[-1]["top"] - grp[0]["top"] > 3 or grp[-1]["x0"] - grp[0]["x1"] > 60):
                            break
                        for t in terms:
                            note = ""
                            if _is_phone(t):
                                cand, want = _digits(text), _digits(t)
                                ok = len(cand) >= 8 and (cand == want or cand.endswith(want.lstrip("0")) and len(cand) <= len(want) + 3)
                            else:
                                cand = _amount(text).lstrip("0") or "0"
                                ok = False
                                for form, label in _amount_forms(t):
                                    if cand == form.lstrip("0") and re.search(r"\d", text):
                                        ok, note = True, label
                                        break
                            if ok and sum(1 for h in hits if h["term"] == t) < 6 and not any(
                                    h["page"] == pno and abs(h["x"] - 100 * grp[0]["x0"] / W) < 0.5 and
                                    abs(h["y"] - 100 * grp[0]["top"] / H) < 0.5 and h["term"] == t for h in hits):
                                hits.append({"page": pno, "x": round(100 * grp[0]["x0"] / W, 2),
                                             "y": round(100 * grp[0]["top"] / H, 2),
                                             "w": round(100 * (grp[-1]["x1"] - grp[0]["x0"]) / W, 2),
                                             "h": round(100 * (grp[-1]["bottom"] - grp[0]["top"]) / H, 2),
                                             "text": text, "term": t, "note": note})
    except Exception:
        pass
    # Prefer the amount hit on the same page as the phone number, so the viewer opens on the right page
    return hits


def page_count(inv):
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(inv["path"]))
    except Exception:
        return inv.get("pages") or 1
