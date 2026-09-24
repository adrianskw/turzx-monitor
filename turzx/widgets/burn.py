"""Token burn in one line: rolling tokens/min and today's running total (from turzx.agentlog)."""

import math

from turzx import agentlog, theme as t
from turzx.widget import Widget, register

FIRE = "\U000F0238"
SIGMA = "\U000F04A0"


@register("burn")
class Burn(Widget):
    interval = 5.0

    def __init__(self, **options):
        super().__init__(**options)
        self.rate_minutes = float(options.get("rate_minutes", 5))
        if not math.isfinite(self.rate_minutes) or not 0 < self.rate_minutes <= agentlog.MAX_RATE_MINUTES:
            raise ValueError(f"rate_minutes must be between 0 and {agentlog.MAX_RATE_MINUTES}")
        self.rate = 0.0
        self.total_today = 0
        self.cache_hit: float | None = None

    def update(self):
        log = agentlog.shared()
        log.refresh()
        self.rate = log.rate(self.rate_minutes)  # rolling average over rate_minutes
        self.total_today = log.total_today()  # running sum since midnight
        self.cache_hit = log.cache_hit(self.rate_minutes)  # rolling, same window as the rate

    def draw(self, d, w, h):
        t.card(d, w, h)
        big, icon_f, unit_f = t.font(28, "bold"), t.font(26), t.font(16, "bold")
        y = (h - 34) / 2
        # values right-aligned to fixed 5-char fields ("99.9K" / "99.9M") so neighbours never shift
        num_w = d.textlength("99.9K", font=big)
        x = t.PAD
        d.text((x, y + 2), FIRE, font=icon_f, fill=t.ORANGE)
        t.text_right(d, x + 30 + num_w, y, t.human_count(self.rate), big, t.TEXT)
        unit_end = x + 36 + num_w + d.textlength("/min", font=unit_f)
        d.text((x + 36 + num_w, y + 12), "/min", font=unit_f, fill=t.MUTED)
        right = w - t.PAD
        if self.options.get("cache_ring", False):  # rolling cache hit rate, between rate and total
            sigma_x = right - num_w - 32
            t.ring(d, (unit_end + sigma_x) / 2, h / 2, 12, 4, self.cache_hit, t.cache_color(self.cache_hit))
        t.text_right(d, right, y, t.human_count(self.total_today), big, t.TEXT)
        d.text((right - num_w - 32, y + 2), SIGMA, font=icon_f, fill=t.MAGENTA)
