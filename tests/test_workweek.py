import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from PIL import Image, ImageDraw

from turzx.sample import SAMPLE_DATE, SAMPLE_NOW
from turzx.widgets.ai_usage import AiUsage, WorkWeek

WEEK = 7 * 86400


class WorkWeekTests(unittest.TestCase):
    def setUp(self):
        self.ww = WorkWeek({})

    def pace(self, start: datetime, now: datetime) -> float:
        return self.ww.pace(now.timestamp(), start.timestamp() + WEEK - now.timestamp())

    def test_preview_pacing_uses_the_displayed_date(self):
        self.assertEqual(datetime.fromtimestamp(SAMPLE_NOW), SAMPLE_DATE)

    def test_runs_from_zero_to_one_hundred_without_going_back(self):
        start = datetime(2026, 9, 24, 15, 20)  # a mid-week reset
        values = [self.pace(start, start + timedelta(hours=h)) for h in range(0, 169)]
        self.assertAlmostEqual(values[0], 0, places=6)
        self.assertAlmostEqual(values[-1], 100, places=6)
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(values, values[1:])))

    def test_flat_while_asleep(self):
        start = datetime(2026, 9, 21)
        self.assertAlmostEqual(self.pace(start, datetime(2026, 9, 23, 1, 30)),
                               self.pace(start, datetime(2026, 9, 23, 6, 30)), places=6)

    def test_midweek_is_ahead_of_the_clock_and_weekend_is_small(self):
        start = datetime(2026, 9, 21)  # Monday
        # 26.15 work-equivalent hours elapsed out of 61.65 in the full week.
        self.assertAlmostEqual(self.pace(start, datetime(2026, 9, 23, 12)),
                               100 * 26.15 / 61.65, places=6)
        friday_night = self.pace(start, datetime(2026, 9, 26, 1))
        self.assertAlmostEqual(100 - friday_night, 8.8, delta=0.3)  # the weekend's share

    def test_after_midnight_belongs_to_the_evening_before(self):
        self.assertEqual(self.ww.weight(datetime(2026, 9, 26, 0, 30)), 0.25)  # Saturday 00:30 = Friday night
        self.assertEqual(self.ww.weight(datetime(2026, 9, 26, 10)), 0.15)

    def test_bad_options_fail_at_load(self):
        for option in ({"work_hours": "nine"}, {"sleep_hours": "1-99"}, {"evening_weight": 2},
                       {"weekend_weight": "x"}, {"workdays": "monday"}, {"pace_7d": "fast"}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                AiUsage(pace_7d=option.pop("pace_7d", "workweek"), **option)

    def test_every_card_style_uses_weighted_weekly_pace(self):
        for options, height in (({}, 200), ({"window_labels": False}, 200),
                                ({"arrangement": "stacked"}, 200), ({"style": "dense"}, 80)):
            with self.subTest(options=options):
                widget = AiUsage(providers=["claude"], pace_7d="workweek", **options)
                widget.data["claude"] = (43, 31, "2h", "4d12h")
                widget.preview_now = datetime(2026, 9, 23, 12).timestamp()
                widget.preview_elapsed = 0
                with patch.object(widget.workweek, "pace", return_value=42.4) as weighted:
                    widget.draw(ImageDraw.Draw(Image.new("RGB", (400, height))), 400, height)
                weighted.assert_called_once()
