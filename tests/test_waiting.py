import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from turzx.agentlog import Session, _turn_update
from turzx import theme as t
from turzx.sample import SAMPLE_NOW, populate
from turzx.widgets.agents import Agents


def claude(kind, content, **extra):
    return {"type": kind, "message": {"content": content, **extra}}


class TurnStateTests(unittest.TestCase):
    def feed(self, tool, entries):
        s = Session(tool, Path("x.jsonl"))
        for entry, prompt in entries:
            _turn_update(s, tool, entry, prompt)
        return s

    def test_claude_pending_tool_then_result_then_end(self):
        use = claude("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash"}], stop_reason="tool_use")
        s = self.feed("claude", [(claude("user", "go"), True), (use, False)])
        self.assertTrue(s.turn_open)
        self.assertEqual(s.pending, {"t1": "Bash"})
        _turn_update(s, "claude", claude("user", [{"type": "tool_result", "tool_use_id": "t1"}]), False)
        self.assertEqual(s.pending, {})
        _turn_update(s, "claude", claude("assistant", [{"type": "text", "text": "done"}], stop_reason="end_turn"), False)
        self.assertFalse(s.turn_open)

    def test_claude_interrupt_closes_the_turn(self):
        use = claude("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash"}])
        stop = claude("user", [{"type": "text", "text": "[Request interrupted by user for tool use]"}])
        s = self.feed("claude", [(claude("user", "go"), True), (use, False), (stop, True)])
        self.assertFalse(s.turn_open)
        self.assertEqual(s.pending, {})

    def test_codex_call_and_completion(self):
        call = {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec"}}
        done = {"type": "event_msg", "payload": {"type": "task_complete"}}
        s = self.feed("codex", [({"type": "event_msg", "payload": {"type": "task_started"}}, True), (call, False)])
        self.assertEqual(s.pending, {"c1": "exec"})
        _turn_update(s, "codex", done, False)
        self.assertFalse(s.turn_open)


class ClearTests(unittest.TestCase):
    def test_clear_hides_the_replaced_claude_session(self):
        import tempfile, time
        from turzx.agentlog import TokenLog
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            proj, other = Path(tmp, "proj"), Path(tmp, "other")
            proj.mkdir(), other.mkdir()
            stamp = datetime.fromtimestamp(now - 100, timezone.utc).isoformat()
            clear = {"type": "user", "timestamp": stamp,
                     "message": {"content": "<command-name>/clear</command-name>"}}
            files = {"old": (proj, now - 100), "new": (proj, now - 5), "side": (other, now - 100),
                     "earlier": (proj, now - 400)}
            log = TokenLog()
            for name, (folder, mtime) in files.items():
                path = folder / f"{name}.jsonl"
                path.write_text(json.dumps(clear) + "\n" if name == "new" else "")
                os.utime(path, (mtime, mtime))
                log.sessions[path] = s = Session("claude", path)
                log._read_new(s, 0)
            with patch("turzx.agentlog._live_claude", return_value=None):
                names = {s.path.stem for s in log.active(30)}
        self.assertEqual(names, {"new", "side", "earlier"})

    def test_exited_sessions_and_codex_subagents_drop_out(self):
        import tempfile, time
        from turzx.agentlog import TokenLog
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            log = TokenLog()
            for name, tool, idle in (("open", "claude", 600), ("closed", "claude", 600), ("fresh", "claude", 10),
                                     ("thread", "codex", 600), ("gone", "codex", 600), ("guardian", "codex", 5)):
                path = Path(tmp, f"{name}.jsonl")
                meta = {"type": "session_meta", "payload": {"parent_thread_id": "x"}} if name == "guardian" else {}
                path.write_text(json.dumps(meta) + "\n")
                os.utime(path, (now - idle, now - idle))
                log.sessions[path] = s = Session(tool, path)
                log._read_new(s, 0)
            with patch("turzx.agentlog._live_claude", return_value={"open"}), \
                    patch("turzx.agentlog._open_codex", return_value={str(Path(tmp, "thread.jsonl"))}):
                names = {s.path.stem for s in log.active(30)}
        self.assertEqual(names, {"open", "fresh", "thread"})


class ActivityTests(unittest.TestCase):
    def test_reopening_a_codex_thread_is_not_activity(self):
        import tempfile, time
        from turzx.agentlog import TokenLog
        now = time.time()
        stamp = lambda ago: datetime.fromtimestamp(now - ago, timezone.utc).isoformat()
        entries = [{"timestamp": stamp(7200), "type": "event_msg", "payload": {"type": "task_complete"}},
                   {"timestamp": stamp(5), "type": "event_msg", "payload": {"type": "thread_settings_applied"}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "rollout.jsonl")
            path.write_text("".join(json.dumps(e) + "\n" for e in entries))
            log = TokenLog()
            log.sessions[path] = s = Session("codex", path)
            log._read_new(s, 0)
        self.assertAlmostEqual(s.mtime, now - 7200, delta=1)
        self.assertGreater(s.file_mtime, now - 60)


class SingleRowTests(unittest.TestCase):
    def test_spread_rows_with_context_bars_draw(self):
        for n in (5, 6, 9):
            with self.subTest(sessions=n):
                widget = Agents(stats="none", rows="single", row_height=32, max_rows=6, detail_rows=4,
                                spread=True, context_bar=True, summary=True, waiting=True, status="dot")
                populate(widget)
                widget.active = (widget.active * 3)[:n]
                with patch.object(t, "bar", wraps=t.bar) as bar:
                    widget.draw(ImageDraw.Draw(Image.new("RGB", (485, 260))), 485, 260)
                self.assertEqual(bar.call_count, min(n, 6))


class WaitingTests(unittest.TestCase):
    def session(self, idle, names):
        widget = Agents(stats="none", rows="single", waiting=True)
        widget.preview_now = 1000.0
        s = Session("claude", Path("x.jsonl"))
        s.mtime, s.turn_open = 1000.0 - idle, True
        s.pending = {str(i): n for i, n in enumerate(names)}
        return widget, s

    def test_question_waits_at_once_and_bash_after_silence(self):
        widget, s = self.session(5, ["AskUserQuestion"])
        self.assertTrue(widget._waiting(s))
        widget, s = self.session(5, ["Bash"])
        self.assertFalse(widget._waiting(s))
        widget, s = self.session(120, ["Bash"])
        self.assertTrue(widget._waiting(s))

    def test_subagents_and_closed_turns_are_not_waiting(self):
        widget, s = self.session(600, ["Task"])
        self.assertFalse(widget._waiting(s))
        widget, s = self.session(600, ["Bash"])
        s.turn_open = False
        self.assertFalse(widget._waiting(s))

    def test_redraws_every_second_only_while_a_dot_or_bell_shows(self):
        widget, s = self.session(5, ["Bash"])  # working: dot
        widget.active = [s]
        self.assertEqual(widget.next_frame(1000.0), 1001.0)
        widget, s = self.session(5, ["AskUserQuestion"])  # bell
        widget.active = [s]
        self.assertEqual(widget.next_frame(1000.0), 1001.0)
        widget, s = self.session(90, [])  # "1m" until idle reaches 2 min
        s.turn_open = False
        widget.active = [s]
        self.assertEqual(widget.next_frame(1000.0), 1030.0)
        s.mtime = 1000.0 - 7200 - 600  # "2h": wake within a minute anyway
        self.assertEqual(widget.next_frame(1000.0), 1060.0)

    def test_text_status_waiting_bell_draws_and_animates(self):
        widget = Agents(stats="none", rows="single", waiting=True)
        populate(widget)
        self.assertIsNotNone(widget.frame_interval)
        widget.draw(ImageDraw.Draw(Image.new("RGB", (485, 160))), 485, 160)

    def test_waiting_bell_clears_token_column(self):
        for options, size in (({"stats": "none", "rows": "single", "detail_rows": 0}, (485, 160)),
                              ({"stats": "top"}, (485, 260)),
                              ({"style": "dense"}, (800, 260))):
            with self.subTest(options=options):
                widget = Agents(waiting=True, status="dot", **options)
                populate(widget)
                widget.active = [widget.active[1]]  # 420K tokens
                widget.active[0].mtime = SAMPLE_NOW - 59 * 60
                draw = ImageDraw.Draw(Image.new("RGB", size))
                with patch.object(t, "glyph_icon", wraps=t.glyph_icon) as glyph, patch.object(
                    t, "text_right", wraps=t.text_right
                ) as text_right:
                    widget.draw(draw, *size)
                bell = next(call for call in glyph.call_args_list if call.args[3] == widget.BELL)
                token = next(call for call in text_right.call_args_list if call.args[3] == "420K")
                self.assertLessEqual(token.args[1], bell.args[1])
