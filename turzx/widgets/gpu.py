from collections import deque
import time

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
    retry_delay = 10.0

    def __init__(self, **options):
        super().__init__(**options)
        self.history = deque([0.0] * 60, maxlen=60)
        self.handle = None
        self._index = int(options.get("index", 0))
        self._sample = bool(options.get("_sample", False))
        self._next_init = 0.0
        self._nvml_ready = False
        self.util = self.temp = self.power = 0.0
        self.mem_used = self.mem_total = 0
        n = t.smooth_samples(options, self.interval)  # rolling means for the displayed numbers
        self._avg = {k: t.Rolling(n) for k in ("util", "temp", "power")}
        self._graph_avg = t.Rolling(t.smooth_samples({"smooth": options.get("graph_smooth", 2)}, self.interval))
        self._connect()

    def _connect(self):
        if pynvml is None or self._sample or time.monotonic() < self._next_init:
            return
        try:
            if not self._nvml_ready:
                pynvml.nvmlInit()
                self._nvml_ready = True
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(self._index)
        except pynvml.NVMLError:
            self.handle = None
            self._next_init = time.monotonic() + self.retry_delay

    def update(self):
        if self.handle is None:
            self._connect()
        if self.handle is None:
            return
        h = self.handle
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            power = pynvml.nvmlDeviceGetPowerUsage(h) / 1000
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        except pynvml.NVMLError:
            self.handle = None
            self._nvml_ready = False
            self._next_init = time.monotonic() + self.retry_delay
            return
        self.history.append(self._graph_avg.add(util))  # graph: rolling mean over `graph_smooth` s
        self.util = self._avg["util"].add(util)
        self.temp = self._avg["temp"].add(temp)
        self.power = self._avg["power"].add(power)
        self.mem_used, self.mem_total = mem.used, mem.total

    def draw(self, d, w, h):
        t.card(d, w, h)
        o = self.options
        if o.get("style") == "dense":
            self._draw_dense(d, w, h)
            return
        if not self.handle:
            t.stat_line(d, w, 6, o.get("label", "GPU"), [("n/a", t.MUTED)])
            return
        color = t.role(o.get("color", "magenta"))
        strip = h - 24  # VRAM row along the bottom
        t.sparkline(d, t.PAD, 6, w - 2 * t.PAD, strip - 12, self.history, color=color, dim=True)
        t.stat_line(d, w, 6, o.get("label", "GPU"), [
            (t.pct_text(self.util), color),
            (t.temp_text(self.temp, o.get("temp_unit", "°C")), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 83))),
            (t.watts_text(self.power, 3), t.threshold_color(self.power, o.get("power_warn", 180), o.get("power_crit", 210))),
        ], size=o.get("size", 30))
        vram = 100 * self.mem_used / self.mem_total if self.mem_total else 0
        f = t.font(18, "bold")
        t.text_right(d, w - t.PAD, strip - 4, f"{t.human_bytes(self.mem_used)}/{t.human_bytes(self.mem_total)}", f, t.TEXT)
        # reserve the widest possible label so the bar never changes length
        widest = f"999.9M/{t.human_bytes(self.mem_total)}"
        t.bar(d, t.PAD, strip, w - 2 * t.PAD - d.textlength(widest, font=f) - 10, 14, vram, color)

    def _draw_dense(self, d, w, h):
        o = self.options
        d.text((t.PAD, 7), o.get("label", "\U000F0FB2"), font=t.font(28), fill=t.MUTED)
        if not self.handle:
            t.text_right(d, w - t.PAD, 8, "n/a", t.font(27, "bold"), t.MUTED)
            return
        color = t.role(o.get("color", "magenta"))
        t.fields_right(d, w - t.PAD, 8, [
            (f"{self.util:.0f}%", color),
            (f"{self.temp:.0f}{o.get('temp_unit', '°')}",
             t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 83))),
            (f"{self.power:.0f}W", t.threshold_color(self.power, o.get("power_warn", 180), o.get("power_crit", 210))),
        ], t.font(27, "bold"), gap=" ")
        vram = 100 * self.mem_used / self.mem_total if self.mem_total else 0
        label = f"{t.human_bytes(self.mem_used)}/{t.human_bytes(self.mem_total)}"
        f = t.font(12, "bold")
        bar_w = max(1, w - 2 * t.PAD - d.textlength(label, font=f) - 10)
        t.sparkline(d, t.PAD, h - 34, bar_w, 9, self.history.copy(), color=color, dim=True)
        t.text_right(d, w - t.PAD, h - 32, label, f, t.MUTED)
        t.bar(d, t.PAD, h - 14, bar_w, 7, vram, color)
