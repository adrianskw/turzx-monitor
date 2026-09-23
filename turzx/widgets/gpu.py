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
        self.name = "GPU"
        self.util = self.temp = self.power = 0.0
        self.mem_used = self.mem_total = 0
        if pynvml:
            try:
                pynvml.nvmlInit()
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(int(options.get("index", 0)))
                self.name = pynvml.nvmlDeviceGetName(self.handle).replace("NVIDIA GeForce ", "")
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
        y = t.card(d, w, h, self.name.upper())
        if not self.handle:
            d.text((t.PAD, y), "NVML unavailable", font=t.font(t.BODY), fill=t.MUTED)
            return
        o = self.options
        t.fields_right(d, w - t.PAD, 11, [
            (t.temp_text(self.temp), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 83))),
            (t.watts_text(self.power, 3), t.threshold_color(self.power, o.get("power_warn", 180), o.get("power_crit", 210))),
        ], t.font(18, "bold"))
        strip = h - 30  # VRAM row along the bottom
        t.sparkline(d, t.PAD, y, w - 2 * t.PAD, strip - y - 4, self.history, color=t.MAGENTA, dim=True)
        t.big_pct(d, t.PAD, y + 2, self.util)
        vram = 100 * self.mem_used / self.mem_total if self.mem_total else 0
        f = t.font(18, "bold")
        d.text((t.PAD, strip - 2), "VRAM", font=f, fill=t.MUTED)
        label = f"{t.human_bytes(self.mem_used)}/{t.human_bytes(self.mem_total)}"
        t.text_right(d, w - t.PAD, strip - 2, label, f, t.TEXT)
        bx = t.PAD + 56
        # reserve the widest possible label so the bar never changes length
        widest = f"999.9M/{t.human_bytes(self.mem_total)}"
        t.bar(d, bx, strip + 4, w - t.PAD - d.textlength(widest, font=f) - 10 - bx, 12, vram, t.MAGENTA)
