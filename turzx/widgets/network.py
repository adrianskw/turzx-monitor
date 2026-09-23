import time
from collections import deque

import psutil

from turzx import theme as t
from turzx.widget import Widget, register


@register("network")
class Network(Widget):
    """Throughput for one interface (or all physical ones).

    Rates are averaged over a few samples and never drop below the K unit, so
    idle chatter doesn't make the numbers flicker between B, K and M.
    """

    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        self.iface = options.get("interface")
        n = int(options.get("smooth", 3))
        self.down = deque([0.0], maxlen=n)
        self.up = deque([0.0], maxlen=n)
        self._last = None

    def _counters(self):
        per = psutil.net_io_counters(pernic=True)
        names = [self.iface] if self.iface else [n for n in per if n != "lo" and not n.startswith(("docker", "veth", "br-"))]
        return sum(per[n].bytes_recv for n in names if n in per), sum(per[n].bytes_sent for n in names if n in per)

    def update(self):
        now, (rx, tx) = time.monotonic(), self._counters()
        if self._last:
            t0, rx0, tx0 = self._last
            dt = max(now - t0, 1e-3)
            self.down.append((rx - rx0) / dt)
            self.up.append((tx - tx0) / dt)
        self._last = (now, rx, tx)

    def rate(self, samples) -> str:
        return t.human_bytes(sum(samples) / len(samples), "/s", min_unit="K")

    def draw(self, d, w, h):
        y = t.card(d, w, h, "NET")
        f = t.font(30, "bold")
        gap = (h - y - 2 * 36) / 3
        for i, (arrow, color, samples) in enumerate((("↓", t.CYAN, self.down), ("↑", t.MAGENTA, self.up))):
            ry = y + gap + i * (36 + gap) - 6
            d.text((t.PAD, ry), arrow, font=f, fill=color)
            t.text_right(d, w - t.PAD, ry, self.rate(samples), f, t.TEXT)
