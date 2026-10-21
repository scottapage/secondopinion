"""Static PNG charts for emails (email clients can't run JavaScript)."""
import secrets

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .config import BASE_URL, CHART_DIR  # noqa: E402

NAVY, BLUE, INK, GREY, LIGHT = "#003660", "#009ED8", "#1D292E", "#61696D", "#E4E5E6"
CAT_COLOURS = {
    "plan": "#003660", "addon": "#009ED8", "usage": "#4DBBE4", "roaming": "#F1600D", "hardware": "#8E9699",
    "one_off": "#BBBFC0", "account": "#B3E2F3", "other": "#E4E5E6",
}
CAT_LABELS = {
    "plan": "Plans", "addon": "Add-ons", "usage": "Out-of-plan", "roaming": "Roaming", "hardware": "Hardware",
    "one_off": "One-off", "account": "Account", "other": "Other",
}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": LIGHT, "axes.labelcolor": GREY,
    "xtick.color": GREY, "ytick.color": GREY, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False,
})


def _save(fig):
    token = secrets.token_urlsafe(16)
    fig.savefig(CHART_DIR / f"{token}.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"{BASE_URL}/c/{token}.png"


def monthly_spend(a):
    """Stacked monthly spend by category. Returns a public image URL."""
    labels = a["month_labels"]
    fig, ax = plt.subplots(figsize=(5.6, 2.6))
    bottom = [0.0] * len(labels)
    present = [c for c in CAT_COLOURS if c in a["category_month"] and any(v > 0 for v in a["category_month"][c])]
    for cat in present:
        vals = [max(v, 0) for v in a["category_month"][cat]]
        ax.bar(labels, vals, bottom=bottom, color=CAT_COLOURS[cat], width=0.55, label=CAT_LABELS[cat],
               edgecolor="white", linewidth=0.6)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.grid(axis="y", color=LIGHT, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=min(len(present), 5), frameon=False, fontsize=8.5,
              handlelength=1, handleheight=1)
    return _save(fig)


def top_lines(a, n=8):
    lines = a["lines"][:n][::-1]
    fig, ax = plt.subplots(figsize=(5.6, 0.36 * len(lines) + 0.5))
    vals = [l["avg"] for l in lines]
    names = [(l["label"] or "")[:22] for l in lines]
    ax.barh(names, vals, color=[BLUE if "idle" not in l["flags"] else "#F1600D" for l in lines], height=0.6)
    for i, v in enumerate(vals):
        ax.text(v, i, f"  ${v:,.0f}", va="center", fontsize=9, color=INK)
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(length=0)
    ax.set_xlim(0, max(vals or [1]) * 1.2)
    return _save(fig)
