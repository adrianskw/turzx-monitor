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
CODEX_INDEX = Path.home() / ".codex/session_index.jsonl"  # {"id", "thread_name"} per named thread
CLAUDE_REGISTRY = Path.home() / ".claude/sessions"  # <pid>.json per running Claude Code: pid, sessionId
PROC = Path("/proc")
LIVE_GRACE = 60.0  # seconds a session with no live process stays listed (a run just ending)
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
        self.mtime = 0.0  # last activity: the newest prompt, reply or tool record (else file mtime)
        self.file_mtime = 0.0  # last write of any kind, housekeeping included
        self.activity = 0.0
        self.today = 0  # fresh tokens since local midnight
        self.cache_read = 0  # input tokens served from cache, today
        self.input_total = 0  # all input tokens (fresh + cache writes + cache reads), today
        self.context = 0  # current context size: all input tokens of the latest turn
        self.window: int | None = None  # context window, when the log states it (Codex)
        self.turn_start = 0.0  # when the latest prompt was sent: the running job's start
        self.model = ""  # latest model id, e.g. "claude-opus-5-5" / "gpt-6-sol"
        self.effort = ""  # latest reasoning effort: low / medium / high / xhigh / max
        self.title = ""  # name given with /rename (Claude custom-title, Codex thread name)
        self.turn_open = False  # a turn has started and not yet ended (or been interrupted)
        self.pending: dict[str, str] = {}  # tool calls with no result yet: call id -> tool name
        self.codex_totals: tuple[int, int, int] | None = None  # cumulative input, cached input, output
        self.cleared_at: float | None = None  # Claude: when /clear started this log (replacing another)
        self.subagent = False  # Codex: a thread spawned by another (e.g. a guardian review)

    @property
    def label(self) -> str:
        """The session's name: its /rename title, else its working directory's name."""
        return self.title or Path(self.cwd).name or "?"

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


