"""Hourly forecast as a list that wraps into columns: "11am [icon] 72°" rows."""

from turzx import theme as t
from turzx.widget import Widget, register
from turzx.widgets.weather import Weather, glyph


@register("forecast")
class Forecast(Widget):
    """Takes the weather options (latitude, longitude, units) plus `hours` (12) and
    `columns` (2). Shares the Open-Meteo fetch with any other weather widget."""

    interval = 600.0

    def __init__(self, **options):
        super().__init__(**options)
        self.weather = Weather(**options)
        self.hours = int(options.get("hours", 12))
        self.columns = int(options.get("columns", 2))
        if self.hours <= 0 or self.columns <= 0:
            raise ValueError("hours and columns must be positive")

    def next_delay(self):
        return self.weather.next_delay()

    def update(self):
        self.weather.update()

    def draw(self, d, w, h):
        y = t.card(d, w, h)
        snap = self.weather.snapshot
        if snap is None:
            d.text((t.PAD, y), self.weather.error or "loading…", font=t.font(16), fill=t.MUTED)
            return
        if self.options.get("now", False):
            y = self._draw_now(d, w, y, snap)
        if self.options.get("summary", False):  # today's high / low / humidity as a header row
            sf = t.font(20, "bold")
            t.fields_right(d, w - t.PAD, y - 2, [
                (f"↑{snap.high:.0f}°", t.RED), (f"↓{snap.low:.0f}°", t.CYAN),
                (f"\U000F058E{t.pct_text(snap.humidity)}", t.MUTED)], sf, gap=" ")
            y += 30
        hours = snap.hours[:self.hours]
        if not hours:
            d.text((t.PAD, y + 8), "forecast unavailable", font=t.font(16), fill=t.MUTED)
            return
        per_col = -(-len(hours) // self.columns)  # ceil
        col_w = (w - 2 * t.PAD) / self.columns
        row_h = (h - y - 10) / per_col
        deg = "" if any(len(f"{hr[3]:.0f}") > 2 for hr in hours) else "°"
        lf, tf, gf = self._fonts(d, row_h, col_w - 12 * (self.columns > 1), deg)
        for i, (when, code, is_day, temp) in enumerate(hours):
            c, r = divmod(i, per_col)
            x0, x1 = t.PAD + c * col_w, t.PAD + (c + 1) * col_w - (12 if c < self.columns - 1 else 0)
            mid = y + r * row_h + row_h / 2  # every glyph is centred on the row's middle
            label_right = x0 + d.textlength("12am", font=lf)
            t.text_right(d, label_right, mid - self._half(d, lf), when.strftime("%-I%p").lower(), lf, t.MUTED)
            d.text((label_right + (8 if self.options.get("fill") else 4), mid - self._half(d, gf, glyph(code, is_day))), glyph(code, is_day),
                   font=gf, fill=t.ORANGE if is_day else t.MAGENTA)
            t.text_right(d, x1, mid - self._half(d, tf), f"{temp:.0f}{deg}", tf, t.TEXT)
        if self.options.get("stale") == "icon" and self.weather.freshness_text(snap):
            t.stale_mark(d, t.PAD - 4, 4, 18)  # top-left, clear of the summary row

    @staticmethod
    def _half(d, f, text="0") -> float:
        """y offset from a text origin to the middle of its ink (digits by default)."""
        _, y0, _, y1 = d.textbbox((0, 0), text, font=f)
        return (y0 + y1) / 2

    def _fonts(self, d, row_h: float, col_w: float, deg: str):
        """(label, temperature, icon) fonts. Fixed sizes by default; with `fill`, as large as
        the row height allows, shrunk until "12am [icon] 99°" fits the column width
        (2 digits + ° or 3 digits bare, as drawn)."""
        if not self.options.get("fill", False):
            return t.font(15, "bold"), t.font(18, "bold"), t.font(19)
        size = row_h * 0.78
        while True:
            lf, tf, gf = t.font(t.px(size * 0.78), "bold"), t.font(t.px(size), "bold"), t.font(t.px(size * 1.05))
            need = (d.textlength("12am", font=lf) + 8 + d.textlength("\U000F0F31", font=gf) + 12
                    + d.textlength(f"99{deg}" if deg else "107", font=tf))
            if need <= col_w or size <= 12:
                return lf, tf, gf
            size -= 1

    def _draw_now(self, d, w, y, snap) -> float:
        """Current temperature (faint icon behind) and today's hi/lo/humidity, stacked above
        the hours, so a separate current-weather box isn't needed. Returns the next free y."""
        temp = f"{snap.temperature:.0f}°"
        tf = t.fit_font(d, temp, int(self.options.get("now_size", 48)), w - 2 * t.PAD)
        x0, y0, x1, y1 = d.textbbox((0, 0), temp, font=tf)
        tx, ty = w - t.PAD - d.textlength(temp, font=tf), y - y0 + 2
        icon = glyph(snap.code, snap.is_day)
        gf = t.font(t.px(t.size_of(tf) * 4 / 3))
        ix0, iy0, ix1, iy1 = d.textbbox((0, 0), icon, font=gf)
        cx, cy = tx + (x0 + x1) / 2, ty + (y0 + y1) / 2
        d.text((cx - (ix0 + ix1) / 2, cy - (iy0 + iy1) / 2), icon, font=gf, fill=t.blend(t.CARD, t.ORANGE, 0.25))
        d.text((tx, ty), temp, font=tf, fill=t.TEXT)
        if self.weather.freshness_text(snap):
            t.stale_mark(d, t.PAD - 4, y, 18)
        y = ty + y1 + 8
        sf = t.font(16, "bold")
        t.fields_right(d, w - t.PAD, y, [
            (f"↑{snap.high:.0f}°", t.RED), (f"↓{snap.low:.0f}°", t.CYAN),
            (f"\U000F058E{t.pct_text(snap.humidity)}", t.MUTED)], sf, gap=" ")
        return y + 30
