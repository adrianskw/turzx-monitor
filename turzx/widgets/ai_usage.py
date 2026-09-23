import json
import re
import subprocess
import time

from turzx import theme as t
from turzx.widget import Widget, register

# (label, command, brand color) — same CLIs the Omarchy bar widgets use.
PROVIDERS = {
    "claude": ("Claude", "claude-usage", (222, 115, 86)),
    "codex": ("Codex", "codex-usage", (116, 170, 156)),
}
FORMAT = "{5h_pct}|{7d_pct}|{5h_reset}|{7d_reset}"
WINDOWS = {"5h": 5 * 3600, "7d": 7 * 86400}
UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> float | None:
    """'4h36m' / '6d04h' / '12m' -> seconds."""
    parts = re.findall(r"(\d+)([dhms])", text)
    return sum(int(n) * UNITS[u] for n, u in parts) if parts else None


def pace_pct(reset: str, window: str, age: float) -> float | None:
    """How far through the window we are (0-100), i.e. where even usage would be."""
    remaining = parse_duration(reset)
    if remaining is None:
        return None
    total = WINDOWS[window]
    return max(0.0, min(100.0, 100 * (1 - (remaining - age) / total)))


@register("ai_usage")
class AiUsage(Widget):
    """5-hour / 7-day usage limits for Claude and Codex."""

    interval = 60.0

    def __init__(self, **options):
        super().__init__(**options)
        self.providers = options.get("providers", list(PROVIDERS))
        self.data: dict[str, tuple | str] = {p: "…" for p in self.providers}
        self.fetched: dict[str, float] = {}

    def _fetch(self, cmd: str):
        out = subprocess.run([cmd, "--waybar", "--show-5h", "--format", FORMAT],
                             capture_output=True, text=True, timeout=45)
        five, seven, r5, r7 = json.loads(out.stdout)["text"].split("|")
        return int(five), int(seven), r5, r7

    def update(self):
        for p in self.providers:
            try:
                self.data[p] = self._fetch(PROVIDERS[p][1])
                self.fetched[p] = time.monotonic()
            except Exception as e:  # keep last good value visible on transient failures
                if not isinstance(self.data[p], tuple):
                    self.data[p] = f"error: {type(e).__name__}"

    def draw(self, d, w, h):
        y = t.card(d, w, h)
        f, fb = t.font(22), t.font(22, "bold")
        # fixed columns sized for the widest values ("99%", "6d23h") so bars use all the rest
        reset_w = d.textlength("6d23h", font=f)
        pct_right = w - t.PAD - reset_w - 12
        bx = 112
        bw = pct_right - d.textlength("99%", font=fb) - 10 - bx
        block = (h - 2 * y) / len(self.providers)
        for i, p in enumerate(self.providers):
            color = PROVIDERS[p][2]
            by = y + i * block
            t.icon(d, t.PAD, int(by + block / 2 - 24), p, 48)
            val = self.data[p]
            if not isinstance(val, tuple):
                d.text((76, by + block / 2 - 12), val, font=f, fill=t.MUTED)
                continue
            five, seven, r5, r7 = val
            for j, (win, pct, reset) in enumerate((("5h", five, r5), ("7d", seven, r7))):
                ry = by + block / 2 - 36 + j * 38
                d.text((74, ry), win, font=fb, fill=t.MUTED)
                t.bar(d, bx, ry + 8, bw, 14, pct, color)
                pace = pace_pct(reset, win, time.monotonic() - self.fetched.get(p, time.monotonic()))
                if pace is not None:  # tick where even usage across the window would be
                    mx = round(bx + bw * pace / 100)
                    d.rectangle((mx - 1, ry + 3, mx, ry + 26), fill=t.TEXT)
                ahead = pace is not None and pct > pace + 5
                pct_color = t.RED if pct >= 80 else t.YELLOW if ahead else t.TEXT
                t.text_right(d, pct_right, ry, t.pct_text(pct), fb, pct_color)
                t.text_right(d, w - t.PAD, ry, reset, f, t.MUTED)
