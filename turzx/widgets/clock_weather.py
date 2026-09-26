"""Clock and weather in one full-width card: time | current conditions | hourly list."""

from __future__ import annotations

import threading
import time
from datetime import datetime

from turzx import sun, theme as t
from turzx.widget import Widget, register
from turzx.widgets.clock import Clock
from turzx.widgets.weather import Weather, glyph


@register("clock_weather")
class ClockWeather(Widget):
    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        if options.get("style") == "band":
            for name, default, minimum in (("hours", 0, 0), ("gap", 22, 0),
                                           ("time_size", 60, 1), ("date_size", 20, 1),
                                           ("temp_size", 60, 1), ("hilo_size", 18, 1)):
                value = options.get(name, default)
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{name} must be an integer >= {minimum} for band style")
        self.clock = Clock(**options)
        self.weather = Weather(**options)
        self._weather_due = 0.0
        self._fetching = False
        self._time_partner: tuple[ClockWeather, int, int] | None = None

    def align_with_time(self, partner: ClockWeather, width: int, height: int) -> None:
        """Use a time card's measured digits without relying on render order."""
        self._time_partner = partner, width, height

    def _fetch_weather(self):
        try:
            self.weather.update()
        finally:
            self._weather_due = time.monotonic() + self.weather.next_delay()
            self._fetching = False

    def update(self):
        self.clock.update()
        if self.options.get("show") == "time" or self.weather.location is None:
            return
        # Weather is slow (HTTP); fetch it off to the side so the clock never stalls.
        if not self._fetching and time.monotonic() >= self._weather_due:
            self._fetching = True
            threading.Thread(target=self._fetch_weather, daemon=True).start()

    def update_once(self):
        self.clock.update()
        if self.options.get("show") == "time" or self.weather.location is None:
            return
        self.weather.update()

    def draw(self, d, w, h):
        t.card(d, w, h)
        if self.options.get("style") == "dense":
            self._draw_dense(d, w, h)
            return
        if self.options.get("style") == "band":
            self._draw_band(d, w, h)
            return
        if self.options.get("arrangement") == "row":
            self._draw_row(d, w, h)
            return
        mid = w // 2
        snapshot = self.weather.snapshot
        self._draw_clock(d, mid - 4, h)
        self._draw_current(d, mid + 26, w - t.PAD, snapshot)
        self._draw_hourly(d, mid + 10, w - t.PAD, h, snapshot)

    def _draw_band(self, d, w, h):
        """One pane, left to right: time (small raised AM/PM) over the date; weather icon and
        temperature; hi/lo/humidity column; then, with `hours` > 0, an hourly forecast in
        columns filling the rest. Sizes: time_size (60), date_size (20), temp_size (60),
        hilo_size (18); gap (22) between groups."""
        o, now = self.options, self.clock.now
        gap = int(o.get("gap", 22))
        time_f = t.font(int(o.get("time_size", 60)), "bold")
        date_f = t.font(int(o.get("date_size", 20)))
        am_f = t.font(t.px(t.size_of(time_f) * 0.3), "bold")
        stamp = now.strftime(o.get("format", "%-I:%M"))
        date = now.strftime("%a, %b %-d")
        _, ty0, _, ty1 = d.textbbox((0, 0), "0", font=time_f)
        _, dy0, _, dy1 = d.textbbox((0, 0), "0", font=date_f)
        block = (ty1 - ty0) + 10 + (dy1 - dy0)  # time + date, centred vertically
        top = (h - block) / 2
        d.text((t.PAD, top - ty0), stamp, font=time_f, fill=t.TEXT)
        time_w = max(d.textlength("12:59", font=time_f), d.textlength(stamp, font=time_f))
        am_w = d.textlength("AM", font=am_f) + 5 if o.get("ampm", True) else 0
        if o.get("ampm", True):
            _, ay0, _, _ = d.textbbox((0, 0), "AM", font=am_f)
            d.text((t.PAD + d.textlength(stamp, font=time_f) + 5, top - ay0), now.strftime("%p"), font=am_f, fill=t.MUTED)
        d.text((t.PAD + 2, top + (ty1 - ty0) + 10 - dy0), date, font=date_f, fill=t.MUTED)
        x = t.PAD + max(time_w + am_w, d.textlength("Wed, Sep 30", font=date_f),
                        d.textlength(date, font=date_f)) + gap

        snap = self.weather.snapshot
        if snap is None:
            d.text((x, h / 2 - 12), self.weather.error or "loading weather…", font=t.font(18), fill=t.MUTED)
            return
        icon = glyph(snap.code, snap.is_day)
        temp_f = t.font(int(o.get("temp_size", 60)), "bold")
        icon_f = t.font(t.px(t.size_of(temp_f) * 0.62))
        ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
        d.text((x - ix0, h / 2 - (iy0 + iy1) / 2), icon, font=icon_f, fill=t.ORANGE if snap.is_day else t.MAGENTA)
        x += ix1 - ix0 + 8
        temp = f"{snap.temperature:.0f}°"
        _, py0, _, py1 = d.textbbox((0, 0), temp, font=temp_f)
        tw = d.textlength("100°", font=temp_f)
        t.text_right(d, x + tw, h / 2 - (py0 + py1) / 2, temp, temp_f, t.TEXT)
        x += tw + 16
        hf = t.font(int(o.get("hilo_size", 18)), "bold")
        rows = [(f"↑{snap.high:.0f}°", t.RED), (f"↓{snap.low:.0f}°", t.CYAN), (f"\U000F058E{t.pct_text(snap.humidity)}", t.MUTED)]
        hw = max(d.textlength(text, font=hf) for text, _ in rows)
        _, hy0, _, hy1 = d.textbbox((0, 0), "0", font=hf)
        step = min((h - 16) / 3, (hy1 - hy0) + 10)
        for i, (text, color) in enumerate(rows):
            cy = h / 2 + (i - 1) * step
            t.text_right(d, x + hw, cy - (hy0 + hy1) / 2, text, hf, color)
        if o.get("stale") == "icon" and self.weather.freshness_text(snap):
            t.stale_mark(d, x - 12, 4, 18)
        x += hw + gap

        lf, gf, vf = t.font(15, "bold"), t.font(22), t.font(18, "bold")
        hours = snap.hours[:o.get("hours", 0)]
        if hours:
            deg = "" if any(len(f"{hr[3]:.0f}") > 2 for hr in hours) else "°"
            min_col = max(d.textlength("12pm", font=lf), d.textlength(f"107{deg}", font=vf),
                          d.textbbox((0, 0), glyph(0, 1), font=gf)[2]) + 8
            fit = max(0, int((w - t.PAD - x) // min_col))
            hours = hours[:fit]
        if not hours:
            return
        d.line((x - gap / 2, 12, x - gap / 2, h - 13), fill=t.TRACK)
        col = (w - t.PAD - x) / len(hours)
        deg = "" if any(len(f"{hr[3]:.0f}") > 2 for hr in hours) else "°"
        for i, (when, code, is_day, temp_h) in enumerate(hours):
            cx = x + (i + 0.5) * col
            for text, f, fill, cy in ((when.strftime("%-I%p").lower(), lf, t.MUTED, h * 0.24),
                                      (glyph(code, is_day), gf, t.ORANGE if is_day else t.MAGENTA, h * 0.5),
                                      (f"{temp_h:.0f}{deg}", vf, t.TEXT, h * 0.77)):
                x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
                d.text((cx - (x0 + x1) / 2, cy - (y0 + y1) / 2), text, font=f, fill=fill)

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
        temp_y = -6 - 0.5 * (ts - 90) + (ts - t.size_of(temp_f)) * 0.45  # keep it vertically centred when shrunk
        if behind:  # big faint glyph centred directly behind the temperature digits
            x0, y0, x1, y1 = d.textbbox((col - d.textlength(temp, font=temp_f), temp_y), temp, font=temp_f)
            icon_f = t.font(t.px(ts * 4 / 3))  # frames the digits (160 px behind 120 px)
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
        """[Wed/Sep/30] [time]  |  [condition + temp] [hi/lo/humidity] [hourly list].
        Nothing is stacked under anything else, so the pane can be ~130 px tall."""
        # show = "time" / "weather": draw only that part, over the full width, so time and
        # current weather can each be their own card (two widgets with the same options)
        show = self.options.get("show", "both")
        if show != "weather":
            right = w - t.PAD if show == "time" else round(w * float(self.options.get("split", 0.5))) - 4
            self._draw_row_time(d, right, h)
        if show == "time":
            return
        mid = round(w * float(self.options.get("split", 0.5)))  # time | weather boundary
        left = t.PAD if show == "weather" else mid + int(self.options.get("gap", 14))
        self._draw_row_weather(d, left, w, h)

    def _row_time_geometry(self, d, time_right, h):
        """Measure the date column and time digits for drawing or weather alignment."""
        date_f = t.font(int(self.options.get("date_size", 28)))
        col_w = d.textlength("Wed", font=date_f)
        if self.options.get("date_side", "left") == "right":
            col_right = time_right
            time_right = time_right - col_w - 14
            time_left = t.PAD
        else:
            col_right = t.PAD + col_w
            time_left = col_right + 14
        raised = self.options.get("ampm", True) and self.options.get("ampm_style", "watermark") == "raised"
        if raised:  # small AM/PM top-right of the digits, like a superscript: reserve its width
            raised_f = t.font(int(self.options.get("ampm_size", 22)), "bold")
            time_right -= d.textlength("AM", font=raised_f) + 6
        below = self.options.get("ampm", True) and self.options.get("ampm_style", "watermark") == "below"
        room = h - 16 if t.MARGIN is None else h - 2 * t.PAD  # height the digits may use
        if below:  # small AM/PM under the digits, right-aligned with them: leave it a line
            below_f = t.font(int(self.options.get("ampm_size", 18)), "bold")
            _, by0, _, by1 = d.textbbox((0, 0), "AM", font=below_f)
            room -= by1 - by0 + (8 if t.MARGIN is None else 6)
        # time: as large as fits beside the date column, and within the card height
        ref = t.font(100, "bold")
        _, ry0, _, ry1 = d.textbbox((0, 0), "0", font=ref)
        max_by_height = int(room / ((ry1 - ry0) / 100))
        time_f = t.fit_font(d, "12:59", min(int(self.options.get("time_size", 112)), max_by_height),
                            time_right - time_left)
        _, dy0, _, dy1 = d.textbbox((0, 0), "0", font=time_f)
        time_y = h / 2 - (dy0 + dy1) / 2
        if below and t.MARGIN is not None:  # digits on the top margin (the line on the bottom one)
            time_y = t.PAD - dy0
        elif below:  # digits and AM/PM line spaced evenly: the same gap above, between and below
            time_y = (h - (dy1 - dy0) - (by1 - by0)) / 3 - dy0
        return date_f, col_right, time_right, time_f, time_y, dy0, dy1

    def _draw_row_time(self, d, time_right, h):
        """Date column (left, or right with date_side = "right"), then the time as large as fits."""
        now = self.clock.now
        faint = t.blend(t.CARD, t.MUTED, 0.15)
        date_f, col_right, time_right, time_f, time_y, dy0, dy1 = self._row_time_geometry(d, time_right, h)
        if t.MARGIN is None:
            first, step = 8, (h - 20) / 3
        else:  # "Wed" on the top margin, the day's digits on the bottom one, the month between
            c0, c1 = t.ink(d, date_f)
            first = t.PAD - c0
            step = ((h - t.PAD - c1) - first) / 2
        for i, part in enumerate((now.strftime("%a"), now.strftime("%b"), now.strftime("%-d"))):
            t.text_right(d, col_right, t.px(first + i * step), part, date_f, t.MUTED)

        raised = self.options.get("ampm", True) and self.options.get("ampm_style", "watermark") == "raised"
        below = self.options.get("ampm", True) and self.options.get("ampm_style", "watermark") == "below"
        if below:
            below_f = t.font(int(self.options.get("ampm_size", 18)), "bold")
            _, by0, _, _ = d.textbbox((0, 0), "AM", font=below_f)
        if raised:
            raised_f = t.font(int(self.options.get("ampm_size", 22)), "bold")
        if below:
            _, _, _, by1 = d.textbbox((0, 0), "AM", font=below_f)
            if t.MARGIN is None:  # midway between digits and card bottom
                line_y = (time_y + dy1 + h - (by1 - by0)) / 2 - by0
            else:  # its text on the bottom margin
                line_y = h - t.PAD - by1
            sun_times = self._sun_times() if self.options.get("sun", False) else None
            if sun_times:
                # spread under the widest time ("12:59"), so the spacing never changes when the
                # hour drops to one digit
                self._draw_sun_line(d, time_right - d.textlength("12:59", font=time_f), time_right,
                                    t.px(line_y), below_f, now.strftime("%p"), sun_times)
            else:
                t.text_right(d, time_right, line_y, now.strftime("%p"), below_f, t.MUTED)
        elif raised:
            _, ay0, _, _ = d.textbbox((0, 0), "AM", font=raised_f)
            d.text((time_right + 6, time_y + dy0 - ay0), now.strftime("%p"), font=raised_f, fill=t.MUTED)
        elif self.options.get("ampm", True) and self.options.get("ampm_style", "watermark") == "side":
            # plain AM/PM just left of the digits, top-aligned with them (for wide boxes where a
            # height-limited watermark would hide behind digits of the same size)
            hm_w = d.textlength(now.strftime(self.options.get("format", "%-I:%M")), font=time_f)
            af = t.font(t.px(t.size_of(time_f) * 0.3), "bold")
            _, ay0, _, _ = d.textbbox((0, 0), "AM", font=af)
            t.text_right(d, time_right - hm_w - 12, time_y + dy0 - ay0, now.strftime("%p"), af, t.MUTED)
        elif self.options.get("ampm", True):
            ampm = now.strftime("%p")
            ref = t.font(100, "bold")
            _, ry0, _, ry1 = d.textbbox((0, 0), ampm, font=ref)
            by_width = 2 * time_f.getlength("0") / (ref.getlength("M") / 100)
            by_height = (h - 16) / ((ry1 - ry0) / 100)
            pm_f = t.font(int(min(by_width, by_height)), "bold")
            _, py0, _, py1 = d.textbbox((0, 0), ampm, font=pm_f)
            t.text_right(d, time_right, h / 2 - (py0 + py1) / 2, ampm, pm_f, faint)
        t.text_right(d, time_right, time_y, now.strftime(self.options.get("format", "%-I:%M")), time_f, t.TEXT)

    def _sun_times(self) -> tuple[float, float] | None:
        """(next sunrise, next sunset) at the weather location, recomputed at most once a
        minute. Before dawn both are today's; by day the sunrise is tomorrow's."""
        now = self.clock.now.timestamp()
        if getattr(self, "_sun_cache", (None,))[0] == now // 60:
            return self._sun_cache[1]
        loc = self.weather.location
        if loc is None:
            return None
        found = sun.events(loc[0], loc[1], now, now + 36 * 3600)
        rise = next((ts for ts, up in found if up), None)
        fall = next((ts for ts, up in found if not up), None)
        self._sun_cache = (now // 60, (rise, fall) if rise and fall else None)
        return self._sun_cache[1]

    def _draw_sun_line(self, d, left, right, y, f, ampm, times):
        """The line under the time: AM/PM, then sunrise and sunset with sun-up and sun-down
        icons and 12-hour times ("6:42", "6:46"; which is am/pm is obvious). Spread across
        the digits' width: AM/PM under their left edge, sunset ending at their right, sunrise
        centred between; if the digits are too narrow, packed to the right."""
        _, y0, _, y1 = d.textbbox((0, y), "0", font=f)
        box = t.px(t.size_of(f) * 1.05)
        suns = [(icon, datetime.fromtimestamp(ts).strftime("%-I:%M"))
                for icon, ts in (("\U000F059C", times[0]), ("\U000F059B", times[1]))]
        widths = [d.textlength(ampm, font=f)] + [box + 5 + d.textlength(text, font=f) for _, text in suns]
        gap = max(14, (right - left - sum(widths)) / 2)
        x = right - sum(widths) - 2 * gap
        d.text((x, y), ampm, font=f, fill=t.MUTED)
        x += widths[0] + gap
        for (icon, text), width in zip(suns, widths[1:]):
            t.glyph_icon(d, x, (y0 + y1) / 2, icon, box, t.ORANGE)
            d.text((x + box + 5, y), text, font=f, fill=t.MUTED)
            x += width + gap

    def _draw_row_weather(self, d, left, w, h):
        """Current temperature and conditions, hi/lo/humidity, optional hourly list."""
        snap = self.weather.snapshot
        if snap is None:
            d.text((left, h / 2 - 12), self.weather.error or "loading weather…", font=t.font(18), fill=t.MUTED)
            return

        # hourly list on the far right: "11am [icon] 72°" rows
        # forecast = false: time + current conditions only (the list lives in a forecast widget)
        hours = snap.hours[:int(self.options.get("hours", 5))] if self.options.get("forecast", True) else ()
        lf, tf, gf = t.font(16, "bold"), t.font(18, "bold"), t.font(20)
        list_right = w - t.PAD
        list_left = (list_right - d.textlength("12am", font=lf) - 30 - d.textlength("100°", font=tf)) if hours else list_right + 16
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
        # hilo_style = "below": one small line under the temperature, right-aligned with it
        # (like the time card's AM/PM), leaving the height left of the digits to the icon
        hilo_below = self.options.get("hilo", True) and self.options.get("hilo_style") == "below"
        if hilo_below:
            temp_right = hilo_right
        elif self.options.get("hilo", True):  # hilo = false: the forecast widget shows them
            hstep = (h - 20) / 3
            t.text_right(d, hilo_right, 8, f"↑{snap.high:.0f}°", hf, t.RED)
            t.text_right(d, hilo_right, 8 + hstep, f"↓{snap.low:.0f}°", hf, t.CYAN)
            t.text_right(d, hilo_right, 8 + 2 * hstep, f"\U000F058E{t.pct_text(snap.humidity)}", hf, t.MUTED)
            temp_right = hilo_right - d.textlength("↑100°", font=hf) - 14
        else:
            temp_right = hilo_right

        # Current temperature and condition fit between the left edge and hi/lo.
        temp = f"{snap.temperature:.0f}°"
        icon = glyph(snap.code, snap.is_day)
        icon_left = self.options.get("icon_left", False)
        match = None
        if self.options.get("align") == "time" and self._time_partner is not None:
            partner, partner_w, partner_h = self._time_partner
            _, _, _, time_f, time_y, _, digit_bottom = partner._row_time_geometry(d, partner_w - t.PAD, partner_h)
            match = t.size_of(time_f), time_y + digit_bottom
        if icon_left and hilo_below:
            # the temperature keeps its size (a third digit narrows the icon instead)
            temp_f = t.font(match[0] if match else int(self.options.get("temp_size", 96)), "bold")
        elif icon_left:
            # align = "time": the time card's digit size, if it fits (3-digit temps shrink)
            size = match[0] if match else int(self.options.get("temp_size", 96))
            while True:
                temp_f = t.font(size, "bold")
                icon_f = t.font(max(12, t.px(size * 0.58)))
                ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
                if d.textlength(temp, font=temp_f) + (ix1 - ix0) + 8 <= temp_right - left or size <= 12:
                    break
                size -= 1
        else:
            temp_f = t.fit_font(d, temp, int(self.options.get("temp_size", 96)), temp_right - left)
        x0, y0, x1, y1 = d.textbbox((0, 0), temp, font=temp_f)
        tx, ty = temp_right - d.textlength(temp, font=temp_f), h / 2 - (y0 + y1) / 2
        if match:  # digits sit on the same bottom line as the time's
            ty = match[1] - y1
        if hilo_below:
            # the line's ink starts 8 px under the digits; beside the time (`match`), midway
            # between the digits and the card bottom, as the AM/PM line is
            below_f = t.font(int(self.options.get("hilo_size", 18)), "bold")
            _, by0, _, by1 = d.textbbox((0, 0), "AM", font=below_f)
            if match and t.MARGIN is not None:  # on the bottom margin, level with the AM/PM line
                line_y = h - t.PAD - by1
            else:
                line_y = ((ty + y1 + h - (by1 - by0)) / 2 if match else ty + y1 + 8) - by0
            # no arrows or drop: red high, cyan low and muted humidity say which is which
            fields = [(f"{snap.high:.0f}°", t.RED), (f"{snap.low:.0f}°", t.CYAN),
                      (t.pct_text(snap.humidity), t.MUTED)]
            t.fields_right(d, temp_right, line_y, fields, below_f, gap=" ")
            line_left = temp_right - d.textlength(" ".join(text for text, _ in fields), font=below_f)
        if icon_left and hilo_below:
            # as large as the card's height allows, centred in the space left of the digits
            # (and clear of the hi/lo line under them)
            limit = min(tx, line_left) - 10
            box = max(12, min(t.px(h - (20 if t.MARGIN is None else 2 * t.PAD)), t.px(limit - left)))
            t.glyph_icon(d, (left + limit) / 2, h / 2, icon, box,
                         t.ORANGE if snap.is_day else t.MAGENTA, centre_x=True)
        elif icon_left:
            d.text((tx - ix1 - 8, ty + (y0 + y1) / 2 - (iy0 + iy1) / 2), icon, font=icon_f,
                   fill=t.ORANGE if snap.is_day else t.MAGENTA)
        else:
            icon_f = t.font(t.px(t.size_of(temp_f) * 4 / 3))
            ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=icon_f)
            cx, cy = tx + (x0 + x1) / 2, ty + (y0 + y1) / 2
            d.text((cx - (ix0 + ix1) / 2, cy - (iy0 + iy1) / 2), icon, font=icon_f,
                   fill=t.blend(t.CARD, t.ORANGE, 0.2))
        d.text((tx, ty), temp, font=temp_f, fill=t.TEXT)
        if self.options.get("stale") == "icon" and self.weather.freshness_text(snap):
            t.stale_mark(d, left, 6, 22)
