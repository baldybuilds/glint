import datetime
import json
import os
import tempfile
import unittest

from glint import geometry, naming
from glint.config import Config


class GeometryTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(geometry.normalize(10, 20, 4, 5), (4, 5, 6, 15))

    def test_intersect(self):
        self.assertEqual(geometry.intersect((0, 0, 10, 10), (5, 5, 10, 10)), (5, 5, 5, 5))
        self.assertIsNone(geometry.intersect((0, 0, 10, 10), (10, 0, 5, 5)))

    def test_union(self):
        self.assertEqual(geometry.union([(493, 0, 2560, 1440), (0, 1440, 3440, 1440)]), (0, 0, 3440, 2880))

    def test_to_pixels_with_origin_ratio_and_clip(self):
        self.assertEqual(geometry.to_pixels((10, 10, 100, 50), (0, 0), 2.0), (20, 20, 200, 100))
        self.assertEqual(geometry.to_pixels((-10, 0, 30, 30), (0, 0), 1.0, limit=(100, 100)), (0, 0, 20, 30))
        self.assertIsNone(geometry.to_pixels((200, 200, 5, 5), (0, 0), 1.0, limit=(100, 100)))

    def test_constraints(self):
        self.assertEqual(geometry.constrain_square(0, 0, 10, -4), (10, -10))
        x, y = geometry.constrain_angle(0, 0, 10, 1)
        self.assertAlmostEqual(y, 0, places=6)

    def test_even(self):
        self.assertEqual(geometry.even((1, 2, 101, 55)), (1, 2, 100, 54))

    def test_apply_aspect(self):
        self.assertEqual(geometry.apply_aspect((0, 0, 200, 100), 1.0), (50.0, 0.0, 100, 100))

    def test_stack_positions_bottom_left(self):
        pos = geometry.stack_positions((0, 0, 1000, 800), [(240, 100), (240, 150)], "bottom-left", 20, 10)
        self.assertEqual(pos[1], (20, 800 - 20 - 150))           # newest nearest the corner
        self.assertEqual(pos[0], (20, 800 - 20 - 150 - 10 - 100))

    def test_stack_positions_top_right(self):
        pos = geometry.stack_positions((100, 0, 1000, 800), [(240, 100)], "top-right", 20, 10)
        self.assertEqual(pos[0], (100 + 1000 - 20 - 240, 20))


class NamingTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 15, 9, 5, 7)

    def test_default_template(self):
        self.assertEqual(naming.render("Glint {date} at {time}", self.NOW), "Glint 2026-10-15 at 09.05.07")

    def test_tokens(self):
        name = naming.render("{type}_{width}x{height}_{year}{month}{day}-{counter}", self.NOW,
                             kind="Window", size=(800, 600), counter=3)
        self.assertEqual(name, "Window_800x600_20261015-3")

    def test_sanitises_and_keeps_unknown_tokens(self):
        self.assertEqual(naming.render("a/b {nope}", self.NOW), "a-b {nope}")

    def test_empty_falls_back(self):
        self.assertTrue(naming.render("  ", self.NOW).startswith("Glint 2026-10-15"))

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as d:
            first = naming.unique_path(d, "Shot", "png")
            open(first, "w").close()
            self.assertEqual(os.path.basename(naming.unique_path(d, "Shot", ".png")), "Shot (2).png")


class ConfigTests(unittest.TestCase):
    def test_defaults_merge_and_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as fh:
                json.dump({"general": {"format": "jpg"}, "unknown": 1}, fh)
            cfg = Config(path)
            self.assertEqual(cfg.get("general.format"), "jpg")
            self.assertTrue(cfg.get("general.play_sounds"))       # default kept
            self.assertEqual(cfg.get("missing.key", 5), 5)
            seen = []
            cfg.connect(lambda k, v: seen.append((k, v)))
            cfg.set("quick_access.position", "top-right")
            self.assertEqual(seen, [("quick_access.position", "top-right")])
            self.assertEqual(Config(path).get("quick_access.position"), "top-right")

    def test_corrupt_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            self.assertEqual(Config(path).get("general.format"), "png")


class CommandParsingTests(unittest.TestCase):
    def setUp(self):
        from glint.app import parse_command
        self.parse = parse_command

    def test_plain(self):
        self.assertEqual(self.parse(["capture-area"]), ("capture-area", {}))

    def test_url_scheme(self):
        self.assertEqual(self.parse(["glint://open-annotate?filepath=/tmp/a%20b.png"]),
                         ("open-annotate", {"filepath": "/tmp/a b.png"}))

    def test_inline_query_and_args(self):
        self.assertEqual(self.parse(["self-timer?seconds=3"]), ("self-timer", {"seconds": "3"}))
        self.assertEqual(self.parse(["pin", "/x/y.png"]), ("pin", {"filepath": "/x/y.png"}))
        self.assertEqual(self.parse(["record-screen", "format=gif"]), ("record-screen", {"format": "gif"}))


if __name__ == "__main__":
    unittest.main()
