import math
import time
from collections import deque
from pathlib import Path

import psutil

from turzx import theme as t
from turzx.widget import Widget, register


def _core_percent(before, after) -> float | None:
    """CPU usage between two counter snapshots, independent of worker thread."""
    def total(times):
        # Linux includes guest time in user/nice, so exclude the duplicate.
        return sum(times) - getattr(times, "guest", 0) - getattr(times, "guest_nice", 0)

    elapsed = total(after) - total(before)
    idle = (after.idle + getattr(after, "iowait", 0)
            - before.idle - getattr(before, "iowait", 0))
    if elapsed <= 0 or idle < 0:
        return None
    return max(0.0, min(100.0, 100 * (elapsed - idle) / elapsed))


@register("cpu")
class Cpu(Widget):
    interval = 1.0

    def __init__(self, **options):
        super().__init__(**options)
        if options.get("ram_bar") or options.get("ram_pct"):
            raise ValueError("CPU RAM options were removed; use a memory widget")
        try:
            self._core_step = float(options.get("core_step", 5))
        except (TypeError, ValueError) as exc:
            raise ValueError("core_step must be between 0 and 100") from exc
        if isinstance(options.get("core_step"), bool) or not math.isfinite(self._core_step) or not 0 < self._core_step <= 100:
            raise ValueError("core_step must be between 0 and 100")
        self.history = deque([0.0] * 60, maxlen=60)
        self.cores: list[float] = []
        self.total = 0.0
        self.temp = None
        self.power = None
        self._energy = None
        # displayed numbers are rolling means over `smooth` seconds
        n = t.smooth_samples(options, self.interval)
        self._avg = {k: t.Rolling(n) for k in ("total", "temp", "power")}
        self._core_avg: list[t.Rolling] = []
        self._n = n
        # the graph is smoothed too (`graph_smooth` seconds, 0 = raw samples)
        self._graph_avg = t.Rolling(t.smooth_samples({"smooth": options.get("graph_smooth", 2)}, self.interval))
        self._cpu_times = None if options.get("_sample", False) else psutil.cpu_times(percpu=True)

    def _read_power(self):
        """Package power from RAPL energy counter deltas (works for AMD and Intel)."""
        rapl = Path("/sys/class/powercap/intel-rapl:0")
        try:
            uj = int((rapl / "energy_uj").read_text())
        except (OSError, ValueError):
            return None
        now = time.monotonic()
        power = None
        if self._energy is not None:
            t0, e0 = self._energy
            if uj >= e0 and now > t0:  # skip counter wraparound and repeated timestamps
                power = (uj - e0) / 1e6 / (now - t0)
        self._energy = (now, uj)
        return power

    def update(self):
        current = psutil.cpu_times(percpu=True)
        previous, self._cpu_times = self._cpu_times, current
        if previous is not None and len(previous) == len(current) and current:
            raw = [_core_percent(before, after) for before, after in zip(previous, current)]
            if all(value is not None for value in raw):
                if len(self._core_avg) != len(raw):
                    self._core_avg = [t.Rolling(self._n) for _ in raw]
                self.cores = [avg.add(v) for avg, v in zip(self._core_avg, raw)]
                total = sum(raw) / len(raw)
                self.history.append(self._graph_avg.add(total))
                self.total = self._avg["total"].add(total)
        sensors = psutil.sensors_temperatures()
        temps = sensors.get("k10temp") or sensors.get("coretemp") or []
        temp = next((s.current for s in temps if s.label in ("Tctl", "Package id 0")), None)
        self.temp = None if temp is None else self._avg["temp"].add(temp)
        if self.options.get("power", True):
            power = self._read_power()
            self.power = self._avg["power"].add(power) if power is not None else None
        else:
            self.power = None

    @staticmethod
    def _visible_cores(cores: list[float], limit: int) -> list[float]:
        """Group only when needed; preserve isolated hot cores with a group maximum."""
        if len(cores) <= limit:
            return cores
        return [max(cores[i * len(cores) // limit:(i + 1) * len(cores) // limit])
                for i in range(limit)]

    def draw(self, d, w, h):
        t.card(d, w, h)
        o = self.options
        color = t.role(o.get("color", "accent"))
        if o.get("style") == "dense":
            self._draw_dense(d, w, h, color)
            return
        strip = h - 24  # per-core bars along the bottom
        # history graph sits faintly behind the headline
        t.sparkline(d, t.PAD, 6, w - 2 * t.PAD, strip - 12, self.history, color=color, dim=True)
        fields = [(t.pct_text(self.total), color)]
        if self.temp is not None:
            fields.append((t.temp_text(self.temp, o.get("temp_unit", "°C")), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 85))))
        if self.power is not None and o.get("power", True):
            fields.append((t.watts_text(self.power, int(o.get("watt_digits", 2))), t.threshold_color(self.power, o.get("power_warn", 60), o.get("power_crit", 80))))
        t.stat_line(d, w, 6, o.get("label", "CPU"), fields, size=o.get("size", 30))
        # Every bar the same whole number of pixels, a multiple of the step count so each
        # `core_step` % (5) fills the same number of pixels (1 px per 5 % on a 20 px bar);
        # leftover space goes evenly into the gaps (at least 2 px), the rest to the two ends.
        span = w - 2 * t.PAD
        # Reserve at least two pixels per bar so moderate load remains visible.
        cores = self._visible_cores(self.cores, max(1, span // 4))
        step = self._core_step
        n, steps, min_gap = len(cores) or 1, round(100 / step), 2
        available = max(1, (span - min_gap * (n - 1)) // n)
        bw = available // steps * steps or available
        gap = (span - bw * n) // (n - 1) if n > 1 else 0
        x = t.PAD + (span - bw * n - gap * (n - 1)) // 2
        for i, pct in enumerate(cores):
            t.bar(d, x + i * (bw + gap), strip, bw, 14, pct, color, step=step)

    def _draw_dense(self, d, w, h, color):
        o = self.options
        d.text((t.PAD, 7), o.get("label", "\U000F0EE0"), font=t.font(28), fill=t.MUTED)
        temp = f"{self.temp:.0f}{o.get('temp_unit', '°')}" if self.temp is not None else "—"
        fields = [
            (f"{self.total:.0f}%", color),
            (temp, t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 85))),
        ]
        if o.get("power", True):
            watts = f"{self.power:.0f}W" if self.power is not None else "—"
            fields.append((watts, t.threshold_color(self.power, o.get("power_warn", 60), o.get("power_crit", 80))))
        t.fields_right(d, w - t.PAD, 8, fields, t.font(27, "bold"), gap=" ")
        t.sparkline(d, t.PAD, h - 30, w - 2 * t.PAD, 9, self.history.copy(), color=color, dim=True)
        # Keep each core bar positive even on machines with many logical cores.
        limit = max(1, (w - 2 * t.PAD) // 7)
        cores = self._visible_cores(self.cores, limit)
        if cores:
            slot = (w - 2 * t.PAD) / len(cores)
            for i, pct in enumerate(cores):
                t.bar(d, t.PAD + i * slot, h - 14, slot - 3, 7, pct, color, step=10)
