"""Clock and weather in one full-width card: time | current conditions | hourly list."""

import threading
import time

from turzx import theme as t
from turzx.widget import Widget, register
from turzx.widgets.clock import Clock
from turzx.widgets.weather import Weather, glyph


@register("clock_weather")
class ClockWeather(Widget):
    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        self.clock = Clock(**options)
        self.weather = Weather(**options)
        self._weather_due = 0.0
        self._fetching = False

    def _fetch_weather(self):
        try:
            self.weather.update()
        finally:
            self._weather_due = time.monotonic() + self.weather.next_delay()
            self._fetching = False

    def update(self):
        self.clock.update()
        # Weather is slow (HTTP); fetch it off to the side so the clock never stalls.
        if not self._fetching and time.monotonic() >= self._weather_due:
            self._fetching = True
            threading.Thread(target=self._fetch_weather, daemon=True).start()

    def draw(self, d, w, h):
        t.card(d, w, h)
        mid = w // 2
        self._draw_clock(d, mid - 4, h)
        self._draw_current(d, mid + 26, w - t.PAD)
        self._draw_hourly(d, mid + 10, w - t.PAD, h)

    def _draw_clock(self, d, right, h):
        now = self.clock.now
        big = t.font(108, "bold")
        if self.options.get("ampm", True):
            ampm = t.font(38, "bold")
            pm_w = d.textlength("PM", font=ampm)
            d.text((right - pm_w, 30), now.strftime("%p"), font=ampm, fill=t.MUTED)
            time_right = right - pm_w - 6
        else:
            time_right = right
        t.text_right(d, time_right, 6, now.strftime(self.options.get("format", "%-I:%M")), big, t.TEXT)
        t.text_right(d, right, h - 62, now.strftime("%a, %b %-d"), t.font(38), t.MUTED)

    def _draw_current(self, d, left, right):
        wx = self.weather
        if wx.current is None:
            d.text((left, 20), wx.error or "loading weather…", font=t.font(18), fill=t.MUTED)
            return
        c = wx.current
        d.text((left, 8), glyph(c["weather_code"], c["is_day"]), font=t.font(84), fill=t.ORANGE)
        # hi / lo / humidity column on the far right; temp right-aligned against it
        f = t.font(24, "bold")
        hi, lo = wx.daily["temperature_2m_max"][0], wx.daily["temperature_2m_min"][0]
        t.text_right(d, right, 10, f"↑{hi:.0f}°", f, t.RED)
        t.text_right(d, right, 40, f"↓{lo:.0f}°", f, t.CYAN)
        t.text_right(d, right, 70, f"\U000F058E{t.pct_text(c['relative_humidity_2m'])}", t.font(22), t.MUTED)
        col = right - d.textlength("↑100°", font=f) - 12
        t.text_right(d, col, -6, f"{c['temperature_2m']:.0f}°", t.font(90, "bold"), t.TEXT)

    def _draw_hourly(self, d, left, right, h):
        hours = self.weather.hours[:5]
        if not hours:
            return
        top = 112
        col = (right - left) / len(hours)
        small, icon_f, temp_f = t.font(16, "bold"), t.font(26), t.font(22, "bold")
        for i, (when, code, is_day, temp) in enumerate(hours):
            cx = left + i * col + col / 2
            # stacked per column: hour, icon, temperature
            for text, fnt, y, color in (
                (when.strftime("%-I%p").lower(), small, top, t.MUTED),
                (glyph(code, is_day), icon_f, top + 20, t.ORANGE if is_day else t.MAGENTA),
                (f"{temp:.0f}°", temp_f, top + 48, t.TEXT),
            ):
                d.text((cx - d.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=color)
