import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageChops, ImageDraw

from turzx.sample import SAMPLE_NOW, populate
from turzx.widgets.agents import Agents

OPTS = dict(stats="none", rows="single", grow=True, sleepy=True, nudge=True, status="dot")


def render(widget, now):
    widget.preview_now = now
    img = Image.new("RGB", (485, 260))
    widget.draw(ImageDraw.Draw(img), 485, 260)
    return img


class SleepyTests(unittest.TestCase):
    def test_empty_card_animates_and_redraws_every_step(self):
        widget = Agents(**OPTS)
        populate(widget)
        widget.active = []
        with patch.object(widget, "_draw_sleeping", wraps=widget._draw_sleeping) as sleeping:
            a, b = render(widget, SAMPLE_NOW), render(widget, SAMPLE_NOW + 1)
        self.assertEqual(sleeping.call_count, 2)
        self.assertIsNotNone(ImageChops.difference(a, b).getbbox())  # the z's drift
        self.assertEqual(widget.next_frame(100.2), 101.0)

    def test_off_by_default(self):
        widget = Agents(stats="none", rows="single")
        widget.active = []
        with patch.object(widget, "_draw_sleeping") as sleeping:
            render(widget, SAMPLE_NOW)
        sleeping.assert_not_called()


class NudgeTests(unittest.TestCase):
    def solo(self, minutes):
        widget = Agents(**OPTS)
        populate(widget)
        widget.active = widget.active[:1]
        widget.active[0].mtime = SAMPLE_NOW - 600  # idle: no dot, only the vine animates
        widget.solo_since = SAMPLE_NOW - minutes * 60
        return widget

    def caption(self, minutes):
        widget = self.solo(minutes)
        with patch("turzx.theme.text_right", wraps=__import__("turzx.theme").theme.text_right) as right:
            render(widget, SAMPLE_NOW)
        return {c.args[3] for c in right.call_args_list} & {
            "just one agent", "room for more", "spin up another?", "45m solo"}

    def test_caption_nudges_harder_the_longer_one_agent_runs_alone(self):
        self.assertEqual(self.caption(2), {"just one agent"})
        self.assertEqual(self.caption(12), {"room for more"})
        self.assertEqual(self.caption(22), {"spin up another?"})
        self.assertEqual(self.caption(45), {"45m solo"})

    def test_vine_grows_and_keeps_animating(self):
        young, old = render(self.solo(2), SAMPLE_NOW), render(self.solo(25), SAMPLE_NOW)
        self.assertIsNotNone(ImageChops.difference(young, old).getbbox())
        self.assertEqual(self.solo(2).next_frame(100.2), 101.0)

    def test_solo_clock_starts_at_one_session_and_resets(self):
        widget = Agents(**OPTS)
        log = MagicMock(rate=lambda m: 0, total_today=lambda: 0, cache_hit=lambda m: None)
        populate(widget)
        sessions = widget.active
        with patch("turzx.agentlog.shared", return_value=log), patch("time.time", return_value=500.0):
            log.active = lambda m: sessions[:1]
            widget.update()
            self.assertEqual(widget.solo_since, 500.0)
        with patch("turzx.agentlog.shared", return_value=log), patch("time.time", return_value=900.0):
            widget.update()
            self.assertEqual(widget.solo_since, 500.0)  # still the same solo stint
            log.active = lambda m: sessions[:2]
            widget.update()
            self.assertIsNone(widget.solo_since)


if __name__ == "__main__":
    unittest.main()
