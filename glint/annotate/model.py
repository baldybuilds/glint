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
    ("crop", "Crop", "c"),
]

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


def luminance(color):
    r, g, b = color[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class Shape:
    kind = ""
    casts_shadow = True

    def __init__(self, color=(1, 0, 0, 1), width=4, shadow=True):
        self.color = tuple(color)
        self.width = width
        self.shadow = shadow

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
        self.path(cr)
        cr.stroke()


class LineShape(Shape):
    """line and arrow."""

    def __init__(self, kind, x0, y0, x1=None, y1=None, **kw):
        super().__init__(**kw)
        self.kind = kind
        self.x0, self.y0 = x0, y0
        self.x1 = x0 if x1 is None else x1
        self.y1 = y0 if y1 is None else y1

    def bbox(self):
        pad = self.width * 3 if self.kind == "arrow" else self.width
        x, y, w, h = geometry.normalize(self.x0, self.y0, self.x1, self.y1)
        return (x - pad, y - pad, w + 2 * pad, h + 2 * pad)

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
        return [("p0", self.x0, self.y0), ("p1", self.x1, self.y1)]

    def move_handle(self, key, x, y, constrain=False):
        if key == "p0":
            if constrain:
                x, y = geometry.constrain_angle(self.x1, self.y1, x, y)
            self.x0, self.y0 = x, y
        else:
            self.set_end(x, y, constrain)

    def hit(self, x, y, tol):
        return _seg_distance(x, y, self.x0, self.y0, self.x1, self.y1) <= tol + self.width

    def arrow_polygon(self):
        x0, y0, x1, y1 = self.x0, self.y0, self.x1, self.y1
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1:
            return []
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        nx, ny = -uy, ux
        w = self.width
        head_len = min(L * 0.8, max(16, w * 4.6))
        head_half = head_len * 0.5
        tail_half = max(0.75, w * 0.18)
        shaft_half = max(1.0, w * 0.55)
        bx, by = x1 - ux * head_len * 0.78, y1 - uy * head_len * 0.78
        cx, cy = x1 - ux * head_len, y1 - uy * head_len
        return [
            (x0 + nx * tail_half, y0 + ny * tail_half),
            (bx + nx * shaft_half, by + ny * shaft_half),
            (cx + nx * head_half, cy + ny * head_half),
            (x1, y1),
            (cx - nx * head_half, cy - ny * head_half),
            (bx - nx * shaft_half, by - ny * shaft_half),
            (x0 - nx * tail_half, y0 - ny * tail_half),
        ]

    def draw(self, cr, doc, color):
        cr.set_source_rgba(*color)
        if self.kind == "arrow":
            pts = self.arrow_polygon()
            if not pts:
                return
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.close_path()
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_width(max(1.0, self.width * 0.25))
            cr.fill_preserve()
            cr.stroke()
            return
        cr.set_line_width(self.width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(self.x0, self.y0)
        cr.line_to(self.x1, self.y1)
        cr.stroke()


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
        cr.set_source_surface(self.base_surface(), 0, 0)
        cr.paint()
        for s in self.shapes:
            if s.kind in ("pixelate", "blur") and s is not skip:
                s.render(cr, self)
        self._render_spotlight(cr)
        for s in self.shapes:
            if s.kind in ("pixelate", "blur", "spotlight") or s is skip:
                continue
            s.render(cr, self)
        cr.restore()
        cr.restore()

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
