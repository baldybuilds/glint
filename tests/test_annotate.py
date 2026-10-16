import unittest

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from glint.annotate import background  # noqa: E402
import copy  # noqa: E402

import cairo  # noqa: E402

from glint.annotate.model import (BoxShape, CounterShape, Document, ImageShape, LineShape,  # noqa: E402
                                  MagnifierShape, PathShape, TextShape, emoji_surface, set_dash)


def solid(w, h, rgba=0x3366CCFF):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    pb.fill(rgba)
    return pb


def pixel(surface, x, y):
    data = surface.get_data()
    off = y * surface.get_stride() + x * 4
    b, g, r, a = data[off:off + 4]
    return r, g, b, a


class DocumentTests(unittest.TestCase):
    def test_plain_export_matches_base(self):
        doc = Document(solid(40, 30))
        s = doc.export_surface()
        self.assertEqual((s.get_width(), s.get_height()), (40, 30))
        self.assertEqual(pixel(s, 5, 5), (0x33, 0x66, 0xCC, 0xFF))

    def test_crop_and_background_geometry(self):
        doc = Document(solid(200, 100))
        doc.crop = (50, 20, 100, 60)
        self.assertEqual(doc.output_size(), (100, 60))
        doc.bg.update(enabled=True, padding=10)
        self.assertEqual(doc.output_size(), (120, 80))
        self.assertEqual(doc.to_output(50, 20), (10, 10))
        self.assertEqual(doc.to_image(10, 10), (50, 20))
        self.assertEqual(doc.output_size(full=True), (200, 100))

    def test_filled_rect_draws_colour(self):
        doc = Document(solid(50, 50))
        doc.shapes.append(BoxShape("filled", 10, 10, 40, 40, color=(1, 0, 0, 1), width=2, shadow=False))
        self.assertEqual(pixel(doc.export_surface(), 25, 25), (255, 0, 0, 255))

    def test_spotlight_dims_outside_only(self):
        doc = Document(solid(60, 60, 0xFFFFFFFF))
        doc.spotlight_opacity = 0.5
        doc.shapes.append(BoxShape("spotlight", 20, 20, 40, 40))
        s = doc.export_surface()
        self.assertEqual(pixel(s, 30, 30)[:3], (255, 255, 255))
        self.assertLess(pixel(s, 5, 5)[0], 140)

    def test_pixelate_changes_pixels(self):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
        pb.fill(0x000000FF)
        pb.new_subpixbuf(0, 0, 16, 32).fill(0xFFFFFFFF)
        doc = Document(pb)
        before = pixel(doc.export_surface(), 15, 5)
        doc.shapes.append(BoxShape("pixelate", 0, 0, 32, 32, intensity=32))
        after = pixel(doc.export_surface(), 15, 5)
        self.assertNotEqual(before, after)

    def test_hit_testing(self):
        rect = BoxShape("rect", 10, 10, 50, 50, width=4)
        self.assertTrue(rect.hit(10, 30, 2))
        self.assertFalse(rect.hit(30, 30, 2))
        filled = BoxShape("filled", 10, 10, 50, 50)
        self.assertTrue(filled.hit(30, 30, 2))
        line = LineShape("arrow", 0, 0, 100, 0, width=4)
        self.assertTrue(line.hit(50, 3, 2))
        self.assertFalse(line.hit(50, 30, 2))
        self.assertTrue(CounterShape(20, 20, width=4).hit(25, 25, 0))

    def test_handles_resize(self):
        rect = BoxShape("rect", 10, 10, 50, 50)
        rect.move_handle("br", 80, 90)
        self.assertEqual(rect.rect(), (10, 10, 70, 80))
        line = LineShape("line", 0, 0, 10, 10)
        line.move_handle("p0", 5, 0)
        self.assertEqual((line.x0, line.y0), (5, 0))

    def test_arrow_polygon_tip(self):
        arrow = LineShape("arrow", 0, 0, 100, 0, width=6)
        pts = arrow.arrow_polygon()
        self.assertEqual(len(pts), 7)
        self.assertEqual(pts[3], (100, 0))

    def test_counters_and_labels(self):
        doc = Document(solid(10, 10))
        doc.shapes += [CounterShape(0, 0, number=1), CounterShape(0, 0, number=5)]
        self.assertEqual(doc.next_counter(), 3)
        doc.renumber_counters()
        self.assertEqual([s.number for s in doc.shapes], [1, 2])
        self.assertEqual(CounterShape(0, 0, number=28, style="letter").label(), "AB")

    def test_undo_state_roundtrip(self):
        doc = Document(solid(10, 10))
        state = doc.state()
        doc.shapes.append(PathShape("pen", 1, 1))
        doc.crop = (0, 0, 5, 5)
        doc.restore(state)
        self.assertEqual((doc.shapes, doc.crop), ([], None))

    def test_text_shape_measures_and_renders(self):
        text = TextShape(5, 5, text="Hello", style="box", size=20)
        x, y, w, h = text.bbox()
        self.assertGreater(w, 40)
        self.assertGreater(h, 20)
        doc = Document(solid(200, 60))
        doc.shapes.append(text)
        doc.export_surface()
        self.assertTrue(TextShape(0, 0, text="  ").is_degenerate())

    def test_degenerate_detection(self):
        self.assertTrue(BoxShape("rect", 5, 5, 6, 6).is_degenerate())
        self.assertTrue(LineShape("line", 5, 5, 6, 6).is_degenerate())
        self.assertFalse(LineShape("line", 5, 5, 30, 6).is_degenerate())


