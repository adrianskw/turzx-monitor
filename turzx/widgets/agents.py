"""Active Claude Code / Codex sessions and token burn (data from turzx.agentlog)."""

import json
import math
import time
from pathlib import Path

from turzx import agentlog, theme as t
from turzx.agentlog import Session
from turzx.widget import Widget, register


CLAUDE_SETTINGS = Path.home() / ".claude/settings.json"  # autoCompactWindow: Claude's usable context
EFFORT = {"medium": "med"}  # short forms for the detail line; others fit as logged
CONTEXT_BAR_W = 48  # `context_bar`: fill bar width on single rows


def context_text(n: int) -> str:
    """Context size in whole thousands (60K, 170K); a decimal only from a million (1.0M)."""
    if not n:
        return "—"
    if n >= 999_500:
        return t.human_count(n)
    return f"{n / 1e3:.0f}K"


@register("agents")
class Agents(Widget):
    interval = 5.0

    def __init__(self, **options):
        super().__init__(**options)
        self.active_minutes = float(options.get("active_minutes", 30))
        self.working_seconds = float(options.get("working_seconds", 60))
        self.rate_minutes = float(options.get("rate_minutes", 5))
        if (not all(map(math.isfinite, (self.active_minutes, self.working_seconds, self.rate_minutes))) or
                not 0 < self.active_minutes <= agentlog.MAX_RATE_MINUTES or self.working_seconds < 0 or
                not 0 < self.rate_minutes <= agentlog.MAX_RATE_MINUTES):
            raise ValueError(f"active_minutes and rate_minutes must be between 0 and {agentlog.MAX_RATE_MINUTES}; working_seconds cannot be negative")
        self.detail_rows = options.get("detail_rows", 0)
        if type(self.detail_rows) is not int or self.detail_rows < 0:
            raise ValueError("detail_rows must be a nonnegative integer")
        # "auto": Claude Code's autoCompactWindow from ~/.claude/settings.json (compaction
        # fires there, so that is the usable window), else 1M
        self.claude_window = options.get("claude_window", "auto")
        if self.claude_window != "auto" and (type(self.claude_window) is not int or self.claude_window <= 0):
            raise ValueError('claude_window must be "auto" or a positive integer')
        self._settings = (None, None)  # (mtime_ns, autoCompactWindow) of CLAUDE_SETTINGS
        self.name_weight = options.get("name_weight", "regular")
        if not isinstance(self.name_weight, str) or self.name_weight not in t.FONTS:
            raise ValueError(f"name_weight must be one of: {', '.join(t.FONTS)}")
        self.rate = 0.0
        self.total_today = 0
        self.cache_hit: float | None = None
        self.active: list[Session] = []
        self.history_sources: dict[str, Widget] = {}
        self.preview_now: float | None = None
        self.sleepy = options.get("sleepy", False)  # no sessions: the tool icons doze, z's drifting up
        self.nudge = options.get("nudge", False)    # one session (`grow`): a vine creeps along under it
        self.nudge_minutes = float(options.get("nudge_minutes", 30))
        if not math.isfinite(self.nudge_minutes) or self.nudge_minutes <= 0:
            raise ValueError("nudge_minutes must be a positive finite number")
        self.solo_since: float | None = None  # when the session count last became one
        if options.get("status") == "dot" or options.get("waiting", False) or self.sleepy or self.nudge:
            # The working dot and waiting bell breathe every `pulse` seconds,
            # redrawn every `pulse_step` seconds (a tiny region, so cheap to send).
            for name, attr, default in (("pulse", "pulse", 10), ("pulse_step", "frame_interval", 1)):
                value = options.get(name, default)
                try:
                    seconds = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{name} must be a positive finite number") from exc
                if isinstance(value, bool) or not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError(f"{name} must be a positive finite number")
                setattr(self, attr, seconds)

    def update(self):
        log = agentlog.shared()
        log.refresh()
        self.rate = log.rate(self.rate_minutes)
        self.total_today = log.total_today()
        self.cache_hit = log.cache_hit(self.rate_minutes)
        self.active = log.active(self.active_minutes)
        if len(self.active) != 1:
            self.solo_since = None
        elif self.solo_since is None:
            self.solo_since = time.time()

    def bind_history_sources(self, sources: dict[str, Widget]) -> None:
        """Use the CPU/GPU widgets' already collected samples for the dense view."""
        self.history_sources = sources

    # ---- drawing -----------------------------------------------------------
    DOT = "\uf444"
    BELL = "\U000F009E"  # nf-md-bell_ring
    FIRE = "\U000F0238"
    SIGMA = "\U000F04A0"

    def _now(self) -> float:
        return self.preview_now if self.preview_now is not None else time.time()

    def _status(self, d, right: float, y: float, s: Session, f) -> None:
        """Right-aligned status: pulsing dot while working, else idle age. With `time_colors`,
        the dot goes green -> yellow -> red with how long the job has run, and the idle timer
        does the same with how long the session has sat idle."""
        now = self._now()
        idle = now - s.mtime
        ramp = self.options.get("time_colors", False)
        if self._waiting(s):  # stopped on a permission prompt or question: bell + wait time
            timer = t.duration_text(idle).strip()
            t.text_right(d, right, y, timer, f, t.MAGENTA)
            _, y0, _, y1 = d.textbbox((0, y), "0", font=f)
            box = t.px(t.size_of(f) * 0.8)  # sized by its ink: the glyph overhangs its advance
            t.glyph_icon(d, right - d.textlength(timer, font=f) - 6 - box, (y0 + y1) / 2, self.BELL, box,
                         t.blend(t.CARD, t.MAGENTA, self._pulse()))
        elif idle >= self.working_seconds:
            color = self._time_color(idle) if ramp else t.MUTED
            t.text_right(d, right, y, t.duration_text(idle), f, color)
        elif self.options.get("status", "text") == "dot":
            running = now - s.turn_start if s.turn_start else 0.0
            color = t.blend(t.CARD, self._time_color(running) if ramp else t.GREEN, self._pulse())
            # centre the dot on the middle character of the 3-char idle timer column ("12m")
            cx = right - d.textlength("59m", font=f) / 2
            x0, _, x1, _ = d.textbbox((0, y), self.DOT, font=f)
            d.text((cx - (x0 + x1) / 2, y), self.DOT, font=f, fill=color)
        else:
            t.text_right(d, right, y, "working", f, t.GREEN)

    def _waiting(self, s: Session) -> bool:
        """True when the session needs you (`waiting` option): its turn is open and stalled on a
        tool call with no result. A question or plan approval counts at once; any other call
        after `working_seconds` of silence (a permission prompt, or a long-running command,
        which the logs can't tell apart). Pending subagents are work, not waiting."""
        if not self.options.get("waiting", False) or not s.turn_open or not s.pending:
            return False
        names = set(s.pending.values())
        if names & {"AskUserQuestion", "ExitPlanMode"}:
            return True
        if names <= {"Task", "Agent"}:
            return False
        return self._now() - s.mtime >= self.working_seconds

    def _status_width(self, d, s: Session, f) -> float:
        """Width to reserve for the timer, plus the bell when this session waits."""
        base = d.textlength("59m" if self.options.get("status", "text") == "dot" else "working", font=f)
        if not self._waiting(s):
            return base
        timer = t.duration_text(self._now() - s.mtime).strip()
        return max(base, d.textlength(timer, font=f) + t.px(t.size_of(f) * 0.8) + 6)

    def draw(self, d, w, h):
        y = t.card(d, w, h)
        if self.options.get("style") == "dense":
            self._draw_dense(d, w, h)
            return
        stats = self.options.get("stats", "top")
        if stats == "left":
            self._draw_stats_left(d, y, w, h)
        elif stats == "none":  # burn/today shown elsewhere (the burn widget)
            self._draw_list(d, t.PAD, y, w, h)
        else:
            self._draw_stats_top(d, y, w, h)

    def _draw_stats_top(self, d, y, w, h):
        big, small = t.font(30, "bold"), t.font(18, "bold")
        # headline: burn rate (left) and today's total (right)
        rate = t.human_count(self.rate)
        d.text((t.PAD, y - 2), rate, font=big, fill=t.TEXT)
        d.text((t.PAD + d.textlength(rate, font=big) + 6, y + 10), "tok/min", font=small, fill=t.MUTED)
        total = t.human_count(self.total_today)
        t.text_right(d, w - t.PAD, y - 2, total, big, t.TEXT)
        t.text_right(d, w - t.PAD - d.textlength(total, font=big) - 6, y + 10, "today", small, t.MUTED)

        rows = int((h - y - 50) // 32)
        f = t.font(20)
        if not self.active:
            d.text((t.PAD, y + 50), "no active agents", font=f, fill=t.MUTED)
            return
        shown = self.active[:rows] if len(self.active) <= rows else self.active[:rows - 1]
        state_w = max((self._status_width(d, s, f) for s in shown),
                      default=d.textlength("59m" if self.options.get("status", "text") == "dot" else "working", font=f))
        for i, s in enumerate(shown):
            ry = y + 48 + i * 32
            t.icon(d, t.PAD, t.px(ry), s.tool, 24)
            self._status(d, w - t.PAD, ry, s, f)
            tokens = t.human_count(s.today)
            tok_right = w - t.PAD - state_w - 12
            t.text_right(d, tok_right, ry, tokens, f, t.MUTED)
            name_room = tok_right - d.textlength("999.9M", font=f) - 10 - (t.PAD + 32)
            d.text((t.PAD + 32, ry), t.fit_text(d, s.label, f, name_room), font=f, fill=t.TEXT)
        if len(shown) < len(self.active):
            d.text((t.PAD + 32, y + 48 + len(shown) * 32), f"+{len(self.active) - len(shown)} more",
                   font=f, fill=t.MUTED)

    def _draw_stats_left(self, d, y, w, h):
        """Burn rate and today's total stacked in a narrow left column; the session list
        gets the rest."""
        big, icon_f = t.font(30, "bold"), t.font(26)
        col_w = d.textlength("999.9K", font=big) + 34
        mid = h / 2
        for glyph, value, color, cy in ((self.FIRE, self.rate, t.ORANGE, mid - 38),
                                        (self.SIGMA, self.total_today, t.MAGENTA, mid + 4)):
            d.text((t.PAD, cy + 2), glyph, font=icon_f, fill=color)
            t.text_right(d, t.PAD + col_w, cy, t.human_count(value), big, t.TEXT)
        self._draw_list(d, t.PAD + col_w + 18, y, w, h)

    def _draw_list(self, d, left, y, w, h):
        """One session per two-line row: project name over its tokens today.
        rows = "single": one line per session (full-width name, tokens and status on the right)."""
        if self.options.get("rows") == "single":
            self._draw_list_single(d, left, y, w, h)
            return
        mid = h / 2
        name_f, sub_f = t.font(18, "bold"), t.font(14)
        rings = self.options.get("cache_ring", False)
        row_h = 36
        rows = int((h - 2 * y + 4) // row_h)
        if not self.active:
            d.text((left, mid - 12), "no active agents", font=t.font(20), fill=t.MUTED)
            return
        shown = self.active[:rows] if len(self.active) <= rows else self.active[:rows - 1]
        status_w = max((self._status_width(d, s, name_f) for s in shown),
                       default=d.textlength("59m" if self.options.get("status", "text") == "dot" else "working", font=name_f))
        for i, s in enumerate(shown):
            ry = y + i * row_h
            t.icon(d, left, t.px(ry + 4), s.tool, 26)
            self._status(d, w - t.PAD, ry + 4, s, name_f)
            text_x = left + 34
            right = w - t.PAD - status_w - 10
            if rings:  # today's cache hit rate for this session
                t.ring(d, right - 11, ry + 16, 11, 4, s.cache_hit, t.cache_color(s.cache_hit))
                right -= 32
            room = right - text_x
            d.text((text_x, ry - 1), t.fit_text(d, s.label, name_f, room), font=name_f, fill=t.TEXT)
            d.text((text_x, ry + 20), t.human_count(s.today), font=sub_f, fill=t.MUTED)
        if len(shown) < len(self.active):
            d.text((left + 34, y + len(shown) * row_h + 4), f"+{len(self.active) - len(shown)} more",
                   font=t.font(18), fill=t.MUTED)

    def _draw_dense(self, d, w, h):
        """Full-width one-line session list with a compact burn header."""
        big, small = t.font(25, "bold"), t.font(13)
        d.text((t.PAD, 7), self.FIRE, font=t.font(25), fill=t.ORANGE)
        rate = t.human_count(self.rate)
        d.text((44, 7), rate, font=big, fill=t.TEXT)
        unit_x = 44 + d.textlength(rate, font=big) + 7
        d.text((unit_x, 17), "/min", font=small, fill=t.MUTED)
        ring_x = unit_x + d.textlength("/min", font=small) + 70
        if self.options.get("cache_ring", False):
            t.ring(d, ring_x, 23, 10, 3, self.cache_hit, t.cache_color(self.cache_hit))
        sigma_x = ring_x + 22
        d.text((sigma_x, 7), self.SIGMA, font=t.font(25), fill=t.MAGENTA)
        d.text((sigma_x + 30, 7), t.human_count(self.total_today), font=big, fill=t.TEXT)
        t.text_right(d, w - t.PAD, 16, f"{len(self.active)} sessions", t.font(14), t.MUTED)
        d.line((t.PAD, 45, w - t.PAD, 45), fill=t.TRACK)

        capacity = max(1, int((h - 52) // 22))
        shown = self.active[:capacity] if len(self.active) <= capacity else self.active[:capacity - 1]
        name_f, token_f, state_f = t.font(18, "bold"), t.font(16), t.font(15)
        status_w = max((self._status_width(d, s, state_f) for s in shown), default=0)
        # Existing spacing leaves 69 px beside a cache ring, or 117 px beside tokens.
        status_extra = max(0, status_w - (69 if self.options.get("cache_ring", False) else 117))
        for i, session in enumerate(shown):
            y = 49 + i * 22
            t.icon(d, 15, y + 1, session.tool, 19)
            name = t.fit_text(d, session.label, name_f, w - 255 - status_extra)
            d.text((43, y), name, font=name_f, fill=t.TEXT)
            t.text_right(d, w - 141 - status_extra, y, t.human_count(session.today), token_f, t.MUTED)
            if self.options.get("cache_ring", False):
                t.ring(d, w - 101 - status_extra, y + 10, 8, 3, session.cache_hit, t.cache_color(session.cache_hit))
            self._status(d, w - t.PAD, y, session, state_f)
        if len(shown) < len(self.active):
            d.text((43, 49 + len(shown) * 22), f"+{len(self.active) - len(shown)} more",
                   font=t.font(16), fill=t.MUTED)

        if self.options.get("show_history", False) and len(self.active) <= 3:
            self._draw_dense_history(d, w, h)

    def _draw_dense_history(self, d, w, h):
        """Fill spare session space with the existing CPU/GPU histories."""
        if not self.history_sources:
            return
        top = h - 86
        d.line((t.PAD, top, w - t.PAD, top), fill=t.TRACK)
        d.line((w // 2, top + 5, w // 2, h - 12), fill=t.TRACK)
        for kind, left, color in (("cpu", 16, t.ACCENT), ("gpu", w // 2 + 16, t.MAGENTA)):
            source = self.history_sources.get(kind)
            if source is None:
                continue
            d.text((left, top + 4), f"{kind.upper()} · 60s", font=t.font(13, "bold"), fill=t.MUTED)
            values = source.history.copy()
            t.sparkline(d, left, top + 27, w // 2 - 32, 43, values, color=color, dim=True)

    def _time_color(self, seconds: float):
        """Green, fading to yellow by `time_warn` minutes (10) and to red by `time_crit` (20)."""
        o = self.options
        return t.ramp(seconds / 60, float(o.get("time_warn", 10)), float(o.get("time_crit", 20)), base=t.GREEN, start=0)

    def _context_color(self, s: Session):
        """Name color by context size: normal, then yellow, then red as the context grows.
        Uses % of the window when it is known (logged by Codex; Claude's autoCompactWindow
        or `claude_window`), else token counts."""
        o = self.options
        window = self._context_window(s)
        if window:
            pct = 100 * s.context / window
            return t.ramp(pct, float(o.get("context_warn_pct", 60)), float(o.get("context_crit_pct", 85)))
        return t.ramp(s.context, float(o.get("context_warn", 200_000)), float(o.get("context_crit", 400_000)))

    def _known_claude_window(self) -> int | None:
        """Claude's context window when configured: `claude_window`, else Claude Code's
        autoCompactWindow setting (re-read when the settings file changes)."""
        if self.claude_window != "auto":
            return self.claude_window
        try:
            mtime = CLAUDE_SETTINGS.stat().st_mtime_ns
            if mtime != self._settings[0]:
                value = json.loads(CLAUDE_SETTINGS.read_text()).get("autoCompactWindow")
                ok = type(value) is int and value > 0
                self._settings = (mtime, value if ok else None)
        except (OSError, ValueError, AttributeError):
            self._settings = (None, None)
        return self._settings[1]

    def next_frame(self, now: float) -> float:
        """Every `pulse_step` while a dot or bell is on screen; otherwise only when an idle
        timer next ticks over ('1m' -> '2m', '1h' -> '2h'), and at least once a minute."""
        wake = now + 60
        if (self.sleepy and not self.active) or (self._nudging() and len(self.active) == 1):
            return super().next_frame(now)
        for s in self.active:
            idle = now - s.mtime
            if self._waiting(s) or idle < self.working_seconds:
                return super().next_frame(now)
            unit = 86400 if idle >= 86400 else 3600 if idle >= 3600 else 60
            wake = min(wake, s.mtime + (idle // unit + 1) * unit)
        return wake

    def _nudging(self) -> bool:
        return self.nudge and self.options.get("grow", False) and self.options.get("rows") == "single"

    def _draw_sleeping(self, d, left, y, w, h):
        """No sessions (`sleepy`): Claude, Codex and agy doze in a row, each breathing out of step,
        with z's drifting up and fading off to their upper right."""
        now = self._now()
        size, step = 44, 104
        cap_f = t.font(17)
        _, c0, _, c1 = d.textbbox((0, 0), "0", font=cap_f)
        block = 40 + size + 16 + (c1 - c0)  # z's, icons, caption
        top = (h - block) / 2 + 40
        x0 = (w - (2 * step + size)) / 2
        for i, tool in enumerate(("claude", "codex", "agy")):
            ix = x0 + i * step
            breath = 0.5 + 0.5 * math.cos(2 * math.pi * (now / 8 + i / 3))
            t.icon(d, ix, top, tool, size, alpha=0.3 + 0.25 * breath)
            for p in range(2):  # two z's per icon, half a cycle apart
                age = ((now + 2 * i) / 6 + p / 2) % 1
                zf = t.font(t.px(12 + 10 * age), "bold")
                color = t.blend(t.CARD, t.TEXT, 0.8 * math.sin(math.pi * age))
                d.text((ix + size * 0.8 + 14 * age, top - 2 - 40 * age), "z", font=zf, fill=color)
        caption = "no agents running"
        d.text(((w - d.textlength(caption, font=cap_f)) / 2, top + size + 16 - c0), caption,
               font=cap_f, fill=t.MUTED)

    def _draw_vine(self, d, left, right, top, height):
        """One session (`nudge`): a vine creeps from the left for as long as only one agent runs,
        reaching the caption after `nudge_minutes`. Its leaves sway; past that it's overgrown:
        the leaves yellow and the caption, in yellow, says how long it has been solo."""
        now = self._now()
        solo = max(0.0, now - (self.solo_since if self.solo_since is not None else now))
        grown = solo / (self.nudge_minutes * 60)
        cap_f = t.font(17)
        _, c0, _, c1 = d.textbbox((0, 0), "0", font=cap_f)
        cy = top + height / 2
        if grown >= 1:
            caption, cap_color = f"{t.duration_text(solo)} solo", t.YELLOW
        else:
            caption = ("just one agent" if grown < 0.25 else "room for more" if grown < 0.5
                       else "spin up another?")
            cap_color = t.MUTED
        t.text_right(d, right, cy - (c0 + c1) / 2, caption, cap_f, cap_color)
        end = right - d.textlength("spin up another?", font=cap_f) - 16  # the caption's widest
        length = 14 + (end - left - 14) * min(1.0, grown)
        leaf = t.blend(t.GREEN, t.YELLOW, max(0.0, min(1.0, grown - 1)))  # yellows over its 2nd stint
        stem = t.blend(t.CARD, leaf, 0.7)

        def wave(x):
            return cy + 3 * math.sin((x - left) / 17)

        d.line([(x, wave(x)) for x in range(int(left), int(left + length) + 1, 2)], fill=stem, width=3)
        for i, x in enumerate(range(int(left) + 16, int(left + length) - 6, 24)):
            side = -1 if i % 2 else 1  # alternate above (1) and below the stem
            sway = 0.25 * math.sin(2 * math.pi * now / 4 + i * 1.3)
            a = 0.9 + sway  # tilted forward off the stem, up or down
            ux, uy = math.cos(a), -side * math.sin(a)
            nx, ny = -uy, ux
            bx, by = x, wave(x)
            pts = [(bx, by), (bx + 7 * ux + 4 * nx, by + 7 * uy + 4 * ny), (bx + 15 * ux, by + 15 * uy),
                   (bx + 7 * ux - 4 * nx, by + 7 * uy - 4 * ny)]
            d.polygon(pts, fill=leaf)
        tip = left + length
        d.ellipse((tip - 3, wave(tip) - 3, tip + 3, wave(tip) + 3), fill=t.blend(leaf, t.TEXT, 0.4))

    def _pulse(self) -> float:
        phase = (self._now() % self.pulse) / self.pulse
        return 0.3 + 0.7 * (0.5 + 0.5 * math.cos(2 * math.pi * phase))  # 1 -> 0.3 -> 1

    def _context_window(self, s: Session) -> int | None:
        if s.window is not None and s.window > 0:
            return s.window
        return self._known_claude_window() if s.tool == "claude" else None

    def _window(self, s: Session) -> int:
        """Context window: logged by Codex; Claude doesn't log it, so autoCompactWindow or
        `claude_window`, else 1M."""
        return self._context_window(s) or 1_000_000

    def _draw_detailed(self, d, left, y, w, h, k=1.0):
        """Few sessions (<= `detail_rows`): two lines each. Name + status, then model, a
        context-fill bar and context tokens. Names stay neutral: the bar shows fill.
        `k` scales everything up for fewer sessions (`grow`)."""
        slot = min((h - 2 * y) / len(self.active), 90 * k)
        # names shrink with the slot (23 px at 64+, 22 px at 60) so four sessions still fit
        name_size = min(t.px(23 * k), t.px(slot * 0.36))
        content = name_size + 29 * k  # name line to the bottom of the detail line
        pad = max(2, (slot - content) / 2)  # centred in its slot: few sessions spread out
        name_f = t.font(name_size, self.name_weight)
        small = t.font(16 * k)
        small_b = t.font(16 * k, "bold")
        status_right = w - t.PAD
        text_x = left + t.px(30 * k)
        model_w = d.textlength("gemini-3.8-flash high", font=small)  # longest expected model + effort
        num_w = d.textlength("999K", font=small_b)
        icon = min(t.px(22 * k), name_size)
        bar_h = t.px(8 * k)
        n0, n1 = t.ink(d, name_f)
        s0, s1 = t.ink(d, small)
        lines = [max(n1 - n0, icon), max(s1 - s0, bar_h)]  # ink heights: name line, detail line
        if t.MARGIN is not None:  # first name on the top margin, last detail line on the bottom
            tops = t.spread(t.PAD, h - t.PAD, [lines] * len(self.active), max_within=12)
        for i, s in enumerate(self.active):
            if t.MARGIN is not None:
                ry = tops[i][0] + (lines[0] - (n1 - n0)) / 2 - n0
                t.icon(d, left, t.px(ry + (n0 + n1) / 2 - icon / 2), s.tool, icon)
            else:
                ry = y + i * slot + pad
                t.icon(d, left, ry + name_size * 0.6 - icon / 2, s.tool, icon)
            self._status(d, status_right, ry, s, name_f)
            name_right = status_right - self._status_width(d, s, name_f) - 12
            d.text((text_x, ry), t.fit_text(d, s.label, name_f, name_right - text_x),
                   font=name_f, fill=t.TEXT)
            # detail line centre
            cy = tops[i][1] + lines[1] / 2 if t.MARGIN is not None else ry + name_size + 21 * k
            ty = cy - (s0 + s1) / 2
            # left to right: model + effort under the name, context tokens in a column after
            # the longest model, then the fill bar out to the right edge
            model = s.model.removeprefix("claude-") or "?"
            effort = EFFORT.get(s.effort, s.effort)
            eff_w = d.textlength(" " + effort, font=small) if effort else 0
            model = t.fit_text(d, model, small, model_w - eff_w)
            model_right = text_x + model_w
            d.text((text_x, ty), model, font=small, fill=t.MUTED)
            if effort:  # effort a step brighter than the model so it stands apart
                d.text((text_x + d.textlength(model, font=small) + eff_w - d.textlength(effort, font=small), ty),
                       effort, font=small, fill=t.TEXT)
            count_right = model_right + 14 + num_w
            t.text_right(d, count_right, ty, context_text(s.context), small_b, t.TEXT)
            bar_left = count_right + 10
            if s.context:  # unknown (agy doesn't report it): no empty bar
                t.bar(d, bar_left, t.px(cy - bar_h / 2), status_right - bar_left, bar_h, self._fill(s), t.ACCENT)

    def _fill(self, s: Session) -> float:
        """Context fill, % of the window."""
        window = self._window(s)
        return max(0.0, min(100.0, 100 * s.context / window)) if window else 0.0

    def _draw_hero(self, d, left, y, w, h, inset=24):
        """One or two sessions (`grow`): each gets a large block (see _draw_big); two share the
        card, a size down. One alone gets the `nudge` vine under it. `inset` widens the right
        margin beyond the rows' (without a [display] margin); status, % and bar end together.
        With a margin, the lines run from the top margin to the bottom one, evenly spaced
        (two sessions: twice the gap between them)."""
        right = w - t.PAD - inset
        n = min(2, len(self.active))
        k = 1.0 if n == 1 else 0.8
        heights = self._big_heights(d, k)
        vine = self._nudging() and n == 1
        vine_h = 26
        if t.MARGIN is not None:
            groups = [list(heights) + ([vine_h] if vine else [])] if n == 1 else [list(heights)] * 2
            tops = t.spread(t.PAD, h - t.PAD, groups)
            if vine:
                self._draw_vine(d, left + 44, right, tops[0][3], vine_h)
            for s, g in zip(self.active[:n], tops):
                self._draw_big(d, s, left, right, k, g[:3])
            return
        bottom = h - y
        if n == 1:
            if vine:  # the vine's row, last
                gap = (h - 2 * y - vine_h) / 5  # as if a fourth line of the block
                self._draw_vine(d, left + 44, right, bottom - gap - vine_h, vine_h)
                bottom -= gap + vine_h
            self._draw_big(d, self.active[0], left, right, k, self._even(y, bottom, heights))
            return
        half = (bottom - y) / 2
        for i, s in enumerate(self.active[:2]):
            self._draw_big(d, s, left, right, k, self._even(y + i * half, y + (i + 1) * half, heights))

    @staticmethod
    def _even(top, bottom, heights):
        """Tops of lines spread from top to bottom, the same gap at the edges and between."""
        gap = (bottom - top - sum(heights)) / (len(heights) + 1)
        out, y = [], top + gap
        for hh in heights:
            out.append(y)
            y += hh + gap
        return out

    def _big_fonts(self, k):
        return (t.font(t.px(30 * k), self.name_weight), t.font(t.px(21 * k)), t.font(t.px(21 * k), "bold"),
                t.px(32 * k), t.px(14 * k), t.px(10 * k))  # name, mid, mid bold, icon, bar, bar gap

    def _big_heights(self, d, k):
        """Ink heights of _draw_big's lines: name (or its taller icon), model, context + bar."""
        name_f, mid_f, _, icon, bar_h, bar_gap = self._big_fonts(k)
        n0, n1 = t.ink(d, name_f)
        m0, m1 = t.ink(d, mid_f)
        return (max(n1 - n0, icon), m1 - m0, m1 - m0 + bar_gap + bar_h)

    def _draw_big(self, d, s, left, right, k, tops):
        """A large name and status, the model and effort, then its context as "60K of 258K",
        the fill % and a bar on a line of its own, the lines' ink starting at `tops`. `k`
        scales it (1: one session)."""
        name_f, mid_f, mid_b, icon, bar_h, bar_gap = self._big_fonts(k)
        _, n0, _, n1 = d.textbbox((0, 0), "0", font=name_f)
        _, m0, _, m1 = d.textbbox((0, 0), "0", font=mid_f)
        line1 = self._big_heights(d, k)[0]
        top = tops[0] + (line1 - (n1 - n0)) / 2  # the name's digits, centred in the icon's height
        # line 1: icon, name, status
        ny = top - n0
        t.icon(d, left, t.px(top + (n1 - n0) / 2 - icon / 2), s.tool, icon)
        self._status(d, right, ny, s, name_f)
        text_x = left + t.px(44 * k)
        name_right = right - self._status_width(d, s, name_f) - 14
        name_color = self._context_color(s) if self.options.get("context_colors", False) else t.TEXT
        d.text((text_x, ny), t.fit_text(d, s.label, name_f, name_right - text_x), font=name_f, fill=name_color)
        # line 2: model, then effort a step brighter
        top = tops[1]
        model = s.model.removeprefix("claude-") or "?"
        effort = EFFORT.get(s.effort, s.effort)
        d.text((text_x, top - m0), model, font=mid_f, fill=t.MUTED)
        if effort:
            d.text((text_x + d.textlength(model + " ", font=mid_f), top - m0), effort, font=mid_f, fill=t.TEXT)
        # line 3: context used of the window, fill % at the right, the bar under both
        top = tops[2]
        if not s.context:  # unknown (agy doesn't report it)
            d.text((text_x, top - m0), "context —", font=mid_f, fill=t.MUTED)
            return
        used = context_text(s.context)
        d.text((text_x, top - m0), used, font=mid_b, fill=t.TEXT)
        of = f" of {context_text(self._window(s))}"
        d.text((text_x + d.textlength(used, font=mid_b), top - m0), of, font=mid_f, fill=t.MUTED)
        pct = self._fill(s)
        t.text_right(d, right, top - m0, t.pct_text(pct), mid_b, t.TEXT)
        t.bar(d, text_x, t.px(top + (m1 - m0) + bar_gap), right - text_x, bar_h, pct, t.ACCENT)

    def _draw_list_single(self, d, left, y, w, h):
        grow = self.options.get("grow", False)
        if not self.active and self.sleepy:
            self._draw_sleeping(d, left, y, w, h)
            return
        if grow and len(self.active) in (1, 2):
            self._draw_hero(d, left, y, w, h, inset=0 if t.MARGIN is not None else 24)
            return
        if 0 < len(self.active) <= self.detail_rows and (h - 2 * y) / len(self.active) >= 60:
            self._draw_detailed(d, left, y, w, h)
            return
        row_h = int(self.options.get("row_height", 28))
        name_f = t.font(t.px(row_h * 0.64), self.name_weight)
        tok_f = t.font(t.px(row_h * 0.57))
        rows = int((h - 2 * y + 6) // row_h)
        if not self.active:
            d.text((left, h / 2 - 12), "no active agents", font=t.font(20), fill=t.MUTED)
            return
        cap = int(self.options.get("max_rows", rows))  # e.g. 5: at most 5 sessions, then "+N more"
        if len(self.active) <= min(cap, rows):
            shown = self.active
        else:
            shown = self.active[:min(cap, rows - 1)]
        if self.options.get("spread", False):  # rows share out the card's height
            row_h = max(row_h, (h - 2 * y + 6) / (len(shown) + (len(shown) < len(self.active))))
        base_h = int(self.options.get("row_height", 28))
        row_y = [y + i * row_h + (row_h - base_h) / 2 for i in range(len(shown) + 1)]
        if t.MARGIN is not None:  # first row on the top margin, last on the bottom, even gaps
            n0, n1 = t.ink(d, name_f)
            top_off, bot_off = min(2, n0), max(24, n1)  # the icon (2..24) or the name's digits
            count = len(shown) + (len(shown) < len(self.active))
            row_y = [g[0] - top_off for g in t.spread(t.PAD, h - t.PAD, [[bot_off - top_off]] * count)]
            row_y.append(row_y[-1])
        status_right = w - t.PAD
        status_w = max((self._status_width(d, s, name_f) for s in shown),
                       default=d.textlength("59m" if self.options.get("status", "text") == "dot" else "working", font=name_f))
        tok_right = status_right - status_w - 12
        context = self.options.get("context_bar", False)  # context tokens + fill bar mid-row
        ctx_w = d.textlength("999K", font=tok_f) + 8 + CONTEXT_BAR_W if context else 0
        tokens = self.options.get("tokens", True)  # false: no per-session token column
        rings = self.options.get("cache_ring", False)
        ring_x = tok_right - (d.textlength("99.9M", font=tok_f) + 18 if tokens else 0)
        name_right = (ring_x - 16 if rings else tok_right - (d.textlength("99.9M", font=tok_f) if tokens else 0)) - 10
        ctx_right, name_right = name_right, name_right - (ctx_w + 12 if context else 0)
        for i, s in enumerate(shown):
            ry = row_y[i]
            t.icon(d, left, t.px(ry + 2), s.tool, 22)
            self._status(d, status_right, ry, s, name_f)
            if tokens:
                t.text_right(d, tok_right, ry + 2, t.human_count(s.today), tok_f, t.MUTED)
            if rings:
                t.ring(d, ring_x, ry + 12, 9, 3, s.cache_hit, t.cache_color(s.cache_hit))
            text_x = left + 30
            if context:
                _, y0, _, y1 = d.textbbox((0, ry), "0", font=name_f)
                cy = (y0 + y1) / 2
                if s.context:  # unknown (agy doesn't report it): no empty bar
                    t.bar(d, ctx_right - CONTEXT_BAR_W, t.px(cy - 3), CONTEXT_BAR_W, 6, self._fill(s), t.ACCENT)
                _, c0, _, c1 = d.textbbox((0, 0), "0", font=tok_f)
                t.text_right(d, ctx_right - CONTEXT_BAR_W - 8, cy - (c0 + c1) / 2,
                             context_text(s.context), tok_f, t.MUTED)
            name_color = self._context_color(s) if self.options.get("context_colors", False) else t.TEXT
            room = name_right - text_x
            d.text((text_x, ry), t.fit_text(d, s.label, name_f, room), font=name_f, fill=name_color)
        if len(shown) < len(self.active):
            more = f"+{len(self.active) - len(shown)} more"
            if self.options.get("total", False):
                more += f" · {len(self.active)} agents"
            ry = row_y[len(shown)]
            d.text((left + 30, ry), more, font=name_f, fill=t.MUTED)
            if self.options.get("summary", False):
                self._draw_summary(d, status_right, ry, name_f)

    def _draw_summary(self, d, right, y, f):
        """Right side of the "+N more" row, over all sessions: how many are working (green
        dot), then how many run each tool, e.g. "✳ 5  ⌨ 3  ● 2"."""
        now = self._now()
        working = sum(now - s.mtime < self.working_seconds for s in self.active)
        x = right
        if working:
            t.text_right(d, x, y, str(working), f, t.TEXT)
            x -= d.textlength(str(working), font=f) + 7
            t.text_right(d, x, y, self.DOT, f, t.GREEN)
            x -= d.textlength(self.DOT, font=f) + 16
        for tool in ("agy", "codex", "claude"):
            count = sum(s.tool == tool for s in self.active)
            if not count:
                continue
            t.text_right(d, x, y, str(count), f, t.TEXT)
            x -= d.textlength(str(count), font=f) + 26
            t.icon(d, x, y + 2, tool, 20)
            x -= 16
