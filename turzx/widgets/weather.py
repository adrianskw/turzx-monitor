import json
import urllib.request
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
HOURS = 5  # hourly forecast columns


def glyph(code: int, is_day: int) -> str:
    icon = CODES.get(code, ("\U000F0590", ""))[0]
    return NIGHT_CLEAR if code <= 1 and not is_day else icon


@register("weather")
class Weather(Widget):
    """Current conditions from Open-Meteo (no API key needed)."""

    interval = 600.0

    def __init__(self, **options):
        super().__init__(**options)
        self.current = None
        self.hours = []
        self.error = None if "latitude" in options else "set latitude/longitude in layout.toml"

    def next_delay(self):
        return 60.0 if self.error else self.interval

    def update(self):
        if "latitude" not in self.options:
            return
        imperial = self.options.get("units") == "imperial"
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.options['latitude']}&longitude={self.options['longitude']}"
            "&current=temperature_2m,relative_humidity_2m,weather_code,is_day"
            "&daily=temperature_2m_max,temperature_2m_min&forecast_days=2&timezone=auto"
            f"&hourly=temperature_2m,weather_code,is_day&forecast_hours={HOURS + 1}"
            + ("&temperature_unit=fahrenheit" if imperial else "")
        )
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            self.current, self.daily = data["current"], data["daily"]
            hourly = data["hourly"]
            now = data["current"]["time"]
            self.hours = [
                (datetime.fromisoformat(ts), hourly["weather_code"][i], hourly["is_day"][i], hourly["temperature_2m"][i])
                for i, ts in enumerate(hourly["time"]) if ts > now
            ][:HOURS]
            self.unit = "°F" if imperial else "°C"
            self.error = None
        except Exception as e:
            self.error = f"weather: {type(e).__name__}"

    def draw(self, d, w, h):
        t.card(d, w, h)
        if self.current is None:
            d.text((t.PAD, 14), self.error or "loading weather…", font=t.font(t.BODY), fill=t.MUTED)
            return
        c = self.current
        # current conditions: icon + temp (right-aligned so 100°+ grows left), hi/lo + humidity
        d.text((t.PAD, 4), glyph(c["weather_code"], c["is_day"]), font=t.font(76), fill=t.ORANGE)
        t.text_right(d, 250, -8, f"{c['temperature_2m']:.0f}°", t.font(80, "bold"), t.TEXT)
        hi, lo = self.daily["temperature_2m_max"][0], self.daily["temperature_2m_min"][0]
        fb = t.font(22, "bold")
        t.fields_right(d, w - t.PAD, 10, [(f"↑{hi:.0f}°", t.RED), (f"↓{lo:.0f}°", t.CYAN)], fb, gap=" ")
        t.text_right(d, w - t.PAD, 44, f"\U000F058E{t.pct_text(c['relative_humidity_2m'])}", t.font(20), t.MUTED)
        # hourly strip
        if not self.hours:
            return
        top = 92
        d.line((t.PAD, top - 6, w - t.PAD, top - 6), fill=t.TRACK, width=1)
        col = (w - 2 * t.PAD) / len(self.hours)
        small, icon_f, temp_f = t.font(16, "bold"), t.font(26), t.font(21, "bold")
        for i, (when, code, is_day, temp) in enumerate(self.hours):
            cx = t.PAD + i * col + col / 2
            label = when.strftime("%-I%p").lower()
            d.text((cx - d.textlength(label, font=small) / 2, top), label, font=small, fill=t.MUTED)
            temp_s = f"{temp:.0f}°"
            row_w = 28 + d.textlength(temp_s, font=temp_f)
            x = cx - row_w / 2
            d.text((x, top + 20), glyph(code, is_day), font=icon_f, fill=t.ORANGE if is_day else t.MAGENTA)
            d.text((x + 28, top + 23), temp_s, font=temp_f, fill=t.TEXT)
