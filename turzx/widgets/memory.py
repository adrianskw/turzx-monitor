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
        t.stat_line(d, w, 6, self.options.get("label", "RAM"), [(t.pct_text(pct), color)],
                    size=self.options.get("size", 30))
        # used/total in the gap under the headline; bar gets the full width
        t.text_right(d, w - t.PAD, 40, f"{t.human_bytes(self.ram.used)}/{t.human_bytes(self.ram.total)}",
                     t.font(18, "bold"), t.TEXT)
        t.bar(d, t.PAD, h - 24, w - 2 * t.PAD, 14, pct, color)
