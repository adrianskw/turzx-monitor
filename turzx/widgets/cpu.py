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
        # ram = "column": icon over a vertical RAM bar at the card's right edge;
        # ram = "bar": a full-width RAM bar under the core bars;
        # ram = "inline": a RAM bar in the headline, between the icon and the readings, with
        # the graph under the headline instead of behind it. Unset: no RAM here.
        self.ram_mode = options.get("ram")
        if self.ram_mode not in (None, "column", "bar", "inline"):
            raise ValueError('ram must be "column", "bar" or "inline"')
        self.ram_pct = None
        self._ram_avg = t.Rolling(n)

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
        if self.ram_mode:
            self.ram_pct = self._ram_avg.add(psutil.virtual_memory().percent)
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
        ram_color = t.role(o.get("ram_color", "cyan"))
        column = 30 if self.ram_mode == "column" else 0  # RAM column width at the right edge
        right = w - column - (8 if column else 0)  # everything else ends here
        show_cores = o.get("cores", True)  # false: no per-core bars (the RAM bar, if any, takes their row)
        rows = (self.ram_mode == "bar") + show_cores  # bar rows along the bottom
        strip = h - 24 * rows if rows else h - 4  # no bars: the graph fills the card
        fields = [(t.pct_text(self.total), color)]
        if self.temp is not None:
            fields.append((t.temp_text(self.temp, o.get("temp_unit", "°C")), t.threshold_color(self.temp, o.get("temp_warn", 75), o.get("temp_crit", 85))))
        if self.power is not None and o.get("power", True):
            fields.append((t.watts_text(self.power, int(o.get("watt_digits", 2))), t.threshold_color(self.power, o.get("power_warn", 60), o.get("power_crit", 80))))
        if self.ram_mode == "inline":
            t.stat_line(d, w, 6, o.get("label", "CPU"), fields, size=o.get("size", 30))
            if self.ram_pct is not None:
                t.graph_with_bar(d, w, h, o.get("size", 30), self.history, color, self.ram_pct, ram_color, fields)
            return
        # Every bar the same whole number of pixels, a multiple of the step count so each
        # `core_step` % (5) fills the same number of pixels (1 px per 5 % on a 20 px bar);
        # leftover space goes evenly into the gaps (at least 2 px), the rest to the two ends.
        span = right - 2 * t.PAD
        # Reserve at least two pixels per bar so moderate load remains visible.
        cores = self._visible_cores(self.cores, max(1, span // 4))
        step = self._core_step
        n, steps, min_gap = len(cores) or 1, round(100 / step), 2
        available = max(1, (span - min_gap * (n - 1)) // n)
        bw = available // steps * steps or available
        gap = (span - bw * n) // (n - 1) if n > 1 else 0
        x = t.PAD + (span - bw * n - gap * (n - 1)) // 2
        if not show_cores:  # nothing to line up with: graph and RAM bar use the full width
            n, bw, gap, x = 1, span, 0, t.PAD
        # history graph faintly behind the headline, spanning exactly the core bars' extent
        # (the bars' leftover pixels would otherwise leave its edges 1-2 px past theirs),
        # then the headline over it
        t.sparkline(d, x, 6, bw * n + gap * (n - 1), strip - 12, self.history, color=color, dim=True)
        t.stat_line(d, right, 6, o.get("label", "CPU"), fields, size=o.get("size", 30))
        for i, pct in enumerate(cores if show_cores else ()):
            t.bar(d, x + i * (bw + gap), strip, bw, 14, pct, color, step=step)
        if self.ram_pct is None:
            return
        if column:  # icon level with the headline, bar down to the core bars' bottom edge
            cx, cy = w - t.PAD - column / 2 + 6, t.headline_centre(6, o.get("size", 30))
            t.glyph_icon(d, cx, cy, o.get("ram_label", "\U000F061A"), min(t.ICON_BOX, column - 4), centre_x=True)
            top = round(cy + t.ICON_BOX / 2 + 8)
            t.vbar(d, round(cx - 7), top, 14, strip + 14 - top, self.ram_pct, ram_color)
        else:  # under the core bars, spanning exactly their extent
            t.bar(d, x, h - 24, bw * n + gap * (n - 1), 14, self.ram_pct, ram_color)

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
