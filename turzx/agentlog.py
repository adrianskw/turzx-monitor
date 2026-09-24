"""Token accounting from local Claude Code / Codex session logs, shared by widgets.

Claude Code: ~/.claude/projects/<project>/<session>.jsonl, one entry per message;
assistant entries carry message.usage.
Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl; session_meta has the cwd,
event_msg/token_count entries carry last_token_usage.

"Burn" counts fresh tokens: input + output (+ cache writes), excluding cache reads,
which dominate raw totals but are cheap re-reads of the same context.

"Cache hit" is the share of input tokens served from the prompt cache:
cache reads / all input (fresh input + cache writes + cache reads). High is good:
cheap, fast turns. Low means context keeps being rebuilt.

One tracker instance (`shared()`) is used by every widget, so each log is parsed
once no matter how many widgets show token data.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude/projects"
CODEX_DIR = Path.home() / ".codex/sessions"
MAX_RATE_MINUTES = 24 * 60
EVENT_SECONDS = MAX_RATE_MINUTES * 60
FINGERPRINT_BYTES = 256


class Session:
    def __init__(self, tool: str, path: Path):
        self.tool = tool
        self.path = path
        self.offset = 0
        self.identity: tuple[int, int] | None = None
        self.size = 0
        self.mtime_ns = 0
        self.ctime_ns = 0
        self.head = b""  # first committed bytes, to detect same-inode rewrites
        self.tail = b""  # last committed bytes, to detect a rewritten log of equal size
        self.cwd = ""
        self.mtime = 0.0
        self.today = 0  # fresh tokens since local midnight
        self.cache_read = 0  # input tokens served from cache, today
        self.input_total = 0  # all input tokens (fresh + cache writes + cache reads), today
        self.context = 0  # current context size: all input tokens of the latest turn
        self.window: int | None = None  # context window, when the log states it (Codex)
        self.turn_start = 0.0  # when the latest prompt was sent: the running job's start
        self.model = ""  # latest model id, e.g. "claude-opus-5-5" / "gpt-6-sol"
        self.effort = ""  # latest reasoning effort: low / medium / high / xhigh / max

    @property
    def cache_hit(self) -> float | None:
        return 100 * self.cache_read / self.input_total if self.input_total else None


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _tokens(tool: str, entry: dict) -> tuple[int, int, int]:
    """(fresh, cache_read, input_total) for one log entry; zeros if it has no usage."""
    if tool == "claude":
        msg = entry.get("message")
        u = msg.get("usage") if isinstance(msg, dict) else None
        if not u:
            return 0, 0, 0
        inp, write, read = u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0)
        return inp + u.get("output_tokens", 0) + write, read, inp + write + read
    payload = entry.get("payload") or {}
    if payload.get("type") != "token_count" or not payload.get("info"):
        return 0, 0, 0
    u = payload["info"].get("last_token_usage") or {}
    inp, cached = u.get("input_tokens", 0), u.get("cached_input_tokens", 0)  # Codex input includes cached
    return max(0, inp - cached) + u.get("output_tokens", 0), cached, inp


def _context(tool: str, entry: dict) -> tuple[int, int | None]:
    """(context tokens of this turn, window size if logged); zeros if no usage here."""
    if tool == "claude":
        msg = entry.get("message")
        u = msg.get("usage") if isinstance(msg, dict) else None
        if not u:
            return 0, None
        return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0), None
    payload = entry.get("payload") or {}
    info = payload.get("info") if payload.get("type") == "token_count" else None
    if not info:
        return 0, None
    return (info.get("last_token_usage") or {}).get("input_tokens", 0), info.get("model_context_window")


def _model(tool: str, entry: dict) -> str:
    """Model id named by this entry, or "": Claude assistant messages, Codex turn_context."""
    if tool == "codex":
        payload = entry.get("payload") or {}
        model = payload.get("model") if entry.get("type") == "turn_context" else ""
        return model if isinstance(model, str) else ""
    msg = entry.get("message")
    model = msg.get("model", "") if isinstance(msg, dict) and entry.get("type") == "assistant" else ""
    return model if isinstance(model, str) and not model.startswith("<") else ""


def _effort(tool: str, entry: dict) -> str:
    """Reasoning effort named by this entry, or "": top-level on Claude assistant entries,
    in turn_context for Codex."""
    if tool == "codex":
        payload = entry.get("payload") or {}
        return str(payload.get("effort") or "") if entry.get("type") == "turn_context" else ""
    return str(entry.get("effort") or "") if entry.get("type") == "assistant" else ""


def _is_prompt(tool: str, entry: dict) -> bool:
    """True for the entry that starts a turn: a typed prompt (Claude) or task_started (Codex)."""
    if tool == "codex":
        return entry.get("type") == "event_msg" and (entry.get("payload") or {}).get("type") == "task_started"
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "text" for c in content)


class TokenLog:
    def __init__(self):
        self.sessions: dict[Path, Session] = {}
        self.events: deque[tuple[float, int, int, int, Path]] = deque()  # (ts, fresh, cache_read, input_total, path)
        self.day = None
        self._lock = threading.Lock()
        self._refreshed = 0.0

    def _candidates(self, cutoff: float):
        for tool, root, pattern in (("claude", CLAUDE_DIR, "*/*.jsonl"), ("codex", CODEX_DIR, "*/*/*/*.jsonl")):
            for path in root.glob(pattern):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime >= cutoff:
                    yield tool, path, stat

    def _read_new(self, s: Session, midnight: float) -> bool:
        try:
            with s.path.open("rb") as f:
                stat = os.fstat(f.fileno())
                identity = (stat.st_dev, stat.st_ino)
                replaced = s.identity is not None and (
                    identity != s.identity or stat.st_size < s.offset or
                    (stat.st_size == s.size and stat.st_ctime_ns != s.ctime_ns))
                if s.identity is not None and not replaced and s.offset:
                    f.seek(0)
                    replaced = f.read(len(s.head)) != s.head
                    if not replaced:
                        f.seek(s.offset - len(s.tail))
                        replaced = f.read(len(s.tail)) != s.tail
                if replaced:
                    # A replaced/truncated log is a new session, even at the same path.
                    self.events = deque(e for e in self.events if e[4] != s.path)
                    s.offset = s.today = s.cache_read = s.input_total = s.context = 0
                    s.window = None
                    s.cwd = ""
                    s.turn_start = 0.0
                    s.model = s.effort = ""
                f.seek(s.offset)
                chunk = f.read()
                end = chunk.rfind(b"\n")
                if end >= 0:
                    committed = s.offset + end + 1
                    f.seek(0)
                    head = f.read(min(committed, FINGERPRINT_BYTES))
                    f.seek(max(0, committed - FINGERPRINT_BYTES))
                    tail = f.read(min(committed, FINGERPRINT_BYTES))
        except OSError:
            return False
        s.identity = identity
        s.size = stat.st_size
        s.mtime_ns = stat.st_mtime_ns
        s.ctime_ns = stat.st_ctime_ns
        s.mtime = stat.st_mtime
        if end < 0:
            return True
        s.offset += end + 1
        s.head, s.tail = head, tail
        event_cutoff = time.time() - EVENT_SECONDS
        for line in chunk[:end].splitlines():
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                cwd = entry.get("cwd") or (entry.get("payload") or {}).get("cwd") or ""
                ctx, window = _context(s.tool, entry)
                fresh, read, inp = _tokens(s.tool, entry)
                ts = _ts(entry["timestamp"]) if (fresh or inp) and "timestamp" in entry else None
                prompt = _ts(entry["timestamp"]) if "timestamp" in entry and _is_prompt(s.tool, entry) else None
                model = _model(s.tool, entry)
                effort = _effort(s.tool, entry)
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError, UnicodeError):
                # A damaged record must not hide valid records later in the file.
                continue
            if not s.cwd:
                s.cwd = cwd
            if ctx:
                s.context = ctx  # latest turn wins: the current context size
            if window:
                s.window = window
            if prompt is not None:
                s.turn_start = prompt
            if model:
                s.model = model
            if effort:
                s.effort = effort
            if ts is not None:
                if ts >= midnight:
                    s.today += fresh
                    s.cache_read += read
                    s.input_total += inp
                if ts >= event_cutoff:
                    self.events.append((ts, fresh, read, inp, s.path))
        return True

    def refresh(self, max_age: float = 2.0) -> None:
        """Read new log lines. Cheap to call from several widgets: skips if refreshed recently."""
        with self._lock:
            now = time.time()
            if 0 <= now - self._refreshed < max_age:
                return
            self._refreshed = now
            today = datetime.now().date()
            midnight = datetime.combine(today, datetime.min.time()).timestamp()
            if today != self.day:  # new day: start counting from scratch
                self.day, self.sessions, self.events = today, {}, deque()
            for tool, path, stat in self._candidates(min(midnight, now - EVENT_SECONDS)):
                s = self.sessions.get(path)
                if s is None:
                    s = self.sessions[path] = Session(tool, path)
                if (stat.st_mtime_ns != s.mtime_ns or stat.st_ctime_ns != s.ctime_ns or stat.st_size != s.size
                        or (stat.st_dev, stat.st_ino) != s.identity):
                    self._read_new(s, midnight)
            self.events = deque(e for e in self.events if e[0] >= now - EVENT_SECONDS)

    def rate(self, minutes: float) -> float:
        """Rolling burn: fresh tokens per minute over the last `minutes`."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            return sum(e[1] for e in self.events if e[0] >= cutoff) / minutes

    def cache_hit(self, minutes: float) -> float | None:
        """Rolling cache hit % over the last `minutes` (None if there was no input)."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            read = sum(e[2] for e in self.events if e[0] >= cutoff)
            inp = sum(e[3] for e in self.events if e[0] >= cutoff)
        return 100 * read / inp if inp else None

    def total_today(self) -> int:
        with self._lock:
            return sum(s.today for s in self.sessions.values())

    def active(self, minutes: float) -> list[Session]:
        cutoff = time.time() - minutes * 60
        with self._lock:
            return sorted((s for s in self.sessions.values() if s.mtime >= cutoff), key=lambda s: -s.mtime)


_shared: TokenLog | None = None
_shared_lock = threading.Lock()


def shared() -> TokenLog:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = TokenLog()
        return _shared
