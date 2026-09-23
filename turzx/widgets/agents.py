"""Active Claude Code / Codex sessions and token burn, read from their local session logs.

Claude Code: ~/.claude/projects/<project>/<session>.jsonl, one entry per message;
assistant entries carry message.usage.
Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl; session_meta has the cwd,
event_msg/token_count entries carry last_token_usage.

"Burn" counts fresh tokens: input + output (+ cache writes), excluding cache reads,
which dominate raw totals but are cheap re-reads of the same context.
"""

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from turzx import theme as t
from turzx.widget import Widget, register

CLAUDE_DIR = Path.home() / ".claude/projects"
CODEX_DIR = Path.home() / ".codex/sessions"


class Session:
    def __init__(self, tool: str, path: Path):
        self.tool = tool
        self.path = path
        self.offset = 0
        self.cwd = ""
        self.mtime = 0.0
        self.today = 0  # fresh tokens since local midnight


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@register("agents")
class Agents(Widget):
    interval = 5.0

    def __init__(self, **options):
        super().__init__(**options)
        self.active_minutes = float(options.get("active_minutes", 30))
        self.working_seconds = float(options.get("working_seconds", 60))
        self.sessions: dict[Path, Session] = {}
        self.events: deque[tuple[float, int]] = deque()  # (timestamp, tokens), last hour
        self.day = None
        self.rate = 0.0
        self.total_today = 0
        self.active: list[Session] = []

    # ---- log parsing -------------------------------------------------------
    def _candidates(self, midnight: float):
        for tool, root, pattern in (("claude", CLAUDE_DIR, "*/*.jsonl"), ("codex", CODEX_DIR, "*/*/*/*.jsonl")):
            for path in root.glob(pattern):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= midnight:
                    yield tool, path, mtime

    def _tokens(self, tool: str, entry: dict) -> int:
        if tool == "claude":
            msg = entry.get("message")
            u = msg.get("usage") if isinstance(msg, dict) else None
            if not u:
                return 0
            return (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0))
        payload = entry.get("payload") or {}
        if payload.get("type") != "token_count" or not payload.get("info"):
            return 0
        u = payload["info"].get("last_token_usage") or {}
        return max(0, u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)) + u.get("output_tokens", 0)

    def _read_new(self, s: Session, midnight: float) -> None:
        try:
            with s.path.open("rb") as f:
                f.seek(s.offset)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        s.offset += end + 1
        for line in chunk[:end].splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not s.cwd:
                s.cwd = entry.get("cwd") or (entry.get("payload") or {}).get("cwd") or ""
            n = self._tokens(s.tool, entry)
            if n and "timestamp" in entry:
                ts = _ts(entry["timestamp"])
                if ts >= midnight:
                    s.today += n
                    if ts >= time.time() - 3600:
                        self.events.append((ts, n))

    def update(self):
        now = time.time()
        today = datetime.now().date()
        midnight = datetime.combine(today, datetime.min.time()).timestamp()
        if today != self.day:  # new day: start counting from scratch
            self.day, self.sessions, self.events = today, {}, deque()
        for tool, path, mtime in self._candidates(midnight):
            s = self.sessions.get(path)
            if s is None:
                s = self.sessions[path] = Session(tool, path)
            if mtime != s.mtime:
                s.mtime = mtime
                self._read_new(s, midnight)
        while self.events and self.events[0][0] < now - 3600:
            self.events.popleft()
        window = float(self.options.get("rate_minutes", 5))
        recent = sum(n for ts, n in self.events if ts >= now - window * 60)
        self.rate = recent / window
        self.total_today = sum(s.today for s in self.sessions.values())
        cutoff = now - self.active_minutes * 60
        self.active = sorted((s for s in self.sessions.values() if s.mtime >= cutoff), key=lambda s: -s.mtime)

    # ---- drawing -----------------------------------------------------------
    def draw(self, d, w, h):
        y = t.card(d, w, h)
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
        now = time.time()
        if not self.active:
            d.text((t.PAD, y + 50), "no active agents", font=f, fill=t.MUTED)
            return
        shown = self.active[:rows] if len(self.active) <= rows else self.active[:rows - 1]
        for i, s in enumerate(shown):
            ry = y + 48 + i * 32
            t.icon(d, t.PAD, int(ry), s.tool, 24)
            idle = now - s.mtime
            if idle < self.working_seconds:
                state, color = "working", t.GREEN
            else:
                state, color = t.duration_text(idle), t.MUTED
            state_w = d.textlength("working", font=f)
            t.text_right(d, w - t.PAD, ry, state, f, color)
            tokens = t.human_count(s.today)
            tok_right = w - t.PAD - state_w - 12
            t.text_right(d, tok_right, ry, tokens, f, t.MUTED)
            name_room = tok_right - d.textlength("999.9M", font=f) - 10 - (t.PAD + 32)
            d.text((t.PAD + 32, ry), t.fit_text(d, Path(s.cwd).name or "?", f, name_room), font=f, fill=t.TEXT)
        if len(shown) < len(self.active):
            d.text((t.PAD + 32, y + 48 + len(shown) * 32), f"+{len(self.active) - len(shown)} more",
                   font=f, fill=t.MUTED)