def _codex_delta(s: Session, entry: dict, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Count the change in Codex's cumulative usage, ignoring repeated reports."""
    payload = entry.get("payload") or {}
    info = payload.get("info") if payload.get("type") == "token_count" else None
    total = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(total, dict):
        return fallback
    values = tuple(total.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens"))
    if any(type(value) is not int or value < 0 for value in values) or values[1] > values[0]:
        return fallback
    previous = s.codex_totals
    s.codex_totals = values
    if previous is None or any(value < old for value, old in zip(values, previous)):
        # The first visible report may follow a truncated log; use its per-update
        # usage instead of attributing the entire historical total to this event.
        return fallback
    inp, read, output = (value - old for value, old in zip(values, previous))
    return inp - read + output, read, inp


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


INTERRUPTED = "[Request interrupted by user"
CLEAR = "<command-name>/clear</command-name>"
CODEX_HOUSEKEEPING = {"thread_settings_applied"}
CLEAR_SLACK = 10.0  # seconds between /clear and the replaced log's last write


def _turn_update(s: "Session", tool: str, entry: dict, prompt: bool) -> None:
    """Track whether a turn is open and which tool calls await results, so a session stopped
    at a permission prompt or a question can be told apart from one that finished."""
    kind = entry.get("type")
    if tool == "codex":
        payload = entry.get("payload") or {}
        ptype = payload.get("type")
        if prompt:
            s.turn_open, s.pending = True, {}
        elif kind == "event_msg" and ptype in ("task_complete", "turn_aborted"):
            s.turn_open, s.pending = False, {}
        elif kind == "response_item" and ptype in ("function_call", "custom_tool_call"):
            s.pending[str(payload.get("call_id"))] = str(payload.get("name") or "")
        elif kind == "response_item" and ptype in ("function_call_output", "custom_tool_call_output"):
            s.pending.pop(str(payload.get("call_id")), None)
        return
    if entry.get("isSidechain"):
        return  # subagent traffic; the main thread's Task call stays pending meanwhile
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = msg.get("content")
    blocks = [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []
    text = content if isinstance(content, str) else " ".join(str(c.get("text", "")) for c in blocks)
    if kind == "user" and text.startswith(INTERRUPTED):
        s.turn_open, s.pending = False, {}
    elif prompt:
        s.turn_open, s.pending = True, {}
    elif kind == "assistant":
        for c in blocks:
            if c.get("type") == "tool_use":
                s.pending[str(c.get("id"))] = str(c.get("name") or "")
        if msg.get("stop_reason") == "end_turn":
            s.turn_open, s.pending = False, {}
    elif kind == "user":
        for c in blocks:
            if c.get("type") == "tool_result":
                s.pending.pop(str(c.get("tool_use_id")), None)
    elif kind == "system" and entry.get("subtype") == "turn_duration":
        s.turn_open, s.pending = False, {}


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


def _housekeeping(tool: str, entry: dict) -> bool:
    """Records written without any work happening: e.g. opening a Codex thread in the app
    re-applies its settings. (Claude's title, mode and cost records carry no timestamp.)"""
    if tool == "codex":
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        return entry.get("type") == "session_meta" or payload.get("type") in CODEX_HOUSEKEEPING
    return False


def _is_clear(entry: dict) -> bool:
    """A Claude `/clear`: logged as the first command of the new session's log."""
    content = (entry.get("message") or {}).get("content") if entry.get("type") == "user" else None
    return isinstance(content, str) and CLEAR in content


def _proc_start(pid: int) -> str | None:
    """A process's start time (clock ticks since boot), or None when it isn't running."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        return stat[stat.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def _live_claude() -> set[str] | None:
    """Session ids of running Claude Code processes (their registry, checked against /proc so a
    crashed process or a reused pid doesn't count), or None when there's no registry to go by."""
    if not CLAUDE_REGISTRY.is_dir() or not PROC.is_dir():
        return None
    ids = set()
    for path in CLAUDE_REGISTRY.glob("*.json"):
        try:
            info = json.loads(path.read_text())
            start = _proc_start(int(info["pid"]))
            if start is not None and str(info.get("procStart", start)) == start:
                ids.add(str(info["sessionId"]))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return ids


def _open_codex() -> set[str] | None:
    """Rollout logs held open by a running Codex (CLI or the ChatGPT app keeps each live
    thread's log open), or None without /proc."""
    if not PROC.is_dir():
        return None
    paths = set()
    for proc in PROC.iterdir():
        try:
            if not proc.name.isdigit() or not (proc / "comm").read_text().startswith("codex"):
                continue
            for fd in (proc / "fd").iterdir():
                target = os.readlink(fd)
                if target.endswith(".jsonl") and "/rollout-" in target:
                    paths.add(target)
        except OSError:
            continue
    return paths


class TokenLog:
    def __init__(self):
        self.sessions: dict[Path, Session] = {}
        self.events: deque[tuple[float, int, int, int, Path]] = deque()  # (ts, fresh, cache_read, input_total, path)
        self.day = None
        self._lock = threading.Lock()
        self._refreshed = 0.0
        self._codex_names: dict[str, str] = {}
        self._codex_index = None  # (mtime_ns, size) of CODEX_INDEX when last read

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
                    s.model = s.effort = s.title = ""
                    s.turn_open, s.pending = False, {}
                    s.codex_totals = None
                    s.cleared_at = None
                    s.subagent = False
                    s.activity = 0.0
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
        s.file_mtime = stat.st_mtime
        s.mtime = s.activity or s.file_mtime
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
                if not isinstance(cwd, str):
                    cwd = ""
                ctx, window = _context(s.tool, entry)
                fresh, read, inp = _tokens(s.tool, entry)
                has_usage = bool(fresh or inp)
                ts = _ts(entry["timestamp"]) if has_usage and "timestamp" in entry else None
                prompt = _ts(entry["timestamp"]) if "timestamp" in entry and _is_prompt(s.tool, entry) else None
                model = _model(s.tool, entry)
                effort = _effort(s.tool, entry)
                title = entry.get("customTitle") if entry.get("type") == "custom-title" else None
                cleared = _ts(entry["timestamp"]) if s.tool == "claude" and _is_clear(entry) else None
                active = _ts(entry["timestamp"]) if "timestamp" in entry and not _housekeeping(s.tool, entry) else None
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError, UnicodeError):
                # A damaged record must not hide valid records later in the file.
                continue
            if s.tool == "codex" and ts is not None:
                fresh, read, inp = _codex_delta(s, entry, (fresh, read, inp))
            if not s.cwd:
                s.cwd = cwd
            if ctx:
                s.context = ctx  # latest turn wins: the current context size
            if window:
                s.window = window
            if prompt is not None:
                s.turn_start = prompt
            try:
                _turn_update(s, s.tool, entry, prompt is not None)
            except (TypeError, AttributeError, ValueError):
                pass  # a malformed record only loses its turn-state hint
            if model:
                s.model = model
            if effort:
                s.effort = effort
            if s.tool == "codex" and entry.get("type") == "session_meta":
                meta = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                source = meta.get("source")
                s.subagent = bool(meta.get("parent_thread_id")) or (isinstance(source, dict) and "subagent" in source)
            if cleared is not None:
                s.cleared_at = cleared
            if active is not None:
                s.activity = max(s.activity, active)
            if isinstance(title, str):
                s.title = title.strip()  # latest rename wins; an empty one clears it
            if ts is not None and (fresh or read or inp):
                if ts >= midnight:
                    s.today += fresh
                    s.cache_read += read
                    s.input_total += inp
                if ts >= event_cutoff:
                    self.events.append((ts, fresh, read, inp, s.path))
        s.mtime = s.activity or s.file_mtime
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
            self._name_codex()

    def _name_codex(self) -> None:
        """Codex keeps thread names outside the rollout logs, in one small index file."""
        try:
            stat = CODEX_INDEX.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            if key != self._codex_index:
                names = {}
                with CODEX_INDEX.open(errors="replace") as index:
                    for line in index:
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                                names[entry["id"]] = str(entry.get("thread_name") or "").strip()
                        except ValueError:
                            continue
                self._codex_names, self._codex_index = names, key
        except OSError:
            self._codex_names, self._codex_index = {}, None
        for s in self.sessions.values():
            if s.tool == "codex":  # rollout-<date>T<time>-<thread id>.jsonl
                s.title = self._codex_names.get(s.path.stem[-36:], "")

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
        """Open sessions written to in the last `minutes`, newest first. Left out: sessions
        whose process has exited, Codex subagent threads, and a Claude log that a /clear
        replaced (its last write is the /clear itself, in the same project folder)."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            live = [s for s in self.sessions.values() if s.mtime >= cutoff and not s.subagent]
        # Sessions whose process has exited drop out (after a short grace), not after `minutes`.
        claude, codex, now = _live_claude(), _open_codex(), time.time()
        live = [s for s in live if now - s.mtime < LIVE_GRACE or (
            (claude is None or s.path.stem in claude) if s.tool == "claude" else
            (codex is None or str(s.path) in codex))]
        with self._lock:
            clears = [c for c in live if c.cleared_at is not None]
            live = [s for s in live if not (s.tool == "claude" and any(
                c is not s and c.path.parent == s.path.parent and abs(s.file_mtime - c.cleared_at) <= CLEAR_SLACK
                for c in clears))]
            return sorted(live, key=lambda s: -s.mtime)


_shared: TokenLog | None = None
_shared_lock = threading.Lock()


def shared() -> TokenLog:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = TokenLog()
        return _shared
