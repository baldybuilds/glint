import os
import tempfile
import unittest

from glint import trimmer
from glint.clicks import ClickHighlighter, color_to_kconfig
from glint.config import Config


class TrimHelperTests(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(trimmer.format_time(0), "00:00.0")
        self.assertEqual(trimmer.format_time(75.25), "01:15.2")
        self.assertEqual(trimmer.format_time(-3), "00:00.0")

    def test_adjust_range_keeps_order_and_bounds(self):
        self.assertEqual(trimmer.adjust_range(0, 10, "start", 4, 10), (4, 10))
        self.assertEqual(trimmer.adjust_range(0, 10, "start", 12, 10, 0.5), (9.5, 10))
        self.assertEqual(trimmer.adjust_range(2, 10, "end", 1, 10, 0.5), (2, 2.5))
        self.assertEqual(trimmer.adjust_range(2, 8, "end", 99, 10), (2, 10))
        self.assertEqual(trimmer.adjust_range(2, 8, "start", -5, 10), (0, 8))

    def test_preview_window_size_follows_aspect(self):
        w, h = trimmer.preview_window_size(1602, 588, 3440, 1440)       # wide region
        self.assertLess(h, 760)
        self.assertAlmostEqual((w - 32) / (h - 230), 1602 / 588, delta=0.05)
        w, h = trimmer.preview_window_size(1080, 1920, 2560, 1440)      # portrait clip
        self.assertLessEqual(h, 1440 * 0.75 + 1)
        self.assertGreaterEqual(w, 720)
        self.assertEqual(trimmer.preview_window_size(0, 0, 1920, 1080), (1100, 760))

    def test_pick_encoder(self):
        self.assertEqual(trimmer.pick_encoder({"libx264", "mpeg4"})[0], "libx264")
        self.assertEqual(trimmer.pick_encoder({"libopenh264", "mpeg4"})[0], "libopenh264")
        self.assertEqual(trimmer.pick_encoder({"mpeg4"})[0], "mpeg4")

    def test_mp4_args(self):
        argv = trimmer.ffmpeg_args("in.mp4", "out.mp4", 1.5, 4.0, height=720, mute=True)
        self.assertEqual(argv[argv.index("-ss") + 1], "1.500")
        self.assertEqual(argv[argv.index("-t") + 1], "2.500")
        self.assertLess(argv.index("-ss"), argv.index("-i"))  # input seek: fast and frame-accurate
        self.assertIn("scale=-2:720:flags=lanczos", argv)
        self.assertIn("-an", argv)
        self.assertEqual(argv[-1], "out.mp4")

    def test_mp4_keeps_audio_unless_muted(self):
        argv = trimmer.ffmpeg_args("in.mp4", "out.mp4", 0, 2)
        self.assertIn("aac", argv)
        self.assertNotIn("-an", argv)
        self.assertIn("-an", trimmer.ffmpeg_args("in.mp4", "out.mp4", 0, 2, has_audio=False))

    def test_gif_args(self):
        argv = trimmer.ffmpeg_args("in.mp4", "out.gif", 0, 3, fmt="gif", gif_fps=12, gif_width=800)
        vf = argv[argv.index("-vf") + 1]
        self.assertTrue(vf.startswith("fps=12,scale='min(800,iw)'"))
        self.assertIn("palettegen", vf)
        self.assertNotIn("-c:v", argv)

    def test_output_path(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "Rec.mp4")
            open(src, "w").close()
            self.assertEqual(trimmer.output_path(src, "mp4"), src)
            self.assertEqual(os.path.basename(trimmer.output_path(src, "gif")), "Rec.gif")
            copy = trimmer.output_path(src, "mp4", copy=True)
            self.assertEqual(os.path.basename(copy), "Rec (trimmed).mp4")
            open(copy, "w").close()
            self.assertEqual(os.path.basename(trimmer.output_path(src, "mp4", copy=True)), "Rec (trimmed) (2).mp4")


class FakeKConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})

    @staticmethod
    def available():
        return True

    def read(self, key):
        return self.values.get(key)

    def write(self, key, value):
        self.values[key] = str(value)

    def delete(self, key):
        self.values.pop(key, None)


class FakeEffects:
    def __init__(self, loaded=False, supported=True):
        self.is_loaded = loaded
        self.is_supported = supported
        self.calls = []

    def supported(self, name):
        return self.is_supported

    def loaded(self, name):
        return self.is_loaded

    def load(self, name):
        self.calls.append("load")
        self.is_loaded = True
        return True

    def unload(self, name):
        self.calls.append("unload")
        self.is_loaded = False

    def reconfigure(self, name):
        self.calls.append("reconfigure")


class ClickHighlighterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(os.path.join(self.tmp.name, "config.json"))
        self.cfg.set("recording.click_color", [1.0, 0.0, 0.5, 1.0])
        self.cfg.set("recording.click_size", 40)

    def tearDown(self):
        self.tmp.cleanup()

    def test_color_conversion(self):
        self.assertEqual(color_to_kconfig((1.0, 0.5, 0.0, 1.0)), "255,128,0")

    def test_enable_loads_effect_and_disable_restores_everything(self):
        kc = FakeKConfig({"Color1": "10,20,30", "RingSize": "18"})
        fx = FakeEffects(loaded=False)
        hl = ClickHighlighter(self.cfg, kc, fx)
        self.assertTrue(hl.enable())
        self.assertTrue(fx.is_loaded)
        self.assertEqual(kc.values["Color1"], "255,0,128")
        self.assertEqual(kc.values["RingSize"], "40")
        hl.disable()
        self.assertFalse(fx.is_loaded)
        self.assertEqual(kc.values, {"Color1": "10,20,30", "RingSize": "18"})
        self.assertIsNone(self.cfg.get("state.click_restore"))

    def test_already_loaded_effect_stays_loaded(self):
        kc = FakeKConfig()
        fx = FakeEffects(loaded=True)
        hl = ClickHighlighter(self.cfg, kc, fx)
        hl.enable()
        hl.disable()
        self.assertTrue(fx.is_loaded)
        self.assertNotIn("unload", fx.calls)
        self.assertEqual(kc.values, {})

    def test_crash_recovery(self):
        kc = FakeKConfig({"Color1": "1,2,3"})
        fx = FakeEffects(loaded=False)
        ClickHighlighter(self.cfg, kc, fx).enable()   # process "dies" here
        fresh = ClickHighlighter(Config(self.cfg.path), kc, fx)
        fresh.restore_pending()
        self.assertFalse(fx.is_loaded)
        self.assertEqual(kc.values, {"Color1": "1,2,3"})

    def test_unsupported(self):
        hl = ClickHighlighter(self.cfg, FakeKConfig(), FakeEffects(supported=False))
        self.assertFalse(hl.enable())


if __name__ == "__main__":
    unittest.main()
