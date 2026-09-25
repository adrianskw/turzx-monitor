import json
import math
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

from turzx import theme as t
from turzx.widget import Widget, register

# (label, command, brand color) — same CLIs the Omarchy bar widgets use.
PROVIDERS = {
    "claude": ("Claude", "claude-usage", (222, 115, 86)),
    "codex": ("Codex", "codex-usage", (116, 170, 156)),
    "agy": ("Antigravity", "agy", (80, 140, 245)),  # Gemini models' shared quota
}
# Seconds between fetches where a provider's CLI is slow to start (agy takes ~4 s).
FETCH_EVERY = {"agy": 300.0}
AGY_WINDOWS = {"5h": "5h", "weekly": "7d"}
FORMAT = "{5h_pct}|{7d_pct}|{5h_reset}|{7d_reset}"
WINDOWS = {"5h": 5 * 3600, "7d": 7 * 86400}
UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> float | None:
    """'4h36m' / '6d04h' / '12m' -> seconds."""
    parts = re.findall(r"(\d+)([dhms])", text)
    return sum(int(n) * UNITS[u] for n, u in parts) if parts else None


def reset_text(reset: str) -> str:
    """Normalize the CLI's countdown to at most 5 chars, never seconds: 55m, 4h55m, 6d01h."""
    secs = parse_duration(reset)
    if secs is None:  # e.g. "not started": the window hasn't begun, so there's no countdown
        return ""
    days, rem = divmod(int(secs), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours:02d}h"
    if hours >= 10:
        return f"{hours}h"  # "23h", not "23h00m": keeps the 5-char budget
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def not_started(reset: str) -> bool:
    return parse_duration(reset) is None and reset.strip().lower().startswith("not")


