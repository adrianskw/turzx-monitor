"""Layout validation, data freshness, and repeatable previews."""

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from turzx.app import App, LAYOUTS, load_config
from turzx.sample import SAMPLE_NOW, populate
from turzx.widgets.ai_usage import AiUsage
from turzx.widgets.weather import Weather


class LayoutTests(unittest.TestCase):
    def test_default_layout_is_valid(self):
        _, slots, cards = load_config(LAYOUTS / "default.toml")
        self.assertEqual(len(slots), 6)
        self.assertEqual(len(cards), 1)

    def test_invalid_boxes_and_intervals_have_contextual_errors(self):
        cases = (
            ("box = [790, 0, 20, 20]", "fit inside"),
            ("box = [0, 0, 20, 20]\ninterval = 0", "widget[1] (clock): interval"),
            ("box = [0, 0, -1, 20]", "fit inside"),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "layout.toml"
            for settings, message in cases:
                with self.subTest(settings=settings):
                    path.write_text(f'[[widget]]\ntype = "clock"\n{settings}\n')
                    with self.assertRaisesRegex(SystemExit, re.escape(message)):
                        load_config(path)

    def test_rejects_overlapping_widgets_and_uncontained_shared_widget(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "layout.toml"
            path.write_text(
                '[[widget]]\ntype = "clock"\nbox = [0, 0, 100, 100]\n'
                '[[widget]]\ntype = "memory"\nbox = [99, 0, 100, 100]\n'
            )
            with self.assertRaisesRegex(SystemExit, "overlaps widget"):
                load_config(path)
            path.write_text('[[widget]]\ntype = "clock"\nbox = [0, 0, 100, 100]\nframe = false\n')
            with self.assertRaisesRegex(SystemExit, "containing"):
                load_config(path)


class FreshnessTests(unittest.TestCase):
    def test_failed_weather_parse_retains_last_complete_forecast(self):
        widget = Weather(latitude=0, longitude=0)
        populate(widget)
        previous = widget.snapshot
        with patch("turzx.widgets.weather.urllib.request.urlopen"), patch(
            "turzx.widgets.weather.json.load", return_value={"current": {"time": "2026-09-23T10:00"}}
        ):
            widget.update()
        self.assertIs(widget.snapshot, previous)
        self.assertEqual(widget.error, "weather: KeyError")
        widget.preview_now = SAMPLE_NOW + 600
        self.assertEqual(widget.freshness_text(previous), "updated 11m ago")

    def test_failed_usage_fetch_retains_values_and_shows_age(self):
        widget = AiUsage(providers=["claude"])
        populate(widget)
        previous = widget.data["claude"]
        with patch.object(widget, "_fetch", side_effect=ValueError("bad response")):
            widget.update()
        self.assertEqual(widget.data["claude"], previous)
        widget.preview_now = SAMPLE_NOW + 600
        self.assertEqual(widget.freshness_text("claude"), "updated 11m ago")


class SamplePreviewTests(unittest.TestCase):
    def test_two_previews_are_identical_without_live_sources(self):
        with TemporaryDirectory() as directory, patch(
            "turzx.widgets.weather.urllib.request.urlopen", side_effect=AssertionError("network called")
        ), patch("turzx.widgets.ai_usage.subprocess.run", side_effect=AssertionError("CLI called")), patch(
            "turzx.widgets.cpu.psutil.cpu_times", side_effect=AssertionError("CPU probed")
        ) as cpu_times, patch("turzx.widgets.gpu.pynvml") as nvml:
            outputs = []
            for index in range(2):
                config, slots, cards = load_config(LAYOUTS / "default.toml", sample=True)
                path = Path(directory) / f"sample-{index}.png"
                app = App(config, slots, str(path), cards)
                try:
                    app.run(once=True, sample=True)
                finally:
                    app.pool.shutdown(wait=True)
                outputs.append(path.read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            cpu_times.assert_not_called()
            nvml.nvmlInit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
