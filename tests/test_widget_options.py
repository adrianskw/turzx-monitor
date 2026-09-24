"""Regression coverage for animation and compact widget option combinations."""

import re
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw

from turzx import theme as t
from turzx.app import App, Slot, load_config
from turzx.sample import SAMPLE_NOW, populate
from turzx.widgets.agents import Agents
from turzx.widgets.ai_usage import AiUsage, reset_text
from turzx.widgets.clock_weather import ClockWeather
from turzx.widgets.weather import glyph


class AnimationTests(unittest.TestCase):
    def test_invalid_timing_is_rejected_during_layout_loading(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "layout.toml"
            for option in ("pulse", "pulse_step"):
                for value in ("0", "-1", "nan", "inf", "-inf", "true", '"invalid"', "[]"):
                    with self.subTest(option=option, value=value):
                        path.write_text(
                            '[[widget]]\ntype="agents"\nbox=[0,0,430,200]\n'
                            f'status="dot"\n{option}={value}\n'
                        )
                        message = f"widget[1] (agents): {option} must be a positive finite number"
                        with self.assertRaisesRegex(SystemExit, re.escape(message)):
                            load_config(path)

    def test_fractional_timing_schedules_and_draws(self):
        widget = Agents(status="dot", pulse=2.5, pulse_step=0.25)
        populate(widget)
        slot = Slot(widget, (0, 0, 430, 200), ready=True, dirty=False, next_due=float("inf"))
        app = App({}, [slot], "/tmp/unused-widget-options.png")
        try:
            app.schedule(SAMPLE_NOW)
            self.assertTrue(slot.dirty)
            self.assertGreater(slot.next_frame, SAMPLE_NOW)
            widget.draw(ImageDraw.Draw(Image.new("RGB", (430, 200))), 430, 200)
        finally:
            app.pool.shutdown(wait=True)


class AgentLayoutTests(unittest.TestCase):
    def test_text_status_does_not_overlap_name_or_cache_ring(self):
        for stats in ("none", "left"):
            for rings in (False, True):
                with self.subTest(stats=stats, rings=rings):
                    widget = Agents(stats=stats, cache_ring=rings)
                    populate(widget)
                    widget.active = widget.active[:1]
                    widget.active[0].cwd = "/sample/" + "x" * 80
                    draw = ImageDraw.Draw(Image.new("RGB", (430, 200)))
                    with patch.object(draw, "text", wraps=draw.text) as text, patch.object(
                        t, "ring", wraps=t.ring
                    ) as ring:
                        widget.draw(draw, 430, 200)
                    status = next(c for c in text.call_args_list if c.args[1] == "working")
                    name = next(c for c in text.call_args_list if c.args[1].startswith("x"))
                    status_box = draw.textbbox(status.args[0], status.args[1], font=status.kwargs["font"])
                    name_box = draw.textbbox(name.args[0], name.args[1], font=name.kwargs["font"])
                    self.assertLess(name_box[2], status_box[0])
                    if rings:
                        _, cx, _, radius, *_ = ring.call_args.args
                        self.assertLess(cx + radius, status_box[0])
                        self.assertLess(name_box[2], cx - radius)


class WeatherIconLayoutTests(unittest.TestCase):
    def test_left_icon_stays_clear_of_two_and_three_digit_temperatures(self):
        widget = ClockWeather(arrangement="row", show="weather", forecast=False,
                              icon_left=True, temp_size=84, hilo_size=20)
        populate(widget)
        for temperature in (71, 107):
            with self.subTest(temperature=temperature):
                widget.weather.snapshot = replace(widget.weather.snapshot, temperature=temperature)
                draw = ImageDraw.Draw(Image.new("RGB", (315, 110)))
                with patch.object(draw, "text", wraps=draw.text) as text:
                    widget.draw(draw, 315, 110)
                icon = next(c for c in text.call_args_list if c.args[1] == glyph(0, 1))
                number = next(c for c in text.call_args_list if c.args[1] == f"{temperature}°")
                high = next(c for c in text.call_args_list if c.args[1].startswith("↑"))
                icon_box = draw.textbbox(icon.args[0], icon.args[1], font=icon.kwargs["font"])
                number_box = draw.textbbox(number.args[0], number.args[1], font=number.kwargs["font"])
                high_box = draw.textbbox(high.args[0], high.args[1], font=high.kwargs["font"])
                self.assertGreaterEqual(icon_box[0], 14)
                self.assertLess(icon_box[2], number_box[0])
                self.assertLess(number_box[2], high_box[0])
                self.assertEqual(icon.kwargs["fill"], t.ORANGE)


class UsageLayoutTests(unittest.TestCase):
    def test_unlabeled_rows_honor_reset_mode_and_window_thresholds(self):
        for resets in ("always", "soon"):
            for r5, r7, expected_soon in (
                ("2h42m", "4d06h", set()),
                ("1h", "1d", {"1h", "1d"}),
                ("59m", "1d01h", {"59m"}),
                ("1h01m", "23h", {"23h"}),
            ):
                with self.subTest(resets=resets, r5=r5, r7=r7):
                    widget = AiUsage(providers=["claude"], window_labels=False, resets=resets)
                    populate(widget)
                    widget.data["claude"] = (43, 31, r5, r7)
                    draw = ImageDraw.Draw(Image.new("RGB", (370, 150)))
                    with patch.object(draw, "text", wraps=draw.text) as text:
                        widget.draw(draw, 370, 150)
                    labels = {c.args[1] for c in text.call_args_list}
                    # countdowns are drawn normalized (e.g. "1h" -> "1h00m"; never seconds)
                    expected = {r5, r7} if resets == "always" else expected_soon
                    expected = {reset_text(r) for r in expected}
                    self.assertEqual(labels & {reset_text(r5), reset_text(r7)}, expected)
                    self.assertIn("43%", labels)
                    self.assertIn("31%", labels)


if __name__ == "__main__":
    unittest.main()
