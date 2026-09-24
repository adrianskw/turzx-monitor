from types import SimpleNamespace

import psutil

from turzx import theme as t
from turzx.widget import Widget, register


@register("memory")
class Memory(Widget):
    """System RAM usage."""

    interval = 2.0

    def __init__(self, **options):
        super().__init__(**options)
        self._avg = t.Rolling(t.smooth_samples(options, self.interval))

    def update(self):
        ram = psutil.virtual_memory()
        # displayed percent is a rolling mean (`smooth` seconds)
        self.ram = SimpleNamespace(percent=self._avg.add(ram.percent), used=ram.used, total=ram.total)

    def draw(self, d, w, h):
        t.card(d, w, h)
        pct = self.ram.percent
        color = t.role(self.options.get("color", "cyan"))
        if self.options.get("style") == "dense":
            d.text((t.PAD, 7), self.options.get("label", "\U000F035B"), font=t.font(28), fill=t.MUTED)
            t.text_right(d, w - t.PAD, 8, f"{pct:.0f}%", t.font(27, "bold"), color)
            t.text_right(d, w - t.PAD, h - 32,
                         f"{t.human_bytes(self.ram.used)}/{t.human_bytes(self.ram.total)}",
                         t.font(13, "bold"), t.MUTED)
            t.bar(d, t.PAD, h - 14, w - 2 * t.PAD, 7, pct, color)
            return
        if self.options.get("style") == "vertical":  # narrow card: icon on top, bar filling bottom-up
            f = t.font(int(self.options.get("size", 26)))
            label = self.options.get("label", "\U000F061A")
            x0, y0, x1, y1 = d.textbbox((0, 0), label, font=f)
            d.text((w / 2 - (x0 + x1) / 2, 10 - y0), label, font=f, fill=t.MUTED)
            bw = int(self.options.get("bar_width", 14))
            top = 10 + (y1 - y0) + 10
            t.vbar(d, (w - bw) / 2, top, bw, h - 10 - top, pct, color)
            return
        if self.options.get("style") == "card":  # no graph: headline, then used/total and bar at the bottom
            t.stat_line(d, w, 6, self.options.get("label", "RAM"), [(t.pct_text(pct), color)],
                        size=int(self.options.get("size", 30)))
            t.usage_bar(d, w, h, pct, self.ram.used, self.ram.total, color, self.options.get("mem_numbers", True))
            return
        if self.options.get("style") == "strip":  # one line: label, %, bar, used/total
            f = t.font(int(self.options.get("size", 26)), "bold")
            df = t.font(int(self.options.get("detail_size", 18)), "bold")
            label = self.options.get("label", "RAM")
            _, y0, _, y1 = d.textbbox((0, 0), "99%", font=f)
            ty = h / 2 - (y0 + y1) / 2
            d.text((t.PAD, ty), label, font=f, fill=t.MUTED)
            pct_right = t.PAD + d.textlength(label + " 99%", font=f)
            t.text_right(d, pct_right, ty, t.pct_text(pct), f, color)
            detail = f"{t.human_bytes(self.ram.used)}/{t.human_bytes(self.ram.total)}"
            _, dy0, _, dy1 = d.textbbox((0, 0), detail, font=df)
            t.text_right(d, w - t.PAD, h / 2 - (dy0 + dy1) / 2, detail, df, t.TEXT)
            bar_right = w - t.PAD - d.textlength(f"99.9G/{t.human_bytes(self.ram.total)}", font=df) - 12
            t.bar(d, pct_right + 14, h / 2 - 7, bar_right - pct_right - 14, 14, pct, color)
            return
        t.stat_line(d, w, 6, self.options.get("label", "RAM"), [(t.pct_text(pct), color)],
                    size=self.options.get("size", 30))
        # used/total in the gap under the headline; bar gets the full width
        t.text_right(d, w - t.PAD, 40, f"{t.human_bytes(self.ram.used)}/{t.human_bytes(self.ram.total)}",
                     t.font(int(self.options.get("detail_size", 18)), "bold"), t.TEXT)
        t.bar(d, t.PAD, h - 24, w - 2 * t.PAD, 14, pct, color)
