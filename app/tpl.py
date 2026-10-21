from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .analysis import fmt_data, fmt_money, fmt_number
from .config import BASE_URL, CARRIERS

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape())
env.filters["money"] = fmt_money
env.filters["data"] = fmt_data
env.filters["phone"] = fmt_number
env.filters["carrier"] = lambda c: CARRIERS.get(c, c or "Unknown")
env.filters["dt"] = lambda ts: datetime.fromtimestamp(ts, ZoneInfo("Pacific/Auckland")).strftime("%d %b %H:%M") if ts else "-"
env.globals["BASE_URL"] = BASE_URL


def render(name, **ctx):
    return env.get_template(name).render(**ctx)
