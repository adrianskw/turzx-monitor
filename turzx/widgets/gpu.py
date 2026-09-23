from collections import deque

from turzx import theme as t
from turzx.widget import Widget, register

try:
    import pynvml
except ImportError:  # pragma: no cover
    pynvml = None


@register("gpu")
class Gpu(Widget):
    """NVIDIA GPU via NVML."""

    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        self.history = deque([0.0] * 60, maxlen=60)
        self.handle = None
        self.util = self.temp = self.power = 0.0
        self.mem_used = self.mem_total = 0
        if pynvml:
            try:
                pynvml.nvmlInit()
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(int(options.get("index", 0)))
            except pynvml.NVMLError:
                self.handle = None

    def update(self):
        if not self.handle:
            return
        h = self.handle
        self.util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        self.temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        self.power = pynvml.nvmlDeviceGetPowerUsage(h) / 1000
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        self.mem_used, self.mem_total = mem.used, mem.total
        self.history.append(self.util)

    def draw(self, d, w, h):
        t.card(d, w, h)
        o = self.options
        if not self.handle:
            t.stat_line(d, w, 6, o.get("label", "GPU"), [("n/a", t.MUTED)])
            return
        color = t.role(o.get("color", "magenta"))
        strip = h - 24  # VRAM row along the bottom
        t.sparkline(d, t.PAD, 6, w - 2 * t.PAD, strip - 12, self.history, color=color, dim=True)
        t.stat_line(d, w, 6, o.get("label", "GPU"), [
            (t.pct_text(self.util), color),
            (t.temp_text(self.temp), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 83))),
            (t.watts_text(self.power, 3), t.threshold_color(self.power, o.get("power_warn", 180), o.get("power_crit", 210))),
        ])
        vram = 100 * self.mem_used / self.mem_total if self.mem_total else 0
        f = t.font(18, "bold")
        t.text_right(d, w - t.PAD, strip - 4, f"{t.human_bytes(self.mem_used)}/{t.human_bytes(self.mem_total)}", f, t.TEXT)
        # reserve the widest possible label so the bar never changes length
        widest = f"999.9M/{t.human_bytes(self.mem_total)}"
        t.bar(d, t.PAD, strip, w - 2 * t.PAD - d.textlength(widest, font=f) - 10, 14, vram, color)
