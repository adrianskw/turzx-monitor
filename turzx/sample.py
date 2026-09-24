"""Fixed data for reproducible layout previews; no widget update methods run."""

from collections import deque
from datetime import datetime, timedelta
from math import sin
from pathlib import Path
from types import SimpleNamespace

from turzx.widget import Widget
from turzx.widgets.agents import Agents, Session
from turzx.widgets.ai_usage import AiUsage
from turzx.widgets.burn import Burn
from turzx.widgets.clock import Clock
from turzx.widgets.clock_weather import ClockWeather
from turzx.widgets.cpu import Cpu
from turzx.widgets.forecast import Forecast
from turzx.widgets.gpu import Gpu
from turzx.widgets.memory import Memory
from turzx.widgets.network import Network
from turzx.widgets.weather import Weather, WeatherSnapshot

SAMPLE_DATE = datetime(2026, 9, 23, 10, 8)
SAMPLE_NOW = 1_800_000_000.0
GIB = 1024 ** 3


def _weather(widget: Weather) -> None:
    imperial = widget.options.get("units") == "imperial"
    readings = (72, 74, 76, 75, 73, 71, 70, 68, 66, 65, 64, 63) if imperial else (22, 23, 24, 24, 23, 22, 21, 20, 19, 18, 18, 17)
    codes = (0, 1, 2, 3, 2, 61, 61, 3, 2, 1, 0, 0)
    hours = tuple(
        (SAMPLE_DATE.replace(minute=0) + timedelta(hours=i), code, int(i < 9), temp)
        for i, (code, temp) in enumerate(zip(codes, readings), 1)
    )
    widget.snapshot = WeatherSnapshot(
        temperature=71 if imperial else 22,
        humidity=42,
        code=0,
        is_day=1,
        high=78 if imperial else 26,
        low=63 if imperial else 17,
        hours=hours,
        unit="°F" if imperial else "°C",
        fetched_at=SAMPLE_NOW - 60,
    )
    widget.preview_now = SAMPLE_NOW
    widget.error = None


def populate(widget: Widget) -> None:
    """Fill one built-in widget with values that never touch live data sources."""
    if isinstance(widget, ClockWeather):
        widget.clock.now = SAMPLE_DATE
        _weather(widget.weather)
    elif isinstance(widget, Clock):
        widget.now = SAMPLE_DATE
    elif isinstance(widget, Weather):
        _weather(widget)
    elif isinstance(widget, Cpu):
        widget.total, widget.temp, widget.power = 37.0, 56.0, 42.0
        widget.cores = [18, 45, 32, 62, 24, 57, 38, 74, 21, 49, 35, 55]
        widget.ram_pct = 62.5 if widget.ram_mode else None
        widget.history = deque((32 + 12 * sin(i / 5) + i / 8 for i in range(60)), maxlen=60)
    elif isinstance(widget, Forecast):
        _weather(widget.weather)
    elif isinstance(widget, Gpu):
        widget.handle = object()
        widget.util, widget.temp, widget.power = 64.0, 61.0, 142.0
        widget.mem_used, widget.mem_total = 8 * GIB, 12 * GIB
        widget.history = deque((44 + 18 * sin(i / 6) + i / 5 for i in range(60)), maxlen=60)
    elif isinstance(widget, Memory):
        widget.ram = SimpleNamespace(percent=62.5, used=20 * GIB, total=32 * GIB)
    elif isinstance(widget, AiUsage):
        examples = {
            "claude": (43, 31, "2h42m", "4d06h"),
            "codex": (26, 54, "1h17m", "2d12h"),
        }
        widget.data = {provider: examples[provider] for provider in widget.providers}
        widget.updated_at = {provider: SAMPLE_NOW - 60 for provider in widget.providers}
        widget.preview_now = SAMPLE_NOW
        widget.preview_elapsed = 0.0
        widget.errors.clear()
    elif isinstance(widget, Agents):
        widget.rate, widget.total_today = 18_500, 3_400_000
        widget.cache_hit = 96.0
        widget.preview_now = SAMPLE_NOW
        widget.active = []
        # one job running 14 min (dot between yellow and red), idle 3 and 22 min (timer
        # green-yellow and red); contexts span normal -> yellow -> red
        for tool, name, idle, running, tokens, hit, context, window in (
            ("codex", "turzx-5in-display", 20, 840, 870_000, 97, 60_000, 258_400),
            ("claude", "landing-page", 180, 600, 420_000, 82, 170_000, None),
            ("codex", "docs", 1320, 300, 98_000, 55, 230_000, 258_400),
        ):
            session = Session(tool, Path("/sample") / name)
            session.cwd = f"/sample/{name}"
            session.mtime = SAMPLE_NOW - idle
            session.turn_start = session.mtime - running
            session.today = tokens
            session.input_total, session.cache_read = 1000, hit * 10
            session.context, session.window = context, window
            session.model = "gpt-6.6-astra" if tool == "codex" else "claude-opus-5-5"
            session.effort = {"turzx-5in-display": "xhigh", "landing-page": "medium"}.get(name, "high")
            widget.active.append(session)
    elif isinstance(widget, Burn):
        widget.rate, widget.total_today, widget.cache_hit = 18_500, 3_400_000, 96.0
    elif isinstance(widget, Network):
        widget.down = deque([1_800_000.0] * widget.down.maxlen, maxlen=widget.down.maxlen)
        widget.up = deque([420_000.0] * widget.up.maxlen, maxlen=widget.up.maxlen)
    else:
        raise SystemExit(f"no sample data for widget {widget.kind!r}")
