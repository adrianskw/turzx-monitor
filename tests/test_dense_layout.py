"""The dense preset renders real widget data in normal and crowded states."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw

from turzx import theme
from turzx.app import App, LAYOUTS, load_config, render
from turzx.sample import populate


class DenseLayoutTests(unittest.TestCase):
    def test_sample_preview_is_repeatable_and_binds_existing_histories(self):
        outputs = []
        with TemporaryDirectory() as directory:
            for index in range(2):
                config, slots, cards = load_config(LAYOUTS / "dense-vertical.toml", sample=True)
                widgets = {s.widget.kind: s.widget for s in slots}
                self.assertIs(widgets["agents"].history_sources["cpu"], widgets["cpu"])
                self.assertIs(widgets["agents"].history_sources["gpu"], widgets["gpu"])
                path = Path(directory) / f"dense-{index}.png"
                app = App(config, slots, str(path), cards)
                try:
                    app.run(once=True, sample=True)
                finally:
                    app.pool.shutdown(wait=True)
                with Image.open(path) as preview:
                    self.assertEqual(preview.size, (800, 480))
                outputs.append(path.read_bytes())
        self.assertEqual(outputs[0], outputs[1])

    def test_history_space_turns_into_session_rows_when_crowded(self):
        _, slots, _ = load_config(LAYOUTS / "dense-vertical.toml", sample=True)
        for slot in slots:
            populate(slot.widget)
            slot.ready = True
        agents = next(s for s in slots if s.widget.kind == "agents")
        with patch.object(theme, "sparkline", wraps=theme.sparkline) as sparkline:
            render(agents)
            self.assertEqual(sparkline.call_count, 2)
        agents.widget.active = agents.widget.active * 3
        with patch.object(theme, "sparkline", wraps=theme.sparkline) as sparkline:
            render(agents)
            sparkline.assert_not_called()

    def test_dense_readings_show_100_percent(self):
        _, slots, _ = load_config(LAYOUTS / "dense-vertical.toml", sample=True)
        for slot in slots:
            populate(slot.widget)
            if slot.widget.kind == "cpu":
                slot.widget.total = 100
            elif slot.widget.kind == "gpu":
                slot.widget.util = 100
            elif slot.widget.kind == "memory":
                slot.widget.ram.percent = 100
            elif slot.widget.kind == "ai_usage":
                p = slot.widget.providers[0]
                _, seven, r5, r7 = slot.widget.data[p]
                slot.widget.data[p] = (100, seven, r5, r7)
            else:
                continue
            _, _, w, h = slot.box
            draw = ImageDraw.Draw(Image.new("RGB", (w, h)))
            with patch.object(draw, "text", wraps=draw.text) as text:
                slot.widget.draw(draw, w, h)
            self.assertIn("100%", [call.args[1] for call in text.call_args_list], slot.widget.kind)


if __name__ == "__main__":
    unittest.main()
