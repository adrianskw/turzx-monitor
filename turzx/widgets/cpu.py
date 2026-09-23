import re
import time
from collections import deque
from pathlib import Path

import psutil

from turzx import theme as t
from turzx.widget import Widget, register


@register("cpu")
class Cpu(Widget):
    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        self.history = deque([0.0] * 60, maxlen=60)
        self.cores: list[float] = []
        self.total = 0.0
        self.temp = None
        self.power = None
        self._energy = None
        self.name = options.get("name") or self._model_name()
        psutil.cpu_percent(percpu=True)  # prime the counters

    @staticmethod
    def _model_name() -> str:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                name = line.split(":", 1)[1]
                name = re.sub(r"\(R\)|\(TM\)|CPU|Processor|\d+-Core|@.*|AMD|Intel", "", name)
                return " ".join(name.split()).upper()
        return "CPU"

    def _read_power(self):
        """Package power from RAPL energy counter deltas (works for AMD and Intel)."""
        rapl = Path("/sys/class/powercap/intel-rapl:0")
        try:
            uj = int((rapl / "energy_uj").read_text())
        except OSError:
            return
        now = time.monotonic()
        if self._energy:
            t0, e0 = self._energy
            if uj >= e0:  # skip counter wraparound
                self.power = (uj - e0) / 1e6 / (now - t0)
        self._energy = (now, uj)

    def update(self):
        self.cores = psutil.cpu_percent(percpu=True)
        self.total = sum(self.cores) / len(self.cores)
        self.history.append(self.total)
        temps = psutil.sensors_temperatures().get("k10temp") or psutil.sensors_temperatures().get("coretemp") or []
        self.temp = next((s.current for s in temps if s.label in ("Tctl", "Package id 0")), None)
        self._read_power()

    def draw(self, d, w, h):
        y = t.card(d, w, h, self.name)
        o = self.options
        fields = []
        if self.temp is not None:
            fields.append((t.temp_text(self.temp), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 85))))
        if self.power is not None:
            fields.append((t.watts_text(self.power, 2), t.threshold_color(self.power, o.get("power_warn", 60), o.get("power_crit", 80))))
        t.fields_right(d, w - t.PAD, 11, fields, t.font(18, "bold"))
        strip = h - 26  # per-core bars along the bottom
        t.sparkline(d, t.PAD, y, w - 2 * t.PAD, strip - y - 8, self.history, dim=True)
        t.big_pct(d, t.PAD, y + 2, self.total)
        n = len(self.cores) or 1
        slot = (w - 2 * t.PAD + 4) / n
        for i, pct in enumerate(self.cores):
            t.bar(d, t.PAD + i * slot, strip, slot - 4, 14, pct, step=10)
