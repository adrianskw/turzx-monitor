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

    def update_once(self):
        self.clock.update()
        self.weather.update()

    def draw(self, d, w, h):
        t.card(d, w, h)
        if self.options.get("style") == "dense":
            self._draw_dense(d, w, h)
            return
        if self.options.get("arrangement") == "row":
            self._draw_row(d, w, h)
            return
        mid = w // 2
        snapshot = self.weather.snapshot
        self._draw_clock(d, mid - 4, h)
        self._draw_current(d, mid + 26, w - t.PAD, snapshot)
        self._draw_hourly(d, mid + 10, w - t.PAD, h, snapshot)

    def _draw_dense(self, d, w, h):
        """Small clock, separate current conditions, and five hourly rows."""
        clock_end, forecast_start = 222, w - 189
        d.line((clock_end - 1, 10, clock_end - 1, h - 11), fill=t.TRACK)
        d.line((forecast_start, 10, forecast_start, h - 11), fill=t.TRACK)

        now = self.clock.now
        stamp = now.strftime(self.options.get("format", "%-I:%M"))
        time_f = t.font(56, "bold")
        d.text((t.PAD, 10), stamp, font=time_f, fill=t.TEXT)
        if self.options.get("ampm", True):
            ampm_x = min(clock_end - 32, t.PAD + d.textlength(stamp, font=time_f) + 5)
            d.text((ampm_x, 24), now.strftime("%p"), font=t.font(17, "bold"), fill=t.MUTED)
        d.text((16, h - 36), now.strftime("%a, %b %-d"), font=t.font(20), fill=t.MUTED)

        snapshot = self.weather.snapshot
        if snapshot is None:
            d.text((clock_end + 10, 42), self.weather.error or "loading weather…",
                   font=t.font(18), fill=t.MUTED)
            return
        d.text((clock_end + 8, 12), glyph(snapshot.code, snapshot.is_day),
               font=t.font(48), fill=t.ORANGE if snapshot.is_day else t.MAGENTA)
        d.text((clock_end + 60, 1), f"{snapshot.temperature:.0f}°", font=t.font(65, "bold"), fill=t.TEXT)
        right = forecast_start - 13
        f = t.font(22, "bold")
        t.text_right(d, right, 9, f"↑{snapshot.high:.0f}°", f, t.RED)
        t.text_right(d, right, 39, f"↓{snapshot.low:.0f}°", f, t.CYAN)
        humidity = f"\U000F058E{round(snapshot.humidity)}%"
        t.text_right(d, right, 69, humidity, t.font(18, "bold"), t.MUTED)
        if age_text := self.weather.freshness_text(snapshot):
            d.text((clock_end + 10, h - 23), age_text, font=t.font(13), fill=t.YELLOW)

        for i, (when, code, is_day, temp) in enumerate(snapshot.hours[:5]):
            y = 18 + 20 * i
            d.text((forecast_start + 9, y), when.strftime("%-I%p").lower(),
                   font=t.font(14, "bold"), fill=t.MUTED)
            d.text((forecast_start + 70, y - 2), glyph(code, is_day),
                   font=t.font(20), fill=t.ORANGE if is_day else t.MAGENTA)
            t.text_right(d, w - 16, y - 1, f"{temp:.0f}°", t.font(18, "bold"), t.TEXT)

    def _draw_clock(self, d, right, h):
        now = self.clock.now
        if self.options.get("ampm_behind", False):
            # AM/PM as a huge faint watermark behind the whole time, so the digits get the full
            # width. Smaller/brighter versions tangled with the digits (text over text).
            time_f = t.font(int(self.options.get("time_size", 132)), "bold")
            time_y = -4
            if self.options.get("ampm", True):
                # As wide as the two minute digits ("M" ~ "08"), but capped so the letters fit
                # inside the card; vertically centred in the card.
                ampm = now.strftime("%p")
                ref = t.font(100, "bold")
                _, ry0, _, ry1 = d.textbbox((0, 0), ampm, font=ref)
                by_width = 2 * time_f.getlength("0") / (ref.getlength("M") / 100)
                by_height = (h - 16) / ((ry1 - ry0) / 100)
                pm_f = t.font(int(min(by_width, by_height)), "bold")
                _, py0, _, py1 = d.textbbox((0, 0), ampm, font=pm_f)
                t.text_right(d, right, h / 2 - (py0 + py1) / 2, ampm, pm_f, t.blend(t.CARD, t.MUTED, 0.15))
            hm = now.strftime(self.options.get("format", "%-I:%M"))
            if self.options.get("condense_one", True):
                t.time_right(d, right, time_y, hm, time_f, t.TEXT)
            else:  # every digit drawn normally
                t.text_right(d, right, time_y, hm, time_f, t.TEXT)
            date_size = int(self.options.get("date_size", 38))
            t.text_right(d, right, h - 16 - date_size, now.strftime("%a, %b %-d"), t.font(date_size), t.MUTED)
            return
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

    def _draw_current(self, d, left, right, snapshot):
        wx = self.weather
        if snapshot is None:
            d.text((left, 20), wx.error or "loading weather…", font=t.font(18), fill=t.MUTED)
            return
        icon = glyph(snapshot.code, snapshot.is_day)
        behind = self.options.get("icon_behind", False)
        if not behind:
            d.text((left, 8), icon, font=t.font(84), fill=t.ORANGE)
        # hi / lo / humidity column on the far right; temp right-aligned against it
        hs = int(self.options.get("hilo_size", 24))
        f = t.font(hs, "bold")
        step = hs + 6
        t.text_right(d, right, 10, f"↑{snapshot.high:.0f}°", f, t.RED)
        t.text_right(d, right, 10 + step, f"↓{snapshot.low:.0f}°", f, t.CYAN)
        if snapshot.humidity >= self.options.get("humidity_min", 0):  # e.g. 70: only when it's notable
            t.text_right(d, right, 10 + 2 * step, f"\U000F058E{t.pct_text(snapshot.humidity)}", f, t.MUTED)  # same font: digits line up
        col = right - d.textlength("↑100°", font=f) - 12
        ts = int(self.options.get("temp_size", 90))
        temp = f"{snapshot.temperature:.0f}°"
        # shrink-to-fit: a third digit (100°+) shrinks the number instead of growing into the clock
        temp_f = t.fit_font(d, temp, ts, col - left - 6)
        temp_y = -6 - 0.5 * (ts - 90) + (ts - temp_f.size) * 0.45  # keep it vertically centred when shrunk
        if behind:  # big faint glyph centred directly behind the temperature digits
            x0, y0, x1, y1 = d.textbbox((col - d.textlength(temp, font=temp_f), temp_y), temp, font=temp_f)
            icon_f = t.font(round(ts * 4 / 3))  # frames the digits (160 px behind 120 px)
            ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
            d.text(((x0 + x1) / 2 - (ix0 + ix1) / 2, (y0 + y1) / 2 - (iy0 + iy1) / 2), icon, font=icon_f,
                   fill=t.blend(t.CARD, t.ORANGE, 0.2))
        t.text_right(d, col, temp_y, temp, temp_f, t.TEXT)
        if age_text := wx.freshness_text(snapshot):
            if self.options.get("stale") == "icon":
                t.stale_mark(d, left - 14, 6, 24)
            else:
                d.text((left, 88), age_text, font=t.font(14), fill=t.MUTED)

    def _draw_hourly(self, d, left, right, h, snapshot):
        hours = snapshot.hours[:int(self.options.get("hours", 5))] if snapshot else ()
        if not hours:
            return
        top = h - 78  # anchored to the bottom so shorter panes keep the forecast intact
        col = (right - left) / len(hours)
        small, icon_f, temp_f = t.font(16, "bold"), t.font(26), t.font(22, "bold")
        # any 3-digit (or negative 2-digit) temp: drop "°" so 7 columns never collide
        self._deg = "" if any(len(f"{h[3]:.0f}") > 2 for h in hours) else "°"
        if self.options.get("hourly_icon_behind", False):
            self._draw_hourly_behind(d, hours, left, col, top + 6)  # uses less height; sit lower
            return
        for i, (when, code, is_day, temp) in enumerate(hours):
            cx = left + i * col + col / 2
            # stacked per column: hour, icon, temperature
            for text, fnt, y, color in (
                (when.strftime("%-I%p").lower(), small, top, t.MUTED),
                (glyph(code, is_day), icon_f, top + 20, t.ORANGE if is_day else t.MAGENTA),
                (f"{temp:.0f}{self._deg}", temp_f, top + 48, t.TEXT),
            ):
                d.text((cx - d.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=color)

    def _draw_hourly_behind(self, d, hours, left, col, top):
        """Hour label on top; the temperature below it with a faint weather glyph centred
        behind the digits, like the main temperature."""
        small, temp_f, icon_f = t.font(17, "bold"), t.font(24, "bold"), t.font(52)
        for i, (when, code, is_day, temp) in enumerate(hours):
            cx = left + i * col + col / 2
            label = when.strftime("%-I%p").lower()
            d.text((cx - d.textlength(label, font=small) / 2, top), label, font=small, fill=t.MUTED)
            temp_s = f"{temp:.0f}{self._deg}"
            tx = cx - d.textlength(temp_s, font=temp_f) / 2
            ty = top + 30
            x0, y0, x1, y1 = d.textbbox((tx, ty), temp_s, font=temp_f)
            icon = glyph(code, is_day)
            ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
            tint = t.ORANGE if is_day else t.MAGENTA
            d.text(((x0 + x1) / 2 - (ix0 + ix1) / 2, (y0 + y1) / 2 - (iy0 + iy1) / 2), icon, font=icon_f,
                   fill=t.blend(t.CARD, tint, 0.3))
            d.text((tx, ty), temp_s, font=temp_f, fill=t.TEXT)

    # ---- arrangement = "row": everything side by side in one short band ----------
    def _draw_row(self, d, w, h):
        """[Wed/Sep/30] [time]  |  [temp with faint icon] [hi/lo/humidity] [hourly list].
        Nothing is stacked under anything else, so the pane can be ~130 px tall."""
        now = self.clock.now
        mid = w // 2
        faint = t.blend(t.CARD, t.MUTED, 0.15)

        # date as a column on the left; each line right-aligned to a 3-char column
        date_f = t.font(int(self.options.get("date_size", 28)))
        col_right = t.PAD + d.textlength("Wed", font=date_f)
        step = (h - 20) / 3
        for i, part in enumerate((now.strftime("%a"), now.strftime("%b"), now.strftime("%-d"))):
            t.text_right(d, col_right, 8 + i * step, part, date_f, t.MUTED)

        # time: as large as fits between the date column and the centre line
        time_right = mid - 4
        time_f = t.fit_font(d, "12:59", int(self.options.get("time_size", 112)), time_right - col_right - 14)
        _, dy0, _, dy1 = d.textbbox((0, 0), "0", font=time_f)
        time_y = h / 2 - (dy0 + dy1) / 2
        if self.options.get("ampm", True):
            ampm = now.strftime("%p")
            ref = t.font(100, "bold")
            _, ry0, _, ry1 = d.textbbox((0, 0), ampm, font=ref)
            by_width = 2 * time_f.getlength("0") / (ref.getlength("M") / 100)
            by_height = (h - 16) / ((ry1 - ry0) / 100)
            pm_f = t.font(int(min(by_width, by_height)), "bold")
            _, py0, _, py1 = d.textbbox((0, 0), ampm, font=pm_f)
            t.text_right(d, time_right, h / 2 - (py0 + py1) / 2, ampm, pm_f, faint)
        t.text_right(d, time_right, time_y, now.strftime(self.options.get("format", "%-I:%M")), time_f, t.TEXT)

        snap = self.weather.snapshot
        left = mid + 14
        if snap is None:
            d.text((left, h / 2 - 12), self.weather.error or "loading weather…", font=t.font(18), fill=t.MUTED)
            return

        # hourly list on the far right: "11am [icon] 72°" rows
        hours = snap.hours[:int(self.options.get("hours", 5))]
        lf, tf, gf = t.font(16, "bold"), t.font(18, "bold"), t.font(20)
        list_right = w - t.PAD
        list_left = list_right - d.textlength("12am", font=lf) - 30 - d.textlength("100°", font=tf)
        if hours:
            row = (h - 16) / len(hours)
            label_right = list_left + d.textlength("12am", font=lf)
            for i, (when, code, is_day, temp) in enumerate(hours):
                ry = 8 + i * row + (row - 20) / 2
                t.text_right(d, label_right, ry + 1, when.strftime("%-I%p").lower(), lf, t.MUTED)
                d.text((label_right + 5, ry - 1), glyph(code, is_day), font=gf, fill=t.ORANGE if is_day else t.MAGENTA)
                t.text_right(d, list_right, ry, f"{temp:.0f}°", tf, t.TEXT)

        # hi / lo / humidity column, digits aligned (same font, right-aligned)
        hs = int(self.options.get("hilo_size", 22))
        hf = t.font(hs, "bold")
        hilo_right = list_left - 16
        hstep = (h - 20) / 3
        t.text_right(d, hilo_right, 8, f"↑{snap.high:.0f}°", hf, t.RED)
        t.text_right(d, hilo_right, 8 + hstep, f"↓{snap.low:.0f}°", hf, t.CYAN)
        t.text_right(d, hilo_right, 8 + 2 * hstep, f"\U000F058E{t.pct_text(snap.humidity)}", hf, t.MUTED)

        # current temperature: shrink-to-fit between the centre line and the hi/lo column,
        # with the weather glyph large and faint behind it
        temp_right = hilo_right - d.textlength("↑100°", font=hf) - 14
        temp = f"{snap.temperature:.0f}°"
        temp_f = t.fit_font(d, temp, int(self.options.get("temp_size", 96)), temp_right - left)
        x0, y0, x1, y1 = d.textbbox((0, 0), temp, font=temp_f)
        tx, ty = temp_right - d.textlength(temp, font=temp_f), h / 2 - (y0 + y1) / 2
        icon = glyph(snap.code, snap.is_day)
        icon_f = t.font(round(temp_f.size * 4 / 3))
        ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
        cx, cy = tx + (x0 + x1) / 2, ty + (y0 + y1) / 2
        d.text((cx - (ix0 + ix1) / 2, cy - (iy0 + iy1) / 2), icon, font=icon_f, fill=t.blend(t.CARD, t.ORANGE, 0.2))
        d.text((tx, ty), temp, font=temp_f, fill=t.TEXT)
        if self.options.get("stale") == "icon" and self.weather.freshness_text(snap):
            t.stale_mark(d, left, 6, 22)
