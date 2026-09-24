import json
import math
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from turzx import theme as t
from turzx.widget import Widget, register

# WMO weather code -> (Nerd Font Material Design icon, description)
CODES = {
    0: ("\U000F0599", "Clear"), 1: ("\U000F0595", "Mostly clear"), 2: ("\U000F0595", "Partly cloudy"),
    3: ("\U000F0590", "Overcast"), 45: ("\U000F0591", "Fog"), 48: ("\U000F0591", "Rime fog"),
    51: ("\U000F0597", "Light drizzle"), 53: ("\U000F0597", "Drizzle"), 55: ("\U000F0597", "Heavy drizzle"),
    61: ("\U000F0597", "Light rain"), 63: ("\U000F0597", "Rain"), 65: ("\U000F0596", "Heavy rain"),
    66: ("\U000F067F", "Freezing rain"), 67: ("\U000F067F", "Freezing rain"),
    71: ("\U000F0598", "Light snow"), 73: ("\U000F0598", "Snow"), 75: ("\U000F0F36", "Heavy snow"),
    77: ("\U000F0598", "Snow grains"), 80: ("\U000F0597", "Showers"), 81: ("\U000F0596", "Showers"),
    82: ("\U000F0596", "Violent showers"), 85: ("\U000F0598", "Snow showers"), 86: ("\U000F0598", "Snow showers"),
    95: ("\U000F0593", "Thunderstorm"), 96: ("\U000F0593", "Thunder + hail"), 99: ("\U000F0593", "Thunder + hail"),
}
NIGHT_CLEAR = "\U000F0594"
NIGHT_PARTLY_CLOUDY = "\U000F0F31"
HOURS = 5  # hourly forecast columns shown by this widget
FORECAST_HOURS = 24  # fetched, so layouts can show more (clock_weather `hours`, forecast widget)
_CACHE: dict = {}  # url -> (time.time(), WeatherSnapshot): widgets showing the same place share one fetch
_CACHE_LOCK = threading.Lock()
_FETCH_LOCKS: dict[str, threading.Lock] = {}
CACHE_SECONDS = 60


@dataclass(frozen=True)
class WeatherSnapshot:
    temperature: float
    humidity: float
    code: int
    is_day: int
    high: float
    low: float
    hours: tuple[tuple[datetime, int, int, float], ...]
    unit: str
    fetched_at: float


def glyph(code: int, is_day: int) -> str:
    icon = CODES.get(code, ("\U000F0590", ""))[0]
    if not is_day and code == 0:
        return NIGHT_CLEAR
    if not is_day and code in (1, 2):
        return NIGHT_PARTLY_CLOUDY
    return icon


def coordinates(options: dict) -> tuple[float, float] | None:
    """Return a validated location, or None when no location was supplied."""
    if "latitude" not in options and "longitude" not in options:
        return None
    if "latitude" not in options or "longitude" not in options:
        raise ValueError("latitude and longitude must be set together")
    if isinstance(options["latitude"], bool) or isinstance(options["longitude"], bool):
        raise ValueError("latitude and longitude must be numbers")
    try:
        lat, lon = float(options["latitude"]), float(options["longitude"])
    except (TypeError, ValueError) as exc:
        raise ValueError("latitude and longitude must be numbers") from exc
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("latitude must be -90 to 90 and longitude -180 to 180")
    return lat, lon


