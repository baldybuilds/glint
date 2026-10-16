"""Annotation document model and cairo rendering.

All shape coordinates are in *image pixels* of the base screenshot. The
document maps them to output space (crop + background padding) when
rendering, so cropping and framing are non-destructive.
"""

import copy
import math

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GdkPixbuf, Pango, PangoCairo  # noqa: E402

from .. import geometry, system  # noqa: E402
from .background import paint_background, rounded_rect, soft_shadow  # noqa: E402

# Tool ids, labels, shortcut keys. Order is toolbar order.
TOOLS = [
    ("select", "Select", "v"),
    ("arrow", "Arrow", "a"),
    ("rect", "Rectangle", "r"),
    ("filled", "Filled rectangle", "shift+r"),
    ("ellipse", "Ellipse", "o"),
    ("line", "Line", "l"),
    ("text", "Text", "t"),
    ("pixelate", "Pixelate", "p"),
    ("blur", "Blur", "b"),
    ("spotlight", "Spotlight", "s"),
    ("counter", "Counter", "n"),
    ("pen", "Pen", "d"),
    ("marker", "Highlighter", "h"),
    ("magnify", "Magnifier", "m"),
    ("crop", "Crop", "c"),
]

LINE_STYLES = [("solid", "Solid"), ("dashed", "Dashed"), ("dotted", "Dotted"), ("dashdot", "Dash-dot")]
ARROW_HEADS = [("standard", "Tapered"), ("classic", "Classic"), ("open", "Open"), ("double", "Double")]
MAGNIFY_ZOOMS = [2, 3, 4]
DASHABLE = ("rect", "ellipse", "line", "arrow", "pen")

TEXT_STYLES = [
    ("standard", "Standard"),
    ("outline", "Outline"),
    ("box", "Box"),
    ("rounded", "Rounded box"),
    ("mono", "Monospaced"),
    ("monobox", "Monospaced box"),
]

PALETTE = [
    (1.0, 0.23, 0.19, 1.0),   # red
    (1.0, 0.58, 0.0, 1.0),    # orange
    (1.0, 0.80, 0.0, 1.0),    # yellow
    (0.20, 0.78, 0.35, 1.0),  # green
    (0.0, 0.48, 1.0, 1.0),    # blue
    (0.69, 0.32, 0.87, 1.0),  # purple
    (1.0, 1.0, 1.0, 1.0),     # white
    (0.0, 0.0, 0.0, 1.0),     # black
]


def _seg_distance(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def set_dash(cr, style, width):
    """Dash pattern for a stroke of `width`; round caps make dots and soften
    dash ends (the gaps allow for them)."""
    w = max(1.0, width)
    # Cairo merges a zero-length dash mid-pattern into the gaps, so the
    # dash-dot "dot" gets a tiny length; round caps make it a full dot.
    patterns = {"dashed": [w * 3, w * 2.4], "dotted": [0, w * 2.2], "dashdot": [w * 3, w * 2.2, 0.01, w * 2.2]}
    if style in patterns:
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_dash(patterns[style])
    else:
        cr.set_dash([])


def surface_from_pixbuf(pixbuf):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    cr = cairo.Context(surface)
    system.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return surface


def emoji_surface(emoji, size=256):
    """Render an emoji (in colour, where the font allows) cropped to its ink."""
    probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * 2, size * 2)
    cr = cairo.Context(probe)
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string("Sans")
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(emoji, -1)
    ink, _logical = layout.get_pixel_extents()
    w, h = max(1, ink.width), max(1, ink.height)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(out)
    cr.move_to(-ink.x, -ink.y)
    PangoCairo.show_layout(cr, layout)
    out.flush()
    return out


