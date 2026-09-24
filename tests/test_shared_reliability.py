"""Failure paths used by both compact and dense layouts."""

import json
import os
import threading
import unittest
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from turzx import session, theme
from turzx.agentlog import TokenLog
from turzx.app import App
from turzx.widgets.cpu import Cpu
from turzx.widgets.gpu import Gpu


class TokenLogTests(unittest.TestCase):
    def test_bad_lines_do_not_hide_later_usage_or_partial_next_line(self):
        now = datetime.now().astimezone().isoformat()

        def entry(count, timestamp=now):
            return json.dumps({
                "timestamp": timestamp,
                "message": {"usage": {"input_tokens": count}},
            }).encode() + b"\n"

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "project" / "session.jsonl"
            path.parent.mkdir()
            path.write_bytes(entry(10) + b"{bad json}\n[]\n" + entry(99, "bad date") + entry(20) + b"{\"timestamp\"")
            with patch("turzx.agentlog.CLAUDE_DIR", root), patch("turzx.agentlog.CODEX_DIR", root / "missing"):
                log = TokenLog()
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 30)
                self.assertEqual(len(log.events), 2)
                with path.open("ab") as file:
                    file.write(b":null}\n" + entry(5))
                os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 35)
                self.assertEqual(len(log.events), 3)


class ThemeReloadTests(unittest.TestCase):
    def test_bad_reload_preserves_palette_and_recovers_after_fix(self):
        original = {role: getattr(theme, role) for role in theme.ROLES}
        try:
            with TemporaryDirectory() as directory:
                path = Path(directory) / "colors.toml"
                path.write_text('darker_background = "#112233"\naccent = "#445566"\n')
                with patch.object(theme, "theme_path", return_value=path):
                    theme.load("current")
                    app = App({}, [], "/tmp/theme-reload-test.png")
                    app.mode = session.OFF
                    try:
                        stamp = app._theme_mtime
                        path.write_text('darker_background = "#abcdef"\naccent = [\n')
                        os.utime(path, (stamp + 1, stamp + 1))
                        with self.assertLogs("turzx", level="WARNING"):
                            app.watch_session(0)
                        self.assertEqual(theme.BG, (0x11, 0x22, 0x33))
                        self.assertEqual(theme.ACCENT, (0x44, 0x55, 0x66))
                        self.assertEqual(app._theme_mtime, stamp)

                        path.write_text('darker_background = "#abcdef"\naccent = "wrong"\n')
                        os.utime(path, (stamp + 2, stamp + 2))
                        with self.assertLogs("turzx", level="WARNING"):
                            app.watch_session(3)
                        self.assertEqual(theme.BG, (0x11, 0x22, 0x33))
                        self.assertEqual(app._theme_mtime, stamp)

                        path.write_text('darker_background = "#abcdef"\naccent = "#123456"\n')
                        os.utime(path, (stamp + 3, stamp + 3))
                        app.watch_session(6)
                        self.assertEqual(theme.BG, (0xab, 0xcd, 0xef))
                        self.assertEqual(theme.ACCENT, (0x12, 0x34, 0x56))
                        self.assertEqual(app._theme_mtime, path.stat().st_mtime)
                    finally:
                        app.pool.shutdown(wait=True)
        finally:
            for role, value in original.items():
                setattr(theme, role, value)


class GpuRetryTests(unittest.TestCase):
    def test_nvml_failure_retries_after_backoff_and_recovers(self):
        now = [100.0]
        with patch("turzx.widgets.gpu.pynvml") as nvml, patch(
            "turzx.widgets.gpu.time.monotonic", side_effect=lambda: now[0]
        ):
            nvml.NVMLError = RuntimeError
            nvml.nvmlInit.side_effect = [RuntimeError("driver unavailable"), None, None]
            nvml.nvmlDeviceGetHandleByIndex.return_value = object()
            nvml.nvmlDeviceGetUtilizationRates.return_value = SimpleNamespace(gpu=55)
            nvml.nvmlDeviceGetTemperature.return_value = 60
            nvml.nvmlDeviceGetPowerUsage.return_value = 120000
            nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(used=1, total=2)

            widget = Gpu()
            self.assertIsNone(widget.handle)
            now[0] = 105
            widget.update()
            self.assertEqual(nvml.nvmlInit.call_count, 1)
            now[0] = 111
            widget.update()
            self.assertEqual(nvml.nvmlInit.call_count, 2)
            self.assertEqual(widget.util, 55)
            self.assertEqual((widget.mem_used, widget.mem_total), (1, 2))

            now[0] = 112
            nvml.nvmlDeviceGetUtilizationRates.side_effect = RuntimeError("device lost")
            widget.update()
            self.assertIsNone(widget.handle)
            nvml.nvmlDeviceGetUtilizationRates.side_effect = None
            now[0] = 123
            widget.update()
            self.assertIsNotNone(widget.handle)


class CpuSamplingTests(unittest.TestCase):
    def test_counter_deltas_work_across_worker_threads(self):
        Times = namedtuple("Times", "user idle iowait guest guest_nice")
        snapshots = [
            [Times(0, 0, 0, 0, 0), Times(0, 0, 0, 0, 0)],
            [Times(10, 10, 0, 0, 0), Times(0, 20, 0, 0, 0)],
            [Times(30, 10, 0, 0, 0), Times(10, 30, 0, 0, 0)],
        ]
        with patch("turzx.widgets.cpu.psutil.cpu_times", side_effect=snapshots), patch(
            "turzx.widgets.cpu.psutil.sensors_temperatures", return_value={}
        ), patch.object(Cpu, "_read_power"):
            widget = Cpu(smooth=0, graph_smooth=0)
            for expected in ((50, 0), (100, 50)):
                worker = threading.Thread(target=widget.update)
                worker.start()
                worker.join()
                self.assertEqual(widget.cores, list(expected))
                self.assertEqual(widget.total, sum(expected) / 2)


if __name__ == "__main__":
    unittest.main()
