import os
import sqlite3
import tempfile
from contextlib import closing
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from turzx import agentlog


def varint(n):
    out = bytearray()
    while True:
        out.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            return bytes(out)


def pb(*fields):
    """Encode (number, value) pairs: ints as varints, bytes as length-delimited."""
    out = b""
    for number, value in fields:
        if isinstance(value, int):
            out += varint(number << 3) + varint(value)
        else:
            out += varint(number << 3 | 2) + varint(len(value)) + value
    return out


def generation(used, window):
    """A gen_metadata row shaped like agy's: an unrelated field, then the generation with
    its usage (4) and context (9.10: parts in 3, in use 1, window 4)."""
    parts = pb((1, pb((1, b"system"), (4, 5420))), (1, pb((1, b"tools"), (4, used - 5420))), (2, used))
    return pb((2, b"xx"), (1, pb((3, 1016), (4, pb((1, 1016), (2, 3587))),
                                 (9, pb((1, 23), (10, pb((1, used), (3, parts), (4, window))))))))


class AgySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.agy = self.tmp / "antigravity-cli"
        for sub in ("conversations", "presence", "annotations"):
            (self.agy / sub).mkdir(parents=True)
        (self.agy / "settings.json").write_text('{"model": "Gemini 3.1 Pro (High)"}')
        self.proc = self.tmp / "proc"
        self.project = self.tmp / "pipeline-v3"
        self.project.mkdir()

    def run_agy(self, pid, conv, args=("agy",)):
        (self.agy / "conversations" / f"{conv}.db").write_bytes(b"")
        lock = self.agy / "presence" / f"{conv}.lock"
        lock.write_bytes(b"")
        p = self.proc / str(pid)
        (p / "fd").mkdir(parents=True)
        (p / "comm").write_text("agy\n")
        (p / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
        os.symlink(self.project, p / "cwd")
        os.symlink(lock, p / "fd" / "7")

    def active(self):
        with patch.object(agentlog, "PROC", self.proc), patch.object(agentlog, "AGY_DIR", self.agy), \
                patch.object(agentlog, "CLAUDE_DIR", self.tmp / "none"), patch.object(agentlog, "CODEX_DIR", self.tmp / "none"):
            log = agentlog.TokenLog()
            log.refresh()
            return log.active(30)

    def test_model_name_and_flags(self):
        self.assertEqual(agentlog._agy_model("Gemini 3.1 Pro (High)"), ("gemini-3.1-pro", "high"))
        self.assertEqual(agentlog._agy_model("Gemini 3 Flash"), ("gemini-3-flash", ""))
        self.assertEqual(agentlog._agy_model("gemini-3.8-flash-high"), ("gemini-3.8-flash", "high"))
        self.assertEqual(agentlog._agy_model("gpt-oss-120b-medium"), ("gpt-oss-120b", "medium"))
        self.assertEqual(agentlog._agy_model("Claude Opus 4.6 (Thinking)"), ("claude-opus-4.6", "thinking"))
        self.assertEqual(agentlog._flag(["agy", "--effort=low"], "--effort"), "low")
        self.assertEqual(agentlog._flag(["agy", "--model", "Gemini 3 Flash"], "--model"), "Gemini 3 Flash")

    def test_running_session_is_listed_with_directory_model_and_title(self):
        self.run_agy(4242, "a38b", args=("agy", "--effort", "max"))
        (self.agy / "annotations" / "a38b.pbtxt").write_text('title:"Repository Code Audit"\n')
        sessions = self.active()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual((s.tool, s.label, s.model, s.effort), ("agy", "Repository Code Audit", "gemini-3.1-pro", "max"))
        self.assertEqual(Path(s.cwd), self.project)
        self.assertAlmostEqual(s.mtime, time.time(), delta=5)

    def test_context_comes_from_the_newest_generation(self):
        self.run_agy(4242, "a38b")
        db = self.agy / "conversations" / "a38b.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE gen_metadata (idx integer PRIMARY KEY, data blob, size integer)")
            conn.execute("INSERT INTO gen_metadata VALUES (0, ?, 0)", (generation(50_000, 128_000),))
            conn.execute("INSERT INTO gen_metadata VALUES (1, ?, 0)", (generation(96_853, 128_000),))
        s = self.active()[0]
        self.assertEqual((s.context, s.window), (96_853, 128_000))

    def test_unreadable_generation_leaves_context_unknown(self):
        self.run_agy(4242, "a38b")
        with closing(sqlite3.connect(self.agy / "conversations" / "a38b.db")) as conn, conn:
            conn.execute("CREATE TABLE gen_metadata (idx integer PRIMARY KEY, data blob, size integer)")
            conn.execute("INSERT INTO gen_metadata VALUES (0, ?, 0)", (b"\xff\xff",))
        s = self.active()[0]
        self.assertEqual((s.context, s.window), (0, None))

    def test_closed_conversation_is_not_listed(self):
        (self.agy / "conversations" / "old.db").write_bytes(b"")
        self.proc.mkdir()
        self.assertEqual(self.active(), [])


if __name__ == "__main__":
    unittest.main()