def luminance(color):
    r, g, b = color[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class Shape:
    kind = ""
    casts_shadow = True

    def __init__(self, color=(1, 0, 0, 1), width=4, shadow=True, dash="solid"):
        self.color = tuple(color)
        self.width = width
        self.shadow = shadow
        self.dash = dash

    def bbox(self):
        raise NotImplementedError

    def hit(self, x, y, tol):
        bx, by, bw, bh = self.bbox()
        return bx - tol <= x <= bx + bw + tol and by - tol <= y <= by + bh + tol

    def translate(self, dx, dy):
        raise NotImplementedError

    def handles(self):
        return []

    def move_handle(self, key, x, y, constrain=False):
        pass

    def is_degenerate(self):
        _x, _y, w, h = self.bbox()
        return w < 2 and h < 2

    def draw(self, cr, doc, color):
        raise NotImplementedError

    def render(self, cr, doc):
        if self.shadow and self.casts_shadow:
            off = max(1.5, self.width * 0.35)
            for extra, alpha in ((off + 1.5, 0.10), (off, 0.22)):
                cr.save()
                cr.translate(0, extra)
                self.draw(cr, doc, (0, 0, 0, alpha))
                cr.restore()
        self.draw(cr, doc, self.color)


class BoxShape(Shape):
    """rect, filled, ellipse, pixelate, blur and spotlight."""

    def __init__(self, kind, x0, y0, x1=None, y1=None, intensity=12, ellipse=False, **kw):
        super().__init__(**kw)
        self.kind = kind
        self.x0, self.y0 = x0, y0
        self.x1 = x0 if x1 is None else x1
        self.y1 = y0 if y1 is None else y1
        self.intensity = intensity
        self.ellipse = ellipse  # spotlight shape
        self.casts_shadow = kind in ("rect", "filled", "ellipse")

    def rect(self):
        return geometry.normalize(self.x0, self.y0, self.x1, self.y1)

    def bbox(self):
        return self.rect()

    def set_end(self, x, y, constrain=False):
        if constrain:
            x, y = geometry.constrain_square(self.x0, self.y0, x, y)
        self.x1, self.y1 = x, y

    def translate(self, dx, dy):
        self.x0 += dx
        self.x1 += dx
        self.y0 += dy
        self.y1 += dy

    def handles(self):
        x, y, w, h = self.rect()
        return [("tl", x, y), ("tr", x + w, y), ("bl", x, y + h), ("br", x + w, y + h)]

    def move_handle(self, key, x, y, constrain=False):
        rx, ry, rw, rh = self.rect()
        left, top, right, bottom = rx, ry, rx + rw, ry + rh
        if "l" in key:
            left = x
        if "r" in key:
            right = x
        if "t" in key:
            top = y
        if "b" in key:
            bottom = y
        self.x0, self.y0, self.x1, self.y1 = left, top, right, bottom

    def hit(self, x, y, tol):
        rx, ry, rw, rh = self.rect()
        if self.kind in ("filled", "pixelate", "blur", "spotlight"):
            return rx - tol <= x <= rx + rw + tol and ry - tol <= y <= ry + rh + tol
        band = tol + self.width / 2
        if self.kind == "ellipse":
            cx, cy, ax, ay = rx + rw / 2, ry + rh / 2, max(rw / 2, 1), max(rh / 2, 1)
            outer = ((x - cx) / (ax + band)) ** 2 + ((y - cy) / (ay + band)) ** 2
            inner_ax, inner_ay = max(ax - band, 0.5), max(ay - band, 0.5)
            inner = ((x - cx) / inner_ax) ** 2 + ((y - cy) / inner_ay) ** 2
            return outer <= 1 <= inner
        inside_outer = rx - band <= x <= rx + rw + band and ry - band <= y <= ry + rh + band
        inside_inner = rx + band < x < rx + rw - band and ry + band < y < ry + rh - band
        return inside_outer and not inside_inner

    def path(self, cr):
        x, y, w, h = self.rect()
        if self.kind == "ellipse" or (self.kind == "spotlight" and self.ellipse):
            if w <= 0 or h <= 0:
                return
            cr.save()
            cr.translate(x + w / 2, y + h / 2)
            cr.scale(w / 2, h / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
        elif self.kind == "spotlight":
            rounded_rect(cr, x, y, w, h, 6)
        else:
            rounded_rect(cr, x, y, w, h, min(self.width * 1.2, 10))

    def draw(self, cr, doc, color):
        x, y, w, h = self.rect()
        if w < 1 or h < 1:
            return
        if self.kind in ("pixelate", "blur"):
            surface, (sx, sy) = doc.effect_surface(self.kind, (x, y, w, h), self.intensity)
            if surface is not None:
                cr.save()
                cr.rectangle(sx, sy, surface.get_width(), surface.get_height())
                cr.clip()
                cr.set_source_surface(surface, sx, sy)
                cr.paint()
                cr.restore()
            return
        if self.kind == "spotlight":
            return  # rendered collectively by the document
        cr.set_source_rgba(*color)
        if self.kind == "filled":
            rounded_rect(cr, x, y, w, h, min(self.width, 8))
            cr.fill()
            return
        cr.set_line_width(self.width)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        set_dash(cr, self.dash, self.width)
        self.path(cr)
        cr.stroke()
        cr.set_dash([])


class LineShape(Shape):
    """line and arrow. `bend` curves it: the perpendicular distance of the
    curve's midpoint from the straight line, so it survives moving the ends."""

    def __init__(self, kind, x0, y0, x1=None, y1=None, head="standard", bend=0.0, **kw):
        super().__init__(**kw)
        self.kind = kind
        self.x0, self.y0 = x0, y0
        self.x1 = x0 if x1 is None else x1
        self.y1 = y0 if y1 is None else y1
        self.head = head
        self.bend = bend

    # -- geometry --------------------------------------------------------------

    def _normal(self):
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return 0.0, 0.0
        return -dy / length, dx / length

    def control(self):
        """Quadratic Bézier control point (on the chord when straight)."""
        nx, ny = self._normal()
        mx, my = (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2
        return mx + nx * 2 * self.bend, my + ny * 2 * self.bend

    def mid_point(self):
        nx, ny = self._normal()
        return (self.x0 + self.x1) / 2 + nx * self.bend, (self.y0 + self.y1) / 2 + ny * self.bend

    def points(self):
        """The line as a polyline (two points when straight)."""
        if abs(self.bend) < 0.5:
            return [(self.x0, self.y0), (self.x1, self.y1)]
        cx, cy = self.control()
        length = math.hypot(self.x1 - self.x0, self.y1 - self.y0) + 2 * abs(self.bend)
        n = int(min(64, max(12, length / 6)))
        out = []
        for i in range(n + 1):
            t = i / n
            a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
            out.append((a * self.x0 + b * cx + c * self.x1, a * self.y0 + b * cy + c * self.y1))
        return out

    def bbox(self):
        pad = self.width * 3 if self.kind == "arrow" else self.width
        pts = self.points()
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)

    def is_degenerate(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0) < 3

    def set_end(self, x, y, constrain=False):
        if constrain:
            x, y = geometry.constrain_angle(self.x0, self.y0, x, y)
        self.x1, self.y1 = x, y

    def translate(self, dx, dy):
        self.x0 += dx
        self.x1 += dx
        self.y0 += dy
        self.y1 += dy

    def handles(self):
        mx, my = self.mid_point()
        return [("p0", self.x0, self.y0), ("p1", self.x1, self.y1), ("mid", mx, my)]

    def move_handle(self, key, x, y, constrain=False):
        if key == "mid":
            nx, ny = self._normal()
            bend = (x - (self.x0 + self.x1) / 2) * nx + (y - (self.y0 + self.y1) / 2) * ny
            self.bend = 0.0 if abs(bend) < 4 else bend   # snap back to straight
        elif key == "p0":
            if constrain:
                x, y = geometry.constrain_angle(self.x1, self.y1, x, y)
            self.x0, self.y0 = x, y
        else:
            self.set_end(x, y, constrain)

    def hit(self, x, y, tol):
        pts = self.points()
        return any(_seg_distance(x, y, *pts[i], *pts[i + 1]) <= tol + self.width for i in range(len(pts) - 1))

    # -- arrows ----------------------------------------------------------------

    def _head_len(self, length):
        if self.head == "standard":
            return min(length * 0.8, max(16, self.width * 4.6))
        return min(length * 0.6, max(12, self.width * 3.6))

    def _direction(self, at_start=False):
        """Unit direction the arrow points at its tip (or start)."""
        cx, cy = self.control()
        if at_start:
            dx, dy = self.x0 - cx, self.y0 - cy
        else:
            dx, dy = self.x1 - cx, self.y1 - cy
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return 0.0, 0.0
        return dx / d, dy / d

    def arrow_polygon(self):
        """Outline of the default tapered arrow, tail to tip and back."""
        pts = self.points()
        length = _length(pts)
        if length < 1:
            return []
        w = self.width
        head_len = self._head_len(length)
        head_half = head_len * 0.5
        tail_half = max(0.75, w * 0.18)
        shaft_half = max(1.0, w * 0.55)
        ux, uy = self._direction()
        nx, ny = -uy, ux
        tx, ty = self.x1, self.y1
        cx, cy = tx - ux * head_len, ty - uy * head_len
        shaft = _trim_end(pts, head_len * 0.78)
        total = _length(shaft) or 1
        left, right, run = [], [], 0.0
        for i, (px, py) in enumerate(shaft):
            if i:
                run += math.hypot(px - shaft[i - 1][0], py - shaft[i - 1][1])
            a = shaft[max(0, i - 1)]
            b = shaft[min(len(shaft) - 1, i + 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            d = math.hypot(dx, dy) or 1
            sx, sy = -dy / d, dx / d
            half = tail_half + (shaft_half - tail_half) * run / total
            left.append((px + sx * half, py + sy * half))
            right.append((px - sx * half, py - sy * half))
        return left + [(cx + nx * head_half, cy + ny * head_half), (tx, ty),
                       (cx - nx * head_half, cy - ny * head_half)] + right[::-1]

    def _head(self, cr, color, at_start=False):
        """Triangular (or open) head at the tip / start."""
        ux, uy = self._direction(at_start)
        nx, ny = -uy, ux
        tx, ty = (self.x0, self.y0) if at_start else (self.x1, self.y1)
        head_len = self._head_len(_length(self.points()))
        half = head_len * (0.62 if self.head == "open" else 0.5)
        cx, cy = tx - ux * head_len, ty - uy * head_len
        cr.set_source_rgba(*color)
        cr.set_dash([])
        cr.move_to(cx + nx * half, cy + ny * half)
        cr.line_to(tx, ty)
        cr.line_to(cx - nx * half, cy - ny * half)
        if self.head == "open":
            cr.set_line_width(max(2.0, self.width))
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke()
        else:
            cr.close_path()
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_width(max(1.0, self.width * 0.25))
            cr.fill_preserve()
            cr.stroke()

    def _stroke(self, cr, pts, color, width):
        if len(pts) < 2:
            return
        cr.set_source_rgba(*color)
        cr.set_line_width(width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        set_dash(cr, self.dash, width)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.stroke()
        cr.set_dash([])

    def draw(self, cr, doc, color):
        pts = self.points()
        if self.kind != "arrow":
            self._stroke(cr, pts, color, self.width)
            return
        if self.head == "standard" and self.dash == "solid":
            poly = self.arrow_polygon()
            if not poly:
                return
            cr.set_source_rgba(*color)
            cr.move_to(*poly[0])
            for p in poly[1:]:
                cr.line_to(*p)
            cr.close_path()
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_width(max(1.0, self.width * 0.25))
            cr.fill_preserve()
            cr.stroke()
            return
        length = _length(pts)
        if length < 1:
            return
        # Stop the shaft inside the head so its end doesn't poke out.
        inset = 0 if self.head == "open" else self._head_len(length) * 0.6
        shaft = _trim_end(pts, inset)
        if self.head == "double":
            shaft = _trim_end(shaft[::-1], inset)[::-1]
        self._stroke(cr, shaft, color, max(2.0, self.width * (1.1 if self.head == "standard" else 1.0)))
        self._head(cr, color)
        if self.head == "double":
            self._head(cr, color, at_start=True)


def _length(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def _trim_end(pts, dist):
    """The polyline with `dist` of its length removed from the end."""
    if dist <= 0 or len(pts) < 2:
        return list(pts)
    out = list(pts)
    while len(out) >= 2 and dist > 0:
        (ax, ay), (bx, by) = out[-2], out[-1]
        seg = math.hypot(bx - ax, by - ay)
        if seg > dist:
            t = (seg - dist) / seg
            out[-1] = (ax + (bx - ax) * t, ay + (by - ay) * t)
            return out
        dist -= seg
        out.pop()
    return out if len(out) >= 2 else list(pts[:1]) * 2


class PathShape(Shape):
    """Freehand pen and highlighter (marker)."""

    def __init__(self, kind, x, y, **kw):
        super().__init__(**kw)
        self.kind = kind
        self.points = [(x, y)]
        self.straight = False
        self.casts_shadow = kind == "pen"

    def add_point(self, x, y, straight=False):
        if straight:
            self.points = [self.points[0], (x, y)]
            self.straight = True
            return
        lx, ly = self.points[-1]
        if math.hypot(x - lx, y - ly) >= 1.5:
            self.points.append((x, y))

    def stroke_width(self):
        return self.width * 4 if self.kind == "marker" else self.width

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        pad = self.stroke_width() / 2
        return (min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)

    def is_degenerate(self):
        return len(self.points) < 2

    def translate(self, dx, dy):
        self.points = [(x + dx, y + dy) for x, y in self.points]

    def hit(self, x, y, tol):
        r = tol + self.stroke_width() / 2
        pts = self.points
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= r
        return any(_seg_distance(x, y, *pts[i], *pts[i + 1]) <= r for i in range(len(pts) - 1))

    def draw(self, cr, doc, color):
        pts = self.points
        if len(pts) < 2:
            return
        cr.save()
        if self.kind == "marker":
            r, g, b, _a = color
            cr.set_source_rgba(r, g, b, 0.42)
        else:
            cr.set_source_rgba(*color)
        cr.set_line_width(self.stroke_width())
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if self.kind == "pen":
            set_dash(cr, self.dash, self.stroke_width())
        cr.move_to(*pts[0])
        # Quadratic smoothing through segment midpoints.
        for i in range(1, len(pts) - 1):
            mx = (pts[i][0] + pts[i + 1][0]) / 2
            my = (pts[i][1] + pts[i + 1][1]) / 2
            x0, y0 = cr.get_current_point()
            cx, cy = pts[i]
            cr.curve_to(x0 + 2 / 3 * (cx - x0), y0 + 2 / 3 * (cy - y0),
                        mx + 2 / 3 * (cx - mx), my + 2 / 3 * (cy - my), mx, my)
        cr.line_to(*pts[-1])
        cr.stroke()
        cr.restore()


_measure_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)


class TextShape(Shape):
    kind = "text"

    def __init__(self, x, y, text="", style="standard", size=None, **kw):
        super().__init__(**kw)
        self.x, self.y = x, y
        self.text = text
        self.style = style
        self.size = size or max(14, kw.get("width", 4) * 6)

    def font(self):
        family = "Monospace" if self.style in ("mono", "monobox") else "Sans"
        desc = Pango.FontDescription.from_string(f"{family} Bold")
        desc.set_absolute_size(self.size * Pango.SCALE)
        return desc

    def layout(self, cr):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font())
        layout.set_text(self.text or " ", -1)
        return layout

    def padding(self):
        return self.size * 0.35 if self.style in ("box", "rounded", "monobox") else 0

    def text_extent(self):
        cr = cairo.Context(_measure_surface)
        _ink, logical = self.layout(cr).get_pixel_extents()
        return logical.width, logical.height

    def bbox(self):
        w, h = self.text_extent()
        p = self.padding()
        return (self.x, self.y, w + 2 * p, h + 2 * p)

    def is_degenerate(self):
        return not self.text.strip()

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy

    @property
    def casts_shadow(self):
        return self.style in ("standard", "mono", "box", "rounded", "monobox")

    def draw(self, cr, doc, color):
        shadow_pass = color != self.color
        layout = self.layout(cr)
        _ink, logical = layout.get_pixel_extents()
        p = self.padding()
        if self.style in ("box", "rounded", "monobox"):
            w, h = logical.width + 2 * p, logical.height + 2 * p
            radius = h / 2 if self.style == "rounded" else self.size * 0.2
            rounded_rect(cr, self.x, self.y, w, h, radius)
            cr.set_source_rgba(*color)
            cr.fill()
            if shadow_pass:
                return
            fg = (0, 0, 0, 1) if luminance(self.color) > 0.6 else (1, 1, 1, 1)
            cr.move_to(self.x + p, self.y + p)
            cr.set_source_rgba(*fg)
            PangoCairo.show_layout(cr, layout)
            return
        cr.move_to(self.x, self.y)
        if self.style == "outline":
            if shadow_pass:
                return
            PangoCairo.layout_path(cr, layout)
            cr.set_source_rgba(*self.color)
            cr.set_line_width(max(2, self.size * 0.22))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke_preserve()
            fg = (0, 0, 0, 1) if luminance(self.color) > 0.75 else (1, 1, 1, 1)
            cr.set_source_rgba(*fg)
            cr.fill()
            return
        cr.set_source_rgba(*color)
        PangoCairo.show_layout(cr, layout)


class CounterShape(Shape):
    kind = "counter"

    def __init__(self, x, y, number=1, style="number", **kw):
        super().__init__(**kw)
        self.x, self.y = x, y
        self.number = number
        self.style = style

    def radius(self):
        return 11 + self.width * 2.2

    def label(self):
        if self.style == "letter":
            n, s = self.number, ""
            while n > 0:
                n, rem = divmod(n - 1, 26)
                s = chr(65 + rem) + s
            return s
        return str(self.number)

    def bbox(self):
        r = self.radius()
        return (self.x - r, self.y - r, 2 * r, 2 * r)

    def is_degenerate(self):
        return False

    def hit(self, x, y, tol):
        return math.hypot(x - self.x, y - self.y) <= self.radius() + tol

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy

    def draw(self, cr, doc, color):
        r = self.radius()
        cr.arc(self.x, self.y, r, 0, 2 * math.pi)
        cr.set_source_rgba(*color)
        cr.fill()
        if color != self.color:
            return
        cr.arc(self.x, self.y, r, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.5, r * 0.1))
        cr.stroke()
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string("Sans Bold")
        desc.set_absolute_size(r * 1.05 * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(self.label(), -1)
        _ink, logical = layout.get_pixel_extents()
        fg = (0, 0, 0, 1) if luminance(self.color) > 0.6 else (1, 1, 1, 1)
        cr.set_source_rgba(*fg)
        cr.move_to(self.x - logical.width / 2, self.y - logical.height / 2)
        PangoCairo.show_layout(cr, layout)


class MagnifierShape(Shape):
    """A round lens showing what is under it, enlarged. Drawn from the
    document's own layers, so pixelated/blurred areas stay redacted."""

    kind = "magnify"

    def __init__(self, x, y, radius=0.0, zoom=2, **kw):
        super().__init__(**kw)
        self.x, self.y = x, y
        self.radius = radius
        self.zoom = zoom

    def bbox(self):
        r = self.radius + self.width / 2
        return (self.x - r, self.y - r, 2 * r, 2 * r)

    def is_degenerate(self):
        return self.radius < 6

    def set_end(self, x, y, constrain=False):
        self.radius = math.hypot(x - self.x, y - self.y)

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy

    def handles(self):
        d = self.radius / math.sqrt(2)
        return [("r", self.x + d, self.y + d)]

    def move_handle(self, key, x, y, constrain=False):
        self.set_end(x, y)

    def hit(self, x, y, tol):
        return math.hypot(x - self.x, y - self.y) <= self.radius + tol + self.width / 2

    def draw(self, cr, doc, color):
        if self.radius < 1:
            return
        cr.arc(self.x, self.y, self.radius, 0, 2 * math.pi)
        if color != self.color:           # shadow pass
            cr.set_source_rgba(*color)
            cr.fill()
            return
        cr.save()
        cr.clip()
        cr.translate(self.x, self.y)
        cr.scale(self.zoom, self.zoom)
        cr.translate(-self.x, -self.y)
        doc.render_layers(cr, upto=self)
        cr.restore()
        cr.arc(self.x, self.y, self.radius, 0, 2 * math.pi)
        cr.set_source_rgba(*color)
        cr.set_line_width(max(2.0, self.width))
        cr.stroke()


class ImageShape(Shape):
    """A pasted / inserted picture or emoji. Resizing keeps its proportions."""

    kind = "image"

    def __init__(self, surface, x, y, w=None, h=None, **kw):
        super().__init__(**kw)
        self.surface = surface
        self.x, self.y = x, y
        self.w = float(w or surface.get_width())
        self.h = float(h or surface.get_height())

    def __deepcopy__(self, memo):
        # The pixels never change: share them between copies (undo, paste).
        clone = ImageShape.__new__(ImageShape)
        clone.__dict__.update({k: (v if k == "surface" else copy.deepcopy(v, memo))
                               for k, v in self.__dict__.items()})
        return clone

    def bbox(self):
        return (self.x, self.y, self.w, self.h)

    def is_degenerate(self):
        return self.w < 2 or self.h < 2

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy

    def handles(self):
        x, y, w, h = self.bbox()
        return [("tl", x, y), ("tr", x + w, y), ("bl", x, y + h), ("br", x + w, y + h)]

    def move_handle(self, key, x, y, constrain=False):
        # Anchor the opposite corner; the width follows the pointer.
        ax = self.x + self.w if "l" in key else self.x
        ay = self.y + self.h if "t" in key else self.y
        aspect = self.h / self.w if self.w else 1
        w = max(8.0, abs(x - ax))
        h = w * aspect
        self.w, self.h = w, h
        self.x = ax - w if "l" in key else ax
        self.y = ay - h if "t" in key else ay

    def draw(self, cr, doc, color):
        sw, sh = self.surface.get_width(), self.surface.get_height()
        if self.w < 1 or self.h < 1 or sw < 1 or sh < 1:
            return
        cr.save()
        cr.translate(self.x, self.y)
        cr.scale(self.w / sw, self.h / sh)
        if color != self.color:           # shadow pass: the picture's silhouette
            cr.set_source_rgba(*color)
            cr.mask_surface(self.surface, 0, 0)
        else:
            cr.set_source_surface(self.surface, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
        cr.restore()


class Document:
    def __init__(self, pixbuf):
        self.base = pixbuf
        self.width = pixbuf.get_width()
        self.height = pixbuf.get_height()
        self.shapes = []
        self.crop = None  # (x, y, w, h) in image px
        self.bg = {"enabled": False, "preset": "ocean", "padding": 64, "radius": 12, "shadow": True}
        self.spotlight_opacity = 0.55
        self._base_surface = None
        self._effects = {}

    # -- undo state ----------------------------------------------------------

    def state(self):
        # Background framing is a view setting, deliberately outside undo.
        return copy.deepcopy((self.shapes, self.crop, self.spotlight_opacity))

    def restore(self, state):
        self.shapes, self.crop, self.spotlight_opacity = copy.deepcopy(state)

    # -- geometry ------------------------------------------------------------

    def image_rect(self):
        return self.crop or (0, 0, self.width, self.height)

    def padding(self, full=False):
        return self.bg["padding"] if (self.bg["enabled"] and not full) else 0

    def output_size(self, full=False):
        if full:
            return self.width, self.height
        _x, _y, w, h = self.image_rect()
        p = self.padding()
        return int(round(w + 2 * p)), int(round(h + 2 * p))

    def offset(self, full=False):
        """Output-space position of image pixel (0, 0)."""
        if full:
            return 0, 0
        x, y, _w, _h = self.image_rect()
        p = self.padding()
        return p - x, p - y

    def to_image(self, ox, oy, full=False):
        dx, dy = self.offset(full)
        return ox - dx, oy - dy

    def to_output(self, ix, iy, full=False):
        dx, dy = self.offset(full)
        return ix + dx, iy + dy

    # -- shapes --------------------------------------------------------------

    def shape_at(self, x, y, tol=4):
        for shape in reversed(self.shapes):
            if shape.hit(x, y, tol):
                return shape
        return None

    def next_counter(self):
        return 1 + sum(1 for s in self.shapes if s.kind == "counter")

    def renumber_counters(self):
        n = 1
        for s in self.shapes:
            if s.kind == "counter":
                s.number = n
                n += 1

    # -- rendering -----------------------------------------------------------

    def base_surface(self):
        if self._base_surface is None:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
            cr = cairo.Context(surface)
            system.cairo_set_source_pixbuf(cr, self.base, 0, 0)
            cr.paint()
            self._base_surface = surface
        return self._base_surface

    def effect_surface(self, kind, rect, intensity):
        r = geometry.intersect(tuple(int(round(v)) for v in rect), (0, 0, self.width, self.height))
        if r is None or r[2] < 1 or r[3] < 1:
            return None, (0, 0)
        key = (kind, r, int(intensity))
        if key not in self._effects:
            if len(self._effects) > 64:
                self._effects.clear()
            sub = self.base.new_subpixbuf(*r)
            pb = pixelate(sub, intensity) if kind == "pixelate" else blur(sub, intensity)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, r[2], r[3])
            cr = cairo.Context(surface)
            system.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
            self._effects[key] = surface
        return self._effects[key], (r[0], r[1])

    def render(self, cr, full=False, skip=None):
        ix, iy, iw, ih = (0, 0, self.width, self.height) if full else self.image_rect()
        framed = self.bg["enabled"] and not full
        pad = self.padding(full)
        if framed:
            ow, oh = self.output_size()
            paint_background(cr, ow, oh, self.bg["preset"])
        radius = self.bg["radius"] if framed else 0

        cr.save()
        cr.translate(pad - ix, pad - iy)
        if framed and self.bg["shadow"] and pad > 0:
            soft_shadow(cr, ix, iy, iw, ih, radius)
        cr.save()
        rounded_rect(cr, ix, iy, iw, ih, radius)
        cr.clip()
        self.render_layers(cr, skip=skip)
        cr.restore()
        cr.restore()

    def render_layers(self, cr, skip=None, upto=None):
        """The screenshot and its shapes in image coordinates. With `upto`
        (a magnifier's view) only shapes below it, and no spotlight dimming;
        pixelate and blur always apply so nothing redacted shows through."""
        cr.set_source_surface(self.base_surface(), 0, 0)
        cr.paint()
        for s in self.shapes:
            if s.kind in ("pixelate", "blur") and s is not skip:
                s.render(cr, self)
        if upto is None:
            self._render_spotlight(cr)
        for s in self.shapes:
            if s is upto:
                break
            if s.kind in ("pixelate", "blur", "spotlight") or s is skip:
                continue
            s.render(cr, self)

    def _render_spotlight(self, cr):
        spots = [s for s in self.shapes if s.kind == "spotlight"]
        if not spots:
            return
        cr.push_group()
        cr.set_source_rgba(0, 0, 0, self.spotlight_opacity)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        for s in spots:
            s.path(cr)
            cr.fill()
        cr.pop_group_to_source()
        cr.paint()

    def export_surface(self):
        w, h = self.output_size()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, w), max(1, h))
        cr = cairo.Context(surface)
        self.render(cr)
        surface.flush()
        return surface

    def export_png(self, path):
        self.export_surface().write_to_png(path)
        return path


def pixelate(pixbuf, block):
    w, h = pixbuf.get_width(), pixbuf.get_height()
    block = max(2, int(block))
    small = pixbuf.scale_simple(max(1, w // block), max(1, h // block), GdkPixbuf.InterpType.BILINEAR)
    return small.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)


def blur(pixbuf, strength):
    w, h = pixbuf.get_width(), pixbuf.get_height()
    out = pixbuf
    for factor in (max(2, int(strength)), max(2, int(strength) // 2)):
        small = out.scale_simple(max(1, w // factor), max(1, h // factor), GdkPixbuf.InterpType.BILINEAR)
        out = small.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    return out