def pace_pct(reset: str, window: str, age: float) -> float | None:
    """How far through the window we are (0-100), i.e. where even usage would be."""
    remaining = parse_duration(reset)
    if remaining is None:
        return 0.0 if not_started(reset) else None  # window not begun yet: pace is at 0 %
    total = WINDOWS[window]
    return max(0.0, min(100.0, 100 * (1 - (remaining - age) / total)))


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hours(text, name: str) -> tuple[int, int]:
    """"9-18" -> (9, 18); the end may pass midnight ("7-25" or "7-1" both mean 7am-1am)."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", str(text))
    if not m or not (0 <= int(m[1]) <= 24 and 0 <= int(m[2]) <= 24):
        raise ValueError(f'{name} must look like "9-18"')
    start, end = int(m[1]), int(m[2])
    return start, end if end > start else end + 24


def _in(hour: float, span: tuple[int, int]) -> bool:
    start, end = span
    return start <= hour < end or start <= hour + 24 < end


class WorkWeek:
    """Where the 7-day pace tick belongs if work follows a weekly routine: each hour of the
    week gets a weight (work hours on workdays 1, other waking weekday hours
    `evening_weight`, waking weekend hours `weekend_weight`, sleep 0), and the pace is the
    share of the window's total weight already behind us."""

    def __init__(self, options: dict):
        self.work = _hours(options.get("work_hours", "9-18"), "work_hours")
        self.sleep = _hours(options.get("sleep_hours", "1-7"), "sleep_hours")
        try:
            self.evening = float(options.get("evening_weight", 0.25))
            self.weekend = float(options.get("weekend_weight", 0.15))
        except (TypeError, ValueError) as exc:
            raise ValueError("evening_weight and weekend_weight must be numbers") from exc
        if not (0 <= self.evening <= 1 and 0 <= self.weekend <= 1):
            raise ValueError("evening_weight and weekend_weight must be between 0 and 1")
        days = str(options.get("workdays", "mon-fri")).lower()
        first, _, last = days.partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError('workdays must look like "mon-fri"')
        i, j = DAYS.index(first), DAYS.index(last or first)
        self.workdays = {d % 7 for d in range(i, (j if j >= i else j + 7) + 1)}

    def weight(self, when: datetime) -> float:
        hour = when.hour + when.minute / 60
        if _in(hour, self.sleep):
            return 0.0
        # a day runs from waking to sleeping: 00:30 on Saturday is still Friday night
        day = (when - timedelta(hours=self.sleep[1] % 24)).weekday()
        if day not in self.workdays:
            return self.weekend
        return 1.0 if _in(hour, self.work) else self.evening

    def pace(self, now: float, remaining: float, window: float = WINDOWS["7d"]) -> float:
        """Percent of the window's weight elapsed at `now` (unix), given seconds to its reset."""
        end = now + remaining
        start = end - window
        done = total = 0.0
        at = start
        while at < end:  # local-time hour chunks, weighted at their midpoints (DST-safe)
            local = datetime.fromtimestamp(at)
            nxt = min(end, (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp())
            if nxt <= at:
                nxt = at + 3600
            w = self.weight(datetime.fromtimestamp((at + nxt) / 2)) * (nxt - at)
            total += w
            done += w * max(0.0, min(1.0, (now - at) / (nxt - at)))
            at = nxt
        return 100 * done / total if total else 100 * (1 - remaining / window)


@register("ai_usage")
class AiUsage(Widget):
    """5-hour / 7-day usage limits for Claude, Codex and Antigravity (Gemini)."""

    interval = 60.0

    def __init__(self, **options):
        super().__init__(**options)
        self.providers = options.get("providers", list(PROVIDERS))
        if (not isinstance(self.providers, list) or not self.providers or
                any(not isinstance(p, str) or p not in PROVIDERS for p in self.providers)):
            raise ValueError(f"providers must be a nonempty list of: {', '.join(PROVIDERS)}")
        if options.get("style") == "dense" and len(self.providers) != 1:
            raise ValueError("dense usage card requires exactly one provider")
        self.data: dict[str, tuple | str] = {p: "…" for p in self.providers}
        self.fetched: dict[str, float] = {}
        self.updated_at: dict[str, float] = {}
        self.errors: dict[str, str] = {}
        self.preview_now: float | None = None
        self.preview_elapsed: float | None = None
        # pace_7d = "workweek": the 7d tick follows a weekly routine instead of the clock
        mode = options.get("pace_7d", "linear")
        if mode not in ("linear", "workweek"):
            raise ValueError('pace_7d must be "linear" or "workweek"')
        self.workweek = WorkWeek(options) if mode == "workweek" else None
        # session_pct: estimated weekly share of one full 5-hour session. The 7d bar
        # is notched at each multiple; 0 disables the notches.
        try:
            self.session_pct = float(options.get("session_pct", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("session_pct must be a number between 0 and 100") from exc
        if not 0 <= self.session_pct <= 100 or isinstance(options.get("session_pct"), bool):
            raise ValueError("session_pct must be a number between 0 and 100")

    def _show_reset(self, reset: str, window: str) -> bool:
        """resets = "soon": the countdown only matters within 1 h (5h window) or 1 day (7d window)."""
        remaining = parse_duration(reset)
        return remaining is not None and remaining <= (3600 if window == "5h" else 86400)

    def freshness_text(self, provider: str) -> str | None:
        if provider not in self.updated_at:
            return None
        now = self.preview_now if self.preview_now is not None else time.time()
        age = max(0, now - self.updated_at[provider])
        if provider in self.errors or age >= 2 * max(self.interval, FETCH_EVERY.get(provider, 0)):
            return f"updated {t.duration_text(age)} ago"
        return None

    def _fetch_agy(self, cmd: str):
        """Antigravity's `/usage`, answered in print mode without a model turn or quota. Its
        Gemini group (Flash and Pro share one quota) has a 5-hour and a weekly bucket, each
        a remaining fraction and a reset time; the Claude/GPT group is ignored."""
        out = subprocess.run([cmd, "-p", "/usage", "--output-format", "json", "--log-file", "/dev/null"],
                             capture_output=True, text=True, timeout=45, cwd=tempfile.gettempdir())
        groups = json.loads(out.stdout)["command"]["data"]["groups"]
        group = next(g for g in groups if str(g.get("name", "")).startswith("Gemini"))
        found = {}
        for b in group["buckets"]:
            win = AGY_WINDOWS.get(b.get("window"))
            if win is None:
                continue
            left = max(0.0, min(1.0, float(b["remaining_fraction"])))
            if win == "5h" and left >= 1:
                reset = "not started"  # an untouched window's reset just trails the clock
            else:
                ends = datetime.fromisoformat(b["reset_time"].replace("Z", "+00:00")).timestamp()
                reset = f"{max(0, int(ends - time.time()))}s"
            found[win] = (round(100 * (1 - left)), reset)
        (five, r5), (seven, r7) = found["5h"], found["7d"]
        return five, seven, r5, r7

    def _fetch(self, cmd: str):
        out = subprocess.run([cmd, "--waybar", "--show-5h", "--format", FORMAT],
                             capture_output=True, text=True, timeout=45)
        five, seven, r5, r7 = json.loads(out.stdout)["text"].split("|")
        return int(five), int(seven), r5, r7

    def update(self):
        for p in self.providers:
            if p in self.fetched and time.monotonic() - self.fetched[p] < FETCH_EVERY.get(p, 0):
                continue
            try:
                cmd = PROVIDERS[p][1]
                value = self._fetch_agy(cmd) if p == "agy" else self._fetch(cmd)
                self.data[p] = value
                self.fetched[p] = time.monotonic()
                self.updated_at[p] = time.time()
                self.errors.pop(p, None)
            except Exception as e:  # keep last good value visible on transient failures
                self.errors[p] = type(e).__name__
                if not isinstance(self.data[p], tuple):
                    self.data[p] = f"error: {type(e).__name__}"

    def _pace(self, p: str, reset: str, win: str) -> float | None:
        elapsed = (self.preview_elapsed if self.preview_elapsed is not None else
                   time.monotonic() - self.fetched.get(p, time.monotonic()))
        remaining = parse_duration(reset)
        if win == "7d" and self.workweek and remaining is not None:
            now = self.preview_now if self.preview_now is not None else time.time()
            return max(0.0, min(100.0, self.workweek.pace(now, max(0.0, remaining - elapsed))))
        return pace_pct(reset, win, elapsed)

    def _pct_color(self, pct: int, pace: float | None):
        ahead = pace is not None and pct > pace + 5
        return t.RED if pct >= 80 else t.YELLOW if ahead else t.TEXT

    def _draw_unlabeled(self, d, w, h, y):
        """No "5h"/"7d" text: the 5-hour row is a thick bar with large type, the 7-day row
        a thin bar with small type. Percent and reset columns are fixed-width, right-aligned."""
        rows = (("5h", 16, t.font(22, "bold"), t.font(20)), ("7d", 8, t.font(16, "bold"), t.font(16)))
        big_pct_f, big_reset_f = rows[0][2], rows[0][3]
        soon_mode = self.options.get("resets", "always") == "soon"
        reset_right = w - t.PAD
        pct_right = reset_right - d.textlength("6d23h", font=big_reset_f) - 12
        logo = 40
        bx = t.PAD + logo + 12
        bw = pct_right - d.textlength("99%", font=big_pct_f) - 10 - bx
        block = (h - 2 * y) / len(self.providers)
        for i, p in enumerate(self.providers):
            color = PROVIDERS[p][2]
            by = y + i * block
            t.icon(d, t.PAD, t.px(by + block / 2 - logo / 2), p, logo)
            val = self.data[p]
            if not isinstance(val, tuple):
                d.text((bx, by + block / 2 - 12), val, font=t.font(18), fill=t.MUTED)
                continue
            five, seven, r5, r7 = val
            centers = (by + block / 2 - 13, by + block / 2 + 15)  # vertical centre of each row
            for (win, bar_h, pct_f, reset_f), pct, reset, cy in zip(rows, (five, seven), (r5, r7), centers):
                t.bar(d, bx, cy - bar_h / 2, bw, bar_h, pct, color)
                pace = self._pace(p, reset, win)
                if pace is not None:  # usage target at this point in the window
                    mx = round(bx + bw * pace / 100)
                    d.rectangle((mx - 1, t.px(cy - bar_h / 2 - 4), mx, t.px(cy + bar_h / 2 + 4)), fill=t.TEXT)
                ty = cy - t.size_of(pct_f) * 0.62
                t.text_right(d, pct_right, ty, t.pct_text(pct), pct_f, self._pct_color(pct, pace))
                if not soon_mode or self._show_reset(reset, win):
                    t.text_right(d, reset_right, cy - t.size_of(reset_f) * 0.62, reset_text(reset), reset_f, t.MUTED)
            if age_text := self.freshness_text(p):
                if self.options.get("stale") == "icon":  # clock-alert on the logo's corner, no text
                    t.stale_mark(d, t.PAD + logo - 12, by + block / 2 - logo / 2 - 8, 20)
                else:
                    d.text((bx, by + block - 13), age_text, font=t.font(12), fill=t.MUTED)

    def _draw_stacked(self, d, w, h, y):
        """Per provider: logo with "5h 43%   7d 31%" over the bars, then a thick 5h bar and a
        thin 7d bar, each with its reset countdown in a right-hand column. The bars share one
        width (one scale, so the pace ticks line up) and fill the rest of the card."""
        f, small = t.font(22, "bold"), t.font(17)
        logo = 24
        right = w - t.PAD
        bw = right - d.textlength("6d23h", font=small) - 12 - t.PAD
        _, y0, _, y1 = d.textbbox((0, 0), "0%", font=f)
        _, sy0, _, sy1 = d.textbbox((0, 0), "0", font=small)

        def label(x, cy, text, fill):  # left-aligned header text centred on cy; returns its end
            d.text((x, cy - (y0 + y1) / 2), text, font=f, fill=fill)
            return x + d.textlength(text, font=f)

        block = (h - 2 * y) / len(self.providers)
        for i, p in enumerate(self.providers):
            color = PROVIDERS[p][2]
            top = y + i * block
            head, row5, row7 = top + block * 0.22, top + block * 0.56, top + block * 0.82  # row centres
            t.icon(d, t.PAD, t.px(head - logo / 2), p, logo)
            val = self.data[p]
            x = t.PAD + logo + 12
            if not isinstance(val, tuple):
                label(x, head, val, t.MUTED)
                continue
            five, seven, r5, r7 = val
            pace5, pace7 = self._pace(p, r5, "5h"), self._pace(p, r7, "7d")
            # "5h 43%   7d 31%" over the bars: the 5h group after the logo, the 7d group
            # ending where the bars end, so the header spans the bars and the reset column
            # stands on its own. Percents
            # sit right-aligned in 2-digit slots so nothing moves, in plain text: the bars and
            # their pace ticks show how usage is tracking.
            space, pct_w = d.textlength(" ", font=f), d.textlength("99%", font=f)
            ty = head - (y0 + y1) / 2
            end5 = label(x, head, "5h", t.MUTED) + space + pct_w
            t.text_right(d, end5, ty, t.pct_text(five), f, t.TEXT)
            end7 = t.PAD + bw
            label(end7 - pct_w - space - d.textlength("7d", font=f), head, "7d", t.MUTED)
            t.text_right(d, end7, ty, t.pct_text(seven), f, t.TEXT)
            for win, cy, bh, pct, pace, reset in (("5h", row5, 16, five, pace5, r5), ("7d", row7, 8, seven, pace7, r7)):
                by = t.px(cy - bh / 2)
                t.bar(d, t.PAD, by, bw, bh, pct, color)
                if win == "7d" and self.session_pct and bw * self.session_pct / 100 >= 2:
                    # Skip notches too close to distinguish on this bar.
                    for k in range(1, math.ceil(100 / self.session_pct)):
                        nx = t.PAD + round(bw * k * self.session_pct / 100)
                        d.line((nx, by, nx, by + bh - 1), fill=t.CARD)
                if pace is not None:  # usage target at this point in the window
                    mx = round(t.PAD + bw * pace / 100)
                    d.rectangle((mx - 1, by - 2, mx, by + bh + 1), fill=t.TEXT)
                # an unused 5h window hasn't started its clock: show the full 5h00m it will get
                idle5 = win == "5h" and not_started(reset)
                if idle5 or self.options.get("resets", "always") != "soon" or self._show_reset(reset, win):
                    text = reset_text("5h") if idle5 else reset_text(reset)
                    t.text_right(d, right, cy - (sy0 + sy1) / 2, text, small, t.MUTED)
            if self.freshness_text(p):
                t.stale_mark(d, t.PAD + logo - 12, t.px(head - logo / 2) - 6, 18)

    def draw(self, d, w, h):
        y = t.card(d, w, h)
        if self.options.get("arrangement") == "stacked":
            self._draw_stacked(d, w, h, y)
            return
        if self.options.get("style") == "dense":
            self._draw_dense(d, w, h)
            return
        if not self.options.get("window_labels", True):
            self._draw_unlabeled(d, w, h, y)
            return
        f, fb = t.font(22), t.font(22, "bold")
        # fixed columns sized for the widest values ("99%", "6d23h") so bars use all the rest
        soon_mode = self.options.get("resets", "always") == "soon"
        # "soon" mode has no reset column: countdowns go small under the bar instead
        reset_w = 0 if soon_mode else d.textlength("6d23h", font=f) + 12
        pct_right = w - t.PAD - reset_w
        bx = 112
        bw = pct_right - d.textlength("99%", font=fb) - 10 - bx
        block = (h - 2 * y) / len(self.providers)
        for i, p in enumerate(self.providers):
            color = PROVIDERS[p][2]
            by = y + i * block
            t.icon(d, t.PAD, t.px(by + block / 2 - 24), p, 48)
            val = self.data[p]
            if not isinstance(val, tuple):
                d.text((76, by + block / 2 - 12), val, font=f, fill=t.MUTED)
                continue
            five, seven, r5, r7 = val
            for j, (win, pct, reset) in enumerate((("5h", five, r5), ("7d", seven, r7))):
                ry = by + block / 2 - 36 + j * 38
                d.text((74, ry), win, font=fb, fill=t.MUTED)
                t.bar(d, bx, ry + 8, bw, 14, pct, color)
                pace = self._pace(p, reset, win)
                if pace is not None:  # usage target at this point in the window
                    mx = round(bx + bw * pace / 100)
                    d.rectangle((mx - 1, ry + 3, mx, ry + 26), fill=t.TEXT)
                t.text_right(d, pct_right, ry, t.pct_text(pct), fb, self._pct_color(pct, pace))
                if not soon_mode:
                    t.text_right(d, w - t.PAD, ry, reset_text(reset), f, t.MUTED)
                elif self._show_reset(reset, win):
                    t.text_right(d, bx + bw, ry + 22, f"resets {reset_text(reset)}", t.font(13, "bold"), t.YELLOW)
            if age_text := self.freshness_text(p):
                d.text((74, by + block - 15), age_text, font=t.font(12), fill=t.MUTED)

    def _draw_dense(self, d, w, h):
        """One provider in an 80 px card, with two named usage rows."""
        p = self.providers[0]
        t.icon(d, t.PAD, 24, p, 31)
        value = self.data[p]
        if not isinstance(value, tuple):
            d.text((82, 29), t.fit_text(d, value, t.font(16), w - 96),
                   font=t.font(16), fill=t.MUTED)
            return
        five, seven, r5, r7 = value
        color = PROVIDERS[p][2]
        for label, pct, reset, y, bar_h in (("5h", five, r5, 16, 13), ("7d", seven, r7, 47, 8)):
            d.text((51, y), label, font=t.font(13, "bold"), fill=t.MUTED)
            t.bar(d, 82, y + 5, 177, bar_h, pct, color)
            pace = self._pace(p, reset, label)
            if pace is not None:
                mx = round(82 + 177 * pace / 100)
                d.rectangle((mx - 1, y + 1, mx, y + bar_h + 9), fill=t.TEXT)
            t.text_right(d, w - 77, y - 2, f"{pct}%", t.font(17, "bold"), self._pct_color(pct, pace))
            if self.options.get("resets", "always") != "soon" or self._show_reset(reset, label):
                t.text_right(d, w - 13, y, reset_text(reset), t.font(12), t.MUTED)
        if age_text := self.freshness_text(p):
            d.text((82, h - 17), age_text, font=t.font(11), fill=t.YELLOW)
