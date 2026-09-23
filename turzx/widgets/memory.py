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
        y = t.card(d, w, h, "RAM")
        pct = self.ram.percent
        color = t.role(self.options.get("color", "cyan"))
        t.big_pct(d, w - t.PAD - d.textlength("99%", font=t.font(84, "bold")), y + 2, pct, 84, color)
        t.text_right(d, w - t.PAD, y + 98, f"{t.human_bytes(self.ram.used)}/{t.human_bytes(self.ram.total)}",
                     t.font(24), t.TEXT)
        t.bar(d, t.PAD, h - 30, w - 2 * t.PAD, 16, pct, color)
