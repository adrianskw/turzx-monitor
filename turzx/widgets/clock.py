from datetime import datetime

from turzx import theme as t
from turzx.widget import Widget, register


@register("clock")
class Clock(Widget):
    interval = 1.0  # checks every second, but only redraws when the minute changes

    def update(self):
        self.now = datetime.now()

    def draw(self, d, w, h):
        t.card(d, w, h)
        # Right-aligned so the AM/PM and date stay put when the hour gains a digit.
        right = w - t.PAD
        if self.options.get("ampm", True):
            ampm = t.font(34, "bold")
            d.text((right - d.textlength("PM", font=ampm), 18), self.now.strftime("%p"), font=ampm, fill=t.MUTED)
            right -= d.textlength("PM", font=ampm) + 6
        hm = self.now.strftime(self.options.get("format", "%-I:%M"))
        t.text_right(d, right, -14, hm, t.font(106, "bold"), t.TEXT)
        t.text_right(d, w - t.PAD, h - 46, self.now.strftime("%a, %b %-d"), t.font(32), t.MUTED)
