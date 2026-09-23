import time
from collections import deque

import psutil

from turzx import theme as t
from turzx.widget import Widget, register


@register("network")
class Network(Widget):
    """Throughput for one interface (or all non-loopback ones)."""

    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        self.iface = options.get("interface")
        self.down = deque([0.0] * 40, maxlen=40)
        self.up = deque([0.0] * 40, maxlen=40)
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

    def draw(self, d, w, h):
        y = t.card(d, w, h, "NET")
        f = t.font(30, "bold")
        d.text((t.PAD, y), "↓", font=f, fill=t.CYAN)
        t.text_right(d, w - t.PAD, y, t.human_bytes(self.down[-1], "/s"), f, t.TEXT)
        d.text((t.PAD, y + 40), "↑", font=f, fill=t.MAGENTA)
        t.text_right(d, w - t.PAD, y + 40, t.human_bytes(self.up[-1], "/s"), f, t.TEXT)
        top, gh = y + 86, h - y - 96
        vmax = max(max(self.down), max(self.up), 1024)
        t.sparkline(d, t.PAD, top, w - 2 * t.PAD, gh, self.down, vmax=vmax, color=t.CYAN)
        t.sparkline(d, t.PAD, top, w - 2 * t.PAD, gh, self.up, vmax=vmax, color=t.MAGENTA)
