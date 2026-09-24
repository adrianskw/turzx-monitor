"""Regression tests for source failures and display edge cases."""

import io
import json
import os
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from turzx import session, theme
from turzx.agentlog import TokenLog
from turzx.app import App, Slot
from turzx.driver import TurzxDisplay, _pixels
from turzx.sample import populate
from turzx.widgets.cpu import Cpu
from turzx.widgets.forecast import Forecast
from turzx.widgets.gpu import Gpu
from turzx.widgets.weather import Weather, _CACHE, _CACHE_LOCK, _FETCH_LOCKS


class DriverTests(unittest.TestCase):
    def test_pixel_order_and_alpha(self):
        image = Image.new("RGBA", (2, 1))
        image.putdata([(1, 2, 3, 4), (200, 150, 100, 50)])
        self.assertEqual(_pixels(image, True), (bytes((3, 2, 1, 4, 100, 150, 200, 50)), 4))
        self.assertEqual(_pixels(image, False), (bytes((3, 2, 1, 100, 150, 200)), 3))

    def test_auto_port_wakes_sleeping_panel(self):
        with patch("turzx.driver.find_port", return_value=None), patch(
            "turzx.driver.find_sleep_port", return_value="/dev/sleep"
        ), patch("turzx.driver.wake", return_value="/dev/awake") as wake, patch(
            "turzx.driver.serial.Serial"
        ) as serial, patch.object(TurzxDisplay, "_hello"), patch.object(TurzxDisplay, "_send"):
            display = TurzxDisplay()
            wake.assert_called_once_with("/dev/sleep")
            self.assertEqual(serial.call_args.args[0], "/dev/awake")
            display.close()


class SensorTests(unittest.TestCase):
    def test_gpu_hidden_power_is_not_polled_and_optional_failure_keeps_data(self):
        with patch("turzx.widgets.gpu.pynvml") as nvml:
            nvml.NVMLError = RuntimeError
            nvml.nvmlDeviceGetHandleByIndex.return_value = object()
            nvml.nvmlDeviceGetUtilizationRates.return_value = SimpleNamespace(gpu=55)
            nvml.nvmlDeviceGetTemperature.return_value = 60
            nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(used=1, total=2)
            nvml.nvmlDeviceGetPowerUsage.side_effect = RuntimeError("unsupported")
            hidden = Gpu(power=False)
            hidden.update()
            nvml.nvmlDeviceGetPowerUsage.assert_not_called()
            self.assertEqual(hidden.util, 55)
            enabled = Gpu()
            enabled.update()
            self.assertIsNotNone(enabled.handle)
            self.assertEqual(enabled.mem_used, 1)
            self.assertIsNone(enabled.power)

    def test_cpu_many_cores_keeps_isolated_hot_core_and_draws(self):
        widget = Cpu(_sample=True)
        widget.cores = [0.0] * 128
        widget.cores[29] = 100.0
        draw = ImageDraw.Draw(Image.new("RGB", (290, 160)))
        with patch.object(theme, "bar", wraps=theme.bar) as bars:
            widget.draw(draw, 290, 160)
        self.assertGreater(bars.call_count, 0)
        self.assertLess(bars.call_count, 128)
        self.assertIn(100.0, [call.args[5] for call in bars.call_args_list])

    def test_cpu_hidden_power_is_not_polled(self):
        widget = Cpu(_sample=True, power=False)
        with patch("turzx.widgets.cpu.psutil.cpu_times", return_value=[]), patch(
            "turzx.widgets.cpu.psutil.sensors_temperatures", return_value={}
        ), patch.object(widget, "_read_power") as power:
            widget.update()
            power.assert_not_called()

    def test_invalid_cpu_core_step_is_rejected(self):
        for value in (0, -1, 101, float("nan"), float("inf"), True, "oops"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "core_step"):
                Cpu(_sample=True, core_step=value)

    def test_empty_forecast_draws_placeholder(self):
        widget = Forecast(latitude=0, longitude=0)
        populate(widget)
        widget.weather.snapshot = replace(widget.weather.snapshot, hours=())
        widget.draw(ImageDraw.Draw(Image.new("RGB", (170, 270))), 170, 270)