@register("weather")
class Weather(Widget):
    """Current conditions from Open-Meteo (no API key needed)."""

    interval = 600.0

    def __init__(self, **options):
        super().__init__(**options)
        self.snapshot: WeatherSnapshot | None = None
        self.location = coordinates(options)
        self.error = None if self.location is not None else "set TURZX_LATITUDE and TURZX_LONGITUDE"
        self.preview_now: float | None = None

    def freshness_text(self, snapshot: WeatherSnapshot) -> str | None:
        now = self.preview_now if self.preview_now is not None else time.time()
        age = max(0, now - snapshot.fetched_at)
        if self.error or age >= 2 * self.interval:
            return f"updated {t.duration_text(age)} ago"
        return None

    def next_delay(self):
        return 60.0 if self.error and self.location is not None else self.interval

    def update(self):
        if self.location is None:
            return
        imperial = self.options.get("units") == "imperial"
        lat, lon = self.location
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,weather_code,is_day"
            "&daily=temperature_2m_max,temperature_2m_min&forecast_days=2&timezone=auto"
            f"&hourly=temperature_2m,weather_code,is_day&forecast_hours={FORECAST_HOURS + 1}"
            + ("&temperature_unit=fahrenheit" if imperial else "")
        )
        with _CACHE_LOCK:
            fetch_lock = _FETCH_LOCKS.setdefault(url, threading.Lock())
        # Only one widget per location fetches. Waiters recheck the cache after it finishes.
        with fetch_lock:
            with _CACHE_LOCK:
                hit = _CACHE.get(url)
            if hit and time.time() - hit[0] < CACHE_SECONDS:
                self.snapshot, self.error = hit[1], None
                return
            self._fetch(url, imperial)

    def _fetch(self, url: str, imperial: bool) -> None:
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            current, daily = data["current"], data["daily"]
            hourly = data["hourly"]
            now = current["time"]
            hours = tuple(
                (datetime.fromisoformat(ts), int(hourly["weather_code"][i]),
                 int(hourly["is_day"][i]), float(hourly["temperature_2m"][i]))
                for i, ts in enumerate(hourly["time"]) if ts > now
            )[:FORECAST_HOURS]
            snapshot = WeatherSnapshot(
                temperature=float(current["temperature_2m"]),
                humidity=float(current["relative_humidity_2m"]),
                code=int(current["weather_code"]),
                is_day=int(current["is_day"]),
                high=float(daily["temperature_2m_max"][0]),
                low=float(daily["temperature_2m_min"][0]),
                hours=hours,
                unit="°F" if imperial else "°C",
                fetched_at=time.time(),
            )
            self.snapshot = snapshot
            self.error = None
            with _CACHE_LOCK:
                _CACHE[url] = (time.time(), snapshot)
        except Exception as e:
            self.error = f"weather: {type(e).__name__}"

    def draw(self, d, w, h):
        t.card(d, w, h)
        snapshot = self.snapshot
        if snapshot is None:
            d.text((t.PAD, 14), self.error or "loading weather…", font=t.font(t.BODY), fill=t.MUTED)
            return
        # current conditions: icon + temp (right-aligned so 100°+ grows left), hi/lo + humidity
        d.text((t.PAD, 4), glyph(snapshot.code, snapshot.is_day), font=t.font(76), fill=t.ORANGE)
        t.text_right(d, 250, -8, f"{snapshot.temperature:.0f}°", t.font(80, "bold"), t.TEXT)
        fb = t.font(22, "bold")
        t.fields_right(d, w - t.PAD, 10, [(f"↑{snapshot.high:.0f}°", t.RED), (f"↓{snapshot.low:.0f}°", t.CYAN)], fb, gap=" ")
        t.text_right(d, w - t.PAD, 44, f"\U000F058E{t.pct_text(snapshot.humidity)}", t.font(20), t.MUTED)
        if age_text := self.freshness_text(snapshot):
            d.text((t.PAD, 74), age_text, font=t.font(14), fill=t.MUTED)
        # hourly strip
        hours = snapshot.hours[:HOURS]
        if not hours:
            return
        top = 92
        col = (w - 2 * t.PAD) / len(hours)
        small, icon_f, temp_f = t.font(16, "bold"), t.font(26), t.font(21, "bold")
        for i, (when, code, is_day, temp) in enumerate(hours):
            cx = t.PAD + i * col + col / 2
            label = when.strftime("%-I%p").lower()
            d.text((cx - d.textlength(label, font=small) / 2, top), label, font=small, fill=t.MUTED)
            temp_s = f"{temp:.0f}°"
            row_w = 28 + d.textlength(temp_s, font=temp_f)
            x = cx - row_w / 2
            d.text((x, top + 20), glyph(code, is_day), font=icon_f, fill=t.ORANGE if is_day else t.MAGENTA)
            d.text((x + 28, top + 23), temp_s, font=temp_f, fill=t.TEXT)
