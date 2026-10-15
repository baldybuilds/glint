"""Small custom widgets."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from . import system  # noqa: E402


# PyGObject pitfall: the value *returned* by Graphene.Rect().init(...) or
# Gsk.RoundedRect().init_from_rect(...) points into the temporary that was
# just freed (a dangling copy). Always initialise a named object and use it.
def rect(x, y, w, h):
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


def rounded(x, y, w, h, radius):
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect(x, y, w, h), radius)
    return rr


def point(x, y):
    p = Graphene.Point()
    p.init(x, y)
    return p


class ImageView(Gtk.Widget):
    """Draws a texture scaled to an explicit display size.

    Unlike Gtk.Picture, its natural size is the size we ask for rather than
    the texture's pixel size, so undecorated windows built around it get
    exactly that size.
    """

    def __init__(self, texture, width, height, radius=0, border=None, cover=False):
        super().__init__()
        self.texture = texture
        self.display = (int(width), int(height))
        self.radius = radius
        self.border = border  # (r, g, b, a) or None
        self.cover = cover

    def set_texture(self, texture):
        self.texture = texture
        self.queue_draw()

    def set_display_size(self, width, height):
        self.display = (max(1, int(width)), max(1, int(height)))
        self.queue_resize()

    def set_style(self, radius=None, border=False):
        if radius is not None:
            self.radius = radius
        self.border = border if border is not False else self.border
        self.queue_draw()

    def do_measure(self, orientation, _for_size):
        size = self.display[0] if orientation == Gtk.Orientation.HORIZONTAL else self.display[1]
        return size, size, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        bounds = rect(0, 0, w, h)
        clip = rounded(0, 0, w, h, self.radius)
        snapshot.push_rounded_clip(clip)
        if self.texture is not None:
            tw, th = self.texture.get_width(), self.texture.get_height()
            if self.cover and tw and th:
                scale = max(w / tw, h / th)
                dw, dh = tw * scale, th * scale
                dest = rect((w - dw) / 2, (h - dh) / 2, dw, dh)
            else:
                dest = bounds
            snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR, dest)
        snapshot.pop()
        if self.border:
            snapshot.append_border(clip, [1, 1, 1, 1], [system.rgba(*self.border)] * 4)
