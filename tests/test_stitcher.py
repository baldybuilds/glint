import random
import unittest

from glint.stitcher import Stitcher, find_shift


def page(height, seed=1, width=8):
    """A tall 'document': unique, random rows."""
    rnd = random.Random(seed)
    return [bytes(rnd.randrange(256) for _ in range(width)) for _ in range(height)]


def viewport(doc, offset, h, header=0, footer=0, width=8):
    head = [b"H" * width] * header
    foot = [b"F" * width] * footer
    body_h = h - header - footer
    return head + doc[offset:offset + body_h] + foot


class FindShiftTests(unittest.TestCase):
    def test_detects_scroll(self):
        doc = [hash(r) for r in page(300)]
        prev, new = doc[0:100], doc[37:137]
        self.assertEqual(find_shift(prev, new)[0], 37)

    def test_no_scroll(self):
        doc = [hash(r) for r in page(100)]
        self.assertEqual(find_shift(doc, doc)[0], 0)

    def test_no_overlap(self):
        doc = [hash(r) for r in page(400)]
        shift, _ = find_shift(doc[0:100], doc[200:300])
        self.assertIsNone(shift)

    def test_blank_runs_do_not_confuse(self):
        rows = page(200)
        for i in range(60, 90):
            rows[i] = b"\x00" * 8
        doc = [hash(r) for r in rows]
        self.assertEqual(find_shift(doc[40:140], doc[52:152])[0], 12)


class StitcherTests(unittest.TestCase):
    def test_reconstructs_document(self):
        doc = page(600)
        st = Stitcher()
        for off in range(0, 600 - 120 + 1, 20):
            st.add(viewport(doc, off, 120))
        self.assertEqual(st.result_rows(), doc[:len(st.result_rows())])
        self.assertEqual(len(st.result_rows()), 600)

    def test_sticky_header_and_footer_kept_once(self):
        doc = page(500, seed=7)
        st = Stitcher()
        for off in range(0, 500 - 100 + 1, 20):
            st.add(viewport(doc, off, 140, header=24, footer=16))
        out = st.result_rows()
        self.assertEqual(out[:24], [b"H" * 8] * 24)
        self.assertEqual(out[-16:], [b"F" * 8] * 16)
        self.assertEqual(out[24:-16], doc[:500])

    def test_ignores_scrolling_back_up(self):
        doc = page(400, seed=3)
        st = Stitcher()
        for off in (0, 30, 10, 60, 90):
            st.add(viewport(doc, off, 100))
        self.assertEqual(st.result_rows(), doc[:190])

    def test_identical_frames_do_nothing(self):
        doc = page(200)
        st = Stitcher()
        frame = viewport(doc, 0, 100)
        st.add(frame)
        self.assertFalse(st.add(frame))
        self.assertEqual(st.result_rows(), frame)

    def test_respects_max_height(self):
        doc = page(1000, seed=9)
        st = Stitcher(max_height=300)
        for off in range(0, 900, 30):
            st.add(viewport(doc, off, 100))
        self.assertLessEqual(len(st.result_rows()), 300)


if __name__ == "__main__":
    unittest.main()