def coloured(surface, rgb, tol=40):
    """Number of pixels close to `rgb`."""
    n = 0
    for y in range(surface.get_height()):
        for x in range(surface.get_width()):
            r, g, b, _a = pixel(surface, x, y)
            if abs(r - rgb[0]) + abs(g - rgb[1]) + abs(b - rgb[2]) <= tol:
                n += 1
    return n


RED = (1, 0, 0, 1)


class LineStyleTests(unittest.TestCase):
    def render(self, shape):
        doc = Document(solid(120, 60, 0xFFFFFFFF))
        doc.shapes.append(shape)
        return coloured(doc.export_surface(), (255, 0, 0))

    def test_dash_patterns(self):
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        set_dash(cr, "dashed", 4)
        self.assertEqual(len(cr.get_dash()[0]), 2)
        set_dash(cr, "dashdot", 4)
        self.assertEqual(len(cr.get_dash()[0]), 4)
        set_dash(cr, "solid", 4)
        self.assertEqual(cr.get_dash()[0], ())

    def test_dashed_lines_leave_gaps(self):
        kw = dict(color=RED, width=4, shadow=False)
        solid_px = self.render(LineShape("line", 10, 30, 110, 30, **kw))
        for style in ("dashed", "dotted", "dashdot"):
            styled = self.render(LineShape("line", 10, 30, 110, 30, dash=style, **kw))
            self.assertLess(styled, solid_px * 0.85, style)
            self.assertGreater(styled, 0, style)
        rect = self.render(BoxShape("rect", 10, 10, 110, 50, dash="dashed", **kw))
        self.assertLess(rect, self.render(BoxShape("rect", 10, 10, 110, 50, **kw)))


class ArrowTests(unittest.TestCase):
    def test_bend_curves_through_mid_handle(self):
        a = LineShape("arrow", 0, 0, 100, 0, width=4)
        a.move_handle("mid", 50, 30)
        self.assertAlmostEqual(abs(a.bend), 30)
        mx, my = a.mid_point()
        self.assertAlmostEqual((mx, my), (50, 30))
        self.assertTrue(a.hit(50, 30, 2))        # on the curve
        self.assertFalse(a.hit(50, 0, 2))        # not on the old straight line
        self.assertGreaterEqual(a.bbox()[1] + a.bbox()[3], 30)
        a.translate(10, 5)
        self.assertAlmostEqual(a.mid_point()[1], 35)
        a.move_handle("mid", 60, 6)              # close to the chord: straight again
        self.assertEqual(a.bend, 0)

    def test_curved_arrow_tip_and_heads(self):
        a = LineShape("arrow", 0, 0, 100, 0, width=6, bend=25)
        pts = a.arrow_polygon()
        self.assertIn((100, 0), pts)
        for head in ("standard", "classic", "open", "double"):
            doc = Document(solid(140, 80, 0xFFFFFFFF))
            doc.shapes.append(LineShape("arrow", 20, 40, 120, 40, head=head, color=RED, width=5, shadow=False))
            out = doc.export_surface()
            near_tip = sum(pixel(out, 116, y)[1] < 100 for y in range(34, 47))
            near_start = sum(pixel(out, 24, y)[1] < 100 for y in range(30, 51))
            self.assertGreater(near_tip, 0, head)
            if head == "double":
                self.assertGreater(near_start, 3, head)


