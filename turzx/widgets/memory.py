import psutil

from turzx import theme as t
from turzx.widget import Widget, register


@register("memory")
class Memory(Widget):
    """System RAM usage."""

    interval = 2.0

    def update(self):
        self.ram = psutil.virtual_memory()

    def draw(self, d, w, h):
        t.card(d, w, h)
        pct = self.ram.percent
        color = t.role(self.options.get("color", "cyan"))
        t.stat_line(d, w, 6, "RAM", [(t.pct_text(pct), color)])
        strip = h - 24
        f = t.font(18, "bold")
        used = t.human_bytes(self.ram.used)
        t.text_right(d, w - t.PAD, strip - 4, used, f, t.TEXT)
        t.bar(d, t.PAD, strip, w - 2 * t.PAD - d.textlength("99.9G", font=f) - 8, 14, pct, color)
