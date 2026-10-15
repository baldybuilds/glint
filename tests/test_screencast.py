import unittest

from glint.screencast import plan_crop

DP1 = (0, 1440, 3440, 1440)   # a monitor below another one, in logical coords


class PlanCropTests(unittest.TestCase):
    def test_full_monitor_stream(self):
        p = plan_crop((600, 1700, 800, 500), DP1, 3440, 1440)
        self.assertFalse(p.whole)
        self.assertEqual((p.left, p.top, p.width, p.height), (600, 260, 800, 500))
        self.assertEqual((p.right, p.bottom), (3440 - 1400, 1440 - 760))

    def test_hidpi_and_fractional_scaling(self):
        p = plan_crop((100, 100, 200, 100), (0, 0, 1920, 1080), 3840, 2160)   # 200%
        self.assertEqual((p.left, p.top, p.width, p.height), (200, 200, 400, 200))
        p = plan_crop((100, 100, 201, 101), (0, 0, 1920, 1080), 2880, 1620)   # 150%
        self.assertEqual((p.left, p.top), (150, 150))
        self.assertEqual((p.width % 2, p.height % 2), (0, 0))

    def test_region_share_uses_whole_stream(self):
        # The real failure: a 857x323 region was shared but a full monitor assumed.
        p = plan_crop((600, 1700, 800, 500), DP1, 857, 323)
        self.assertTrue(p.whole)
        self.assertEqual((p.left, p.top, p.right, p.bottom), (0, 0, 1, 1))
        self.assertEqual((p.width, p.height), (856, 322))

    def test_selection_outside_stream(self):
        p = plan_crop((100, 100, 300, 200), DP1, 3440, 1440)   # area is on the other monitor
        self.assertTrue(p.whole)

    def test_selection_partly_outside_is_clamped(self):
        p = plan_crop((3300, 2800, 400, 300), DP1, 3440, 1440)
        self.assertFalse(p.whole)
        self.assertEqual((p.left, p.top, p.width, p.height), (3300, 1360, 140, 80))
        self.assertEqual((p.right, p.bottom), (0, 0))

    def test_no_geometry_known(self):
        self.assertTrue(plan_crop((0, 0, 10, 10), None, 1920, 1080).whole)

    def test_crop_never_exceeds_stream(self):
        for rect in ((0, 1440, 3440, 1440), (-50, 1400, 4000, 2000), (1, 1441, 3, 3)):
            p = plan_crop(rect, DP1, 3440, 1440)
            self.assertGreaterEqual(min(p.left, p.top, p.right, p.bottom), 0)
            self.assertEqual(p.left + p.width + p.right, 3440)
            self.assertEqual(p.top + p.height + p.bottom, 1440)


if __name__ == "__main__":
    unittest.main()
