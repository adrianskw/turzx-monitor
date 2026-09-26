import unittest

from PIL import Image, ImageDraw

from turzx import theme as t
from turzx.sample import SAMPLE_DATE, populate
from turzx.widgets.clock_weather import ClockWeather


class SpreadTests(unittest.TestCase):
    def test_single_group_spreads_evenly_between_the_edges(self):
        (tops,) = t.spread(16, 94, [[20, 10, 10]])
        self.assertEqual(tops, [16, 55, 84])  # gaps 19 and 19, bottom edge at 94

    def test_gaps_are_whole_pixels_and_equal(self):
        tops = [g[0] for g in t.spread(16, 204, [[19]] * 7)]
        gaps = {b - a for a, b in zip(tops, tops[1:])}
        self.assertEqual(len(gaps), 1)
        self.assertLessEqual(abs((tops[0] - 16) - (204 - tops[-1] - 19)), 1)  # leftover split

    def test_groups_keep_their_inside_gap_and_share_the_rest(self):
        groups = t.spread(16, 244, [[24, 16, 13]] * 3, within=6)
        self.assertEqual([g[1] - g[0] for g in groups], [30] * 3)  # 24 + 6
        between = groups[1][0] - (groups[0][2] + 13)
        self.assertEqual(groups[2][0] - (groups[1][2] + 13), between)
        self.assertGreater(between, 6)

    def test_ratio_and_cap(self):
        g = t.spread(0, 100, [[10, 10]] * 2)
        inside, between = g[0][1] - g[0][0] - 10, g[1][0] - g[0][1] - 10
        self.assertEqual(between, 2 * inside)
        g = t.spread(0, 200, [[10, 10]] * 2, max_within=5)
        self.assertEqual(g[0][1] - g[0][0] - 10, 5)


class MarginTests(unittest.TestCase):
    def tearDown(self):
        t.set_margin(None)

    def test_margin_sets_the_padding_and_is_validated(self):
        t.set_margin(14)
        self.assertEqual(t.PAD, 16)  # the card sits 2 px inside its box
        t.set_margin(None)
        self.assertEqual(t.PAD, 14)
        for bad in (-1, 41, 1.5, "14", True):
            with self.assertRaises(ValueError):
                t.set_margin(bad)

    def test_sun_line_spacing_does_not_depend_on_the_hour(self):
        t.set_margin(14)
        calls = []
        for hour in (10, 9):
            widget = ClockWeather(arrangement="row", show="time", date_side="right", time_size=100,
                                  ampm_style="below", sun=True)
            populate(widget)
            widget.clock.now = SAMPLE_DATE.replace(hour=hour, minute=8)
            widget._sun_cache = (widget.clock.now.timestamp() // 60, widget._sun_cache[1])
            seen = []
            widget._draw_sun_line = lambda d, left, right, *a: seen.append((left, right))
            widget.draw(ImageDraw.Draw(Image.new("RGB", (315, 110))), 315, 110)
            calls.append(seen[0])
        self.assertEqual(calls[0], calls[1])


if __name__ == "__main__":
    unittest.main()