class MagnifierTests(unittest.TestCase):
    def doc_with_red_dot(self):
        pb = solid(100, 100, 0x0000FFFF)
        pb.new_subpixbuf(48, 48, 4, 4).fill(0xFF0000FF)     # part of the screenshot itself
        return Document(pb)

    def test_enlarges_what_is_below(self):
        doc = self.doc_with_red_dot()
        plain = coloured(doc.export_surface(), (255, 0, 0))
        doc.shapes.append(MagnifierShape(50, 50, radius=30, zoom=4, color=(1, 1, 1, 1), shadow=False))
        self.assertGreater(coloured(doc.export_surface(), (255, 0, 0)), plain * 8)

    def test_never_reveals_redacted_areas(self):
        doc = self.doc_with_red_dot()
        doc.shapes.append(BoxShape("blur", 30, 30, 70, 70, intensity=40))
        doc.shapes.append(MagnifierShape(50, 50, radius=30, zoom=4, color=(1, 1, 1, 1), shadow=False))
        self.assertEqual(coloured(doc.export_surface(), (255, 0, 0), tol=20), 0)

    def test_geometry(self):
        m = MagnifierShape(10, 10)
        m.set_end(13, 14)
        self.assertEqual(m.radius, 5)
        self.assertTrue(m.is_degenerate())
        m.move_handle("r", 40, 10)
        self.assertEqual(m.radius, 30)
        self.assertTrue(m.hit(35, 10, 0))


class ImageShapeTests(unittest.TestCase):
    def test_resize_keeps_aspect_and_copies_share_pixels(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 20)
        img = ImageShape(surface, 10, 10)
        img.move_handle("br", 90, 0)
        self.assertEqual((img.x, img.y, img.w, img.h), (10, 10, 80, 40))
        img.move_handle("tl", 50, 0)          # anchored at the bottom-right corner
        self.assertEqual((img.x + img.w, img.y + img.h), (90, 50))
        self.assertAlmostEqual(img.h, img.w / 2)
        clone = copy.deepcopy(img)
        self.assertIs(clone.surface, surface)
        clone.translate(5, 5)
        self.assertNotEqual(clone.x, img.x)
        doc = Document(solid(20, 20))
        doc.shapes.append(img)
        doc.restore(doc.state())                 # undo snapshots work with images

    def test_renders_and_emoji(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
        cr = cairo.Context(surface)
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        doc = Document(solid(60, 60, 0xFFFFFFFF))
        doc.shapes.append(ImageShape(surface, 10, 10, 30, 30, shadow=False))
        self.assertGreater(coloured(doc.export_surface(), (255, 0, 0)), 700)
        emoji = emoji_surface("🎉", size=64)
        self.assertGreater(emoji.get_width(), 10)


class BackgroundTests(unittest.TestCase):
    def test_compose_adds_padding(self):
        import cairo
        img = cairo.ImageSurface(cairo.FORMAT_ARGB32, 50, 40)
        out = background.compose(img, "ocean", padding=20, radius=6, shadow=True)
        self.assertEqual((out.get_width(), out.get_height()), (90, 80))
        self.assertEqual(pixel(out, 1, 1)[3], 255)  # gradient is opaque

    def test_transparent_preset(self):
        import cairo
        img = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
        out = background.compose(img, "transparent", padding=10, shadow=False)
        self.assertEqual(pixel(out, 1, 1)[3], 0)

    def test_hex(self):
        self.assertEqual(background.hex_to_rgb("#ff0000"), (1.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