class RuntimeTests(unittest.TestCase):
    def test_failed_session_future_does_not_escape_main_loop(self):
        app = App({}, [], None)
        failed = Future()
        failed.set_exception(RuntimeError("hyprctl failed"))
        app._mode_future = failed
        try:
            with self.assertLogs("turzx", level="ERROR"):
                app.watch_session(10)
            self.assertIsNone(app._mode_future)
            self.assertEqual(app.mode, session.ON)
            self.assertEqual(app._next_session_check, 12)
        finally:
            app.pool.shutdown(wait=True)

    def test_backward_clock_resets_wall_deadlines(self):
        slot = Slot(Cpu(_sample=True), (0, 0, 290, 160), next_due=200, next_frame=200)
        app = App({}, [slot], "/tmp/audit-preview.png")
        app._last_wall = 100
        app._next_session_check = 200
        app._next_sun_check = 200
        try:
            app._check_clock(90)
            self.assertEqual(app._next_session_check, 0)
            self.assertEqual(app._next_sun_check, 0)
            self.assertEqual(slot.next_due, 0)
            self.assertEqual(slot.next_frame, 0)
        finally:
            app.pool.shutdown(wait=True)

    def test_malformed_hyprctl_shape_is_ignored(self):
        with patch("turzx.session._run", return_value='{"error":"offline"}'):
            self.assertFalse(session.screensaver())
            self.assertFalse(session.monitors_off())

    def test_log_truncate_and_replace_drop_old_usage(self):
        now = datetime.now().astimezone().isoformat()

        def entry(count):
            return (json.dumps({"timestamp": now, "message": {"usage": {"input_tokens": count}}}) + "\n").encode()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "project" / "one.jsonl"
            path.parent.mkdir()
            path.write_bytes(entry(1000))
            with patch("turzx.agentlog.CLAUDE_DIR", root), patch("turzx.agentlog.CODEX_DIR", root / "missing"):
                log = TokenLog()
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 1000)
                path.write_bytes(entry(2))
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 2)
                self.assertEqual(len(log.events), 1)
                replacement = path.with_suffix(".new")
                replacement.write_bytes(entry(3))
                os.replace(replacement, path)
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 3)
                self.assertEqual(len(log.events), 1)

    def test_recent_yesterday_log_contributes_to_rate_after_midnight(self):
        next_day = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        previous = next_day - timedelta(minutes=10)
        record = {"timestamp": previous.isoformat(), "message": {"usage": {"input_tokens": 60}}}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return next_day if tz is None else next_day.astimezone(tz)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "project" / "one.jsonl"
            path.parent.mkdir()
            path.write_text(json.dumps(record) + "\n")
            os.utime(path, (previous.timestamp(), previous.timestamp()))
            with patch("turzx.agentlog.CLAUDE_DIR", root), patch(
                "turzx.agentlog.CODEX_DIR", root / "missing"
            ), patch("turzx.agentlog.datetime", Clock), patch("turzx.agentlog.time.time", return_value=next_day.timestamp()):
                log = TokenLog()
                log.refresh(max_age=0)
                self.assertEqual(log.total_today(), 0)
                self.assertEqual(log.rate(60), 1)

    def test_concurrent_weather_widgets_share_one_fetch(self):
        data = {
            "current": {"time": "2026-09-24T10:00", "temperature_2m": 20,
                        "relative_humidity_2m": 50, "weather_code": 0, "is_day": 1},
            "daily": {"temperature_2m_max": [25], "temperature_2m_min": [15]},
            "hourly": {"time": ["2026-09-24T11:00"], "weather_code": [0],
                       "is_day": [1], "temperature_2m": [21]},
        }
        calls = []

        def open_url(*args, **kwargs):
            calls.append(1)
            time.sleep(0.03)
            return io.BytesIO(json.dumps(data).encode())

        with _CACHE_LOCK:
            _CACHE.clear()
            _FETCH_LOCKS.clear()
        a, b = Weather(latitude=1, longitude=2), Weather(latitude=1, longitude=2)
        with patch("turzx.widgets.weather.urllib.request.urlopen", side_effect=open_url), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda widget: widget.update(), (a, b)))
        self.assertEqual(len(calls), 1)
        self.assertIs(a.snapshot, b.snapshot)
        with _CACHE_LOCK:
            _CACHE.clear()
            _FETCH_LOCKS.clear()


if __name__ == "__main__":
    unittest.main()
