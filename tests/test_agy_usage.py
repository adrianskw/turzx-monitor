import json
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from turzx.widgets.ai_usage import AiUsage


def usage(gemini_5h, gemini_week, reset_in):
    stamp = datetime.fromtimestamp(time.time() + reset_in, timezone.utc).isoformat().replace("+00:00", "Z")
    bucket = lambda window, left: {"window": window, "remaining_fraction": left, "reset_time": stamp}
    groups = [{"name": "Gemini Models", "buckets": [bucket("weekly", gemini_week), bucket("5h", gemini_5h)]},
              {"name": "Claude and GPT models", "buckets": [bucket("weekly", 0.1), bucket("5h", 0.1)]}]
    return SimpleNamespace(stdout=json.dumps({"command": {"name": "usage", "data": {"groups": groups}}}))


class AgyUsageTests(unittest.TestCase):
    def fetch(self, *args):
        widget = AiUsage(providers=["agy"])
        with patch("turzx.widgets.ai_usage.subprocess.run", return_value=usage(*args)) as run:
            widget.update()
        return widget, run

    def test_gemini_group_as_used_percent_and_countdowns(self):
        widget, run = self.fetch(0.82, 0.25, 3 * 3600)
        five, seven, r5, r7 = widget.data["agy"]
        self.assertEqual((five, seven), (18, 75))  # the Claude/GPT group is ignored
        self.assertAlmostEqual(int(r5[:-1]), 3 * 3600, delta=5)
        self.assertIn("--log-file", run.call_args.args[0])  # no log file per poll

    def test_untouched_5h_window_has_not_started(self):
        widget, _ = self.fetch(1.0, 1.0, 5.5 * 3600)
        self.assertEqual(widget.data["agy"][2], "not started")

    def test_polled_every_five_minutes_not_every_update(self):
        widget, run = self.fetch(0.5, 0.5, 3600)
        with patch("turzx.widgets.ai_usage.subprocess.run", return_value=usage(0.4, 0.4, 3600)) as again:
            widget.update()
        again.assert_not_called()
        self.assertIsNone(widget.freshness_text("agy"))


if __name__ == "__main__":
    unittest.main()
