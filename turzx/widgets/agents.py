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
        if options.get("status") == "dot":
            # the working dot breathes: one smooth fade cycle every `pulse` seconds,
            # redrawn every `pulse_step` seconds (a tiny region, so cheap to send)
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

    def bind_history_sources(self, sources: dict[str, Widget]) -> None:
        """Use the CPU/GPU widgets' already collected samples for the dense view."""
        self.history_sources = sources

    # ---- drawing -----------------------------------------------------------
    DOT = "\uf444"
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
        if idle >= self.working_seconds:
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
        dots = self.options.get("status", "text") == "dot"
        state_w = d.textlength("59m" if dots else "working", font=f)
        for i, s in enumerate(shown):
            ry = y + 48 + i * 32
            t.icon(d, t.PAD, int(ry), s.tool, 24)
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
        dots = self.options.get("status", "text") == "dot"
        status_w = d.textlength("59m" if dots else "working", font=name_f)
        for i, s in enumerate(shown):
            ry = y + i * row_h
            t.icon(d, left, int(ry + 4), s.tool, 26)
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
        for i, session in enumerate(shown):
            y = 49 + i * 22
            t.icon(d, 15, y + 1, session.tool, 19)
            name = t.fit_text(d, session.label, name_f, w - 255)
            d.text((43, y), name, font=name_f, fill=t.TEXT)
            t.text_right(d, w - 141, y, t.human_count(session.today), token_f, t.MUTED)
            if self.options.get("cache_ring", False):
                t.ring(d, w - 101, y + 10, 8, 3, session.cache_hit, t.cache_color(session.cache_hit))
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

    def _draw_detailed(self, d, left, y, w, h):
        """Few sessions (<= `detail_rows`): two lines each. Name + status, then model, a
        context-fill bar and context tokens. Names stay neutral: the bar shows fill."""
        slot = min((h - 2 * y) / len(self.active), 72)
        # names shrink with the slot (23 px at 64+, 22 px at 60) so four sessions still fit
        name_size = min(23, round(slot * 0.36))
        name_f = t.font(name_size, self.name_weight)
        small = t.font(16)
        small_b = t.font(16, "bold")
        status_right = w - t.PAD
        text_x = left + 30
        model_w = d.textlength("gpt-6.6-astra xhigh", font=small)  # longest expected model + effort
        num_w = d.textlength("99.9K", font=small_b)
        for i, s in enumerate(self.active):
            ry = y + i * slot + 2
            icon = min(22, name_size)
            t.icon(d, left, ry + name_size * 0.6 - icon / 2, s.tool, icon)
            self._status(d, status_right, ry, s, name_f)
            name_right = status_right - d.textlength("59m", font=name_f) - 12
            d.text((text_x, ry), t.fit_text(d, s.label, name_f, name_right - text_x),
                   font=name_f, fill=t.TEXT)
            cy = ry + name_size + 21  # detail line centre
            _, y0, _, y1 = d.textbbox((0, 0), "0", font=small)
            ty = cy - (y0 + y1) / 2
            # left to right: model + effort (right-aligned in their column, so efforts line
            # up), context tokens, then the fill bar out to the right edge
            model = s.model.removeprefix("claude-") or "?"
            effort = EFFORT.get(s.effort, s.effort)
            eff_w = d.textlength(" " + effort, font=small) if effort else 0
            model = t.fit_text(d, model, small, model_w - eff_w)
            model_right = text_x + model_w
            if effort:  # effort a step brighter than the model so it stands apart
                t.text_right(d, model_right, ty, effort, small, t.TEXT)
            t.text_right(d, model_right - eff_w, ty, model, small, t.MUTED)
            count_right = model_right + 14 + num_w
            t.text_right(d, count_right, ty, t.human_count(s.context) if s.context else "—", small_b, t.TEXT)
            window = self._window(s)
            pct = max(0.0, min(100.0, 100 * s.context / window)) if window else 0.0
            bar_left = count_right + 10
            t.bar(d, bar_left, round(cy - 4), status_right - bar_left, 8, pct, t.ACCENT)

    def _draw_list_single(self, d, left, y, w, h):
        if 0 < len(self.active) <= self.detail_rows and (h - 2 * y) / len(self.active) >= 60:
            self._draw_detailed(d, left, y, w, h)
            return
        row_h = int(self.options.get("row_height", 28))
        name_f = t.font(round(row_h * 0.64), self.name_weight)
        tok_f = t.font(round(row_h * 0.57))
        rows = int((h - 2 * y + 6) // row_h)
        if not self.active:
            d.text((left, h / 2 - 12), "no active agents", font=t.font(20), fill=t.MUTED)
            return
        cap = int(self.options.get("max_rows", rows))  # e.g. 5: at most 5 sessions, then "+N more"
        if len(self.active) <= min(cap, rows):
            shown = self.active
        else:
            shown = self.active[:min(cap, rows - 1)]
        status_right = w - t.PAD
        age_w = d.textlength("59m", font=name_f)
        tok_right = status_right - age_w - 12
        tokens = self.options.get("tokens", True)  # false: no per-session token column
        rings = self.options.get("cache_ring", False)
        ring_x = tok_right - (d.textlength("99.9M", font=tok_f) + 18 if tokens else 0)
        name_right = (ring_x - 16 if rings else tok_right - (d.textlength("99.9M", font=tok_f) if tokens else 0)) - 10
        for i, s in enumerate(shown):
            ry = y + i * row_h
            t.icon(d, left, ry + 2, s.tool, 22)
            self._status(d, status_right, ry, s, name_f)
            if tokens:
                t.text_right(d, tok_right, ry + 2, t.human_count(s.today), tok_f, t.MUTED)
            if rings:
                t.ring(d, ring_x, ry + 12, 9, 3, s.cache_hit, t.cache_color(s.cache_hit))
            text_x = left + 30
            name_color = self._context_color(s) if self.options.get("context_colors", False) else t.TEXT
            d.text((text_x, ry), t.fit_text(d, s.label, name_f, name_right - text_x), font=name_f, fill=name_color)
        if len(shown) < len(self.active):
            more = f"+{len(self.active) - len(shown)} more"
            if self.options.get("total", False):
                more += f" · {len(self.active)} agents"
            ry = y + len(shown) * row_h
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
        for tool in ("codex", "claude"):
            count = sum(s.tool == tool for s in self.active)
            if not count:
                continue
            t.text_right(d, x, y, str(count), f, t.TEXT)
            x -= d.textlength(str(count), font=f) + 26
            t.icon(d, x, y + 2, tool, 20)
            x -= 16
