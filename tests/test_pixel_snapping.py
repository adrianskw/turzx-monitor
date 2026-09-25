import unittest

from PIL import Image

from turzx import theme as t


class PixelSnappingTests(unittest.TestCase):
    def test_px_rounds_halves_up(self):
        self.assertEqual([t.px(v) for v in (10.49, 10.5, 11.5, -0.5)], [10, 11, 12, 0])

    def test_shapes_round_instead_of_truncating(self):
        im = Image.new("L", (40, 12))
        t.Draw(im).rectangle((10.7, 2, 15.7, 8), fill=255)
        self.assertEqual(im.getbbox()[0], 11)  # plain Pillow truncates this to 10

    def test_bar_right_edge_is_the_snapped_edge(self):
        for x, w in ((10.5, 20.5), (11.5, 20.5), (10.4, 20.2)):
            with self.subTest(x=x, w=w):
                im = Image.new("RGB", (60, 10))
                t.bar(t.Draw(im), x, 0, w, 8, 100, (255, 255, 255))
                box = im.getbbox()
                self.assertEqual((box[0], box[2]), (t.px(x), t.px(x + w)))

    def test_fractional_bars_keep_equal_gaps(self):
        im = Image.new("RGB", (200, 10))
        d, slot = t.Draw(im), 172 / 7
        for i in range(7):
            t.bar(d, 14 + i * slot, 0, slot - 3, 8, 100, (255, 255, 255))
        row = [im.getpixel((x, 4))[0] > 0 for x in range(200)]
        gaps = [len(g) for g in "".join("#" if on else "." for on in row).strip(".").split("#") if g]
        self.assertEqual(set(gaps), {3})


if __name__ == "__main__":
    unittest.main()
