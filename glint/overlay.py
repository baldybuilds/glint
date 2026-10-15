"""Full-screen selection overlay (area / window / fullscreen picking).

One undecorated fullscreen window per monitor shares a single controller so a
selection, crosshair and window highlight behave as one surface spanning the
desktop. In *frozen* mode the windows paint the captured desktop image (which
enables the magnifier); in *live* mode they are transparent and only dim the
real screen, which is what recording and scrolling capture need.

Interaction:
  drag            select an area (Shift = square, hold Space = move it)
  click           capture the window under the pointer (or the whole display)
  Space           toggle window mode
  F               capture the display under the pointer
  Enter           confirm the current selection
  Esc / right     cancel
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Gsk, Gtk, Pango  # noqa: E402

from . import geometry, kwin, system, widgets  # noqa: E402

DRAG_THRESHOLD = 4
LOUPE_SIZE = 132
LOUPE_ZOOM = 8


_rect = widgets.rect
_rounded = widgets.rounded


WHITE = system.rgba(1, 1, 1, 1)
BLACK = system.rgba(0, 0, 0, 1)
ACCENT = system.rgba(0.24, 0.56, 1.0, 1)
LABEL_BG = system.rgba(0.08, 0.08, 0.09, 0.82)


class OverlayController:
    def __init__(self, monitors, shot=None, windows=None, mode="area", hint=None,
                 show_crosshair=True, show_magnifier=True, on_done=None, allow_window=True):
        self.monitors = monitors
        self.shot = shot
        self.windows_info = list(windows or [])
        self.mode = mode if (mode != "window" or self.windows_info) else "area"
        self.allow_window = allow_window and bool(self.windows_info)
        self.hint = hint
        self.show_crosshair = show_crosshair
        self.show_magnifier = show_magnifier and shot is not None
        self.on_done = on_done

        self.cursor = None            # global (x, y)
        self.press = None             # global press point
        self.anchor = None            # global anchor of the drag
        self.current = None           # global current point of the drag
        self.dragging = False
        self.moving = False
        self.selection = None         # finished/in-progress rect (global)
        self.shift = False
        self.finished = False
        self._pixels = None
        self.views = []
        self.windows = []

        if shot is not None:
            pb = shot.pixbuf
            self._pixels = pb.read_pixel_bytes().get_data()
            self._rowstride = pb.get_rowstride()
            self._channels = pb.get_n_channels()

        for monitor in monitors:
            win = OverlayWindow(self, monitor)
            self.windows.append(win)
            self.views.append(win.view)

    def present(self):
        for win in self.windows:
            win.present_on_monitor()

    # -- state ---------------------------------------------------------------

    def redraw(self):
        for v in self.views:
            v.queue_draw()

    def hovered_window(self):
        if self.mode != "window" and not (self.allow_window and not self.dragging):
            return None
        if self.cursor is None:
            return None
        for w in reversed(self.windows_info):
            if geometry.contains((w["x"], w["y"], w["w"], w["h"]), *self.cursor):
                return w
        return None

    def highlight_rect(self):
        if self.dragging and self.selection:
            return self.selection, "area"
        if self.selection and not self.dragging:
            return self.selection, "area"
        if self.press is None:
            w = self.hovered_window()
            if w:
                return (w["x"], w["y"], w["w"], w["h"]), "window"
        return None, None

    def pixel_hex(self, gx, gy):
        if self._pixels is None:
            return None
        px = self.shot.pixel_rect((gx, gy, 1, 1))
        if px is None:
            return None
        x, y = px[0], px[1]
        off = y * self._rowstride + x * self._channels
        try:
            r, g, b = self._pixels[off], self._pixels[off + 1], self._pixels[off + 2]
        except IndexError:
            return None
        return f"#{r:02X}{g:02X}{b:02X}"

    # -- input ---------------------------------------------------------------

    def motion(self, gx, gy):
        self.cursor = (gx, gy)
        if self.press is not None and not self.dragging:
            if abs(gx - self.press[0]) > DRAG_THRESHOLD or abs(gy - self.press[1]) > DRAG_THRESHOLD:
                self.dragging = True
                self.anchor = self.press
        if self.dragging:
            if self.moving and self.current is not None:
                dx, dy = gx - self.current[0], gy - self.current[1]
                self.anchor = (self.anchor[0] + dx, self.anchor[1] + dy)
            self.current = (gx, gy)
            x1, y1 = gx, gy
            if self.shift:
                x1, y1 = geometry.constrain_square(self.anchor[0], self.anchor[1], gx, gy)
            self.selection = self._clamp(geometry.normalize(self.anchor[0], self.anchor[1], x1, y1))
        self.redraw()

    def _clamp(self, rect):
        mon = self._monitor_for(self.anchor or self.cursor)
        if mon is None:
            return rect
        return geometry.intersect(rect, mon.rect) or (rect[0], rect[1], 0, 0)

    def _monitor_for(self, point):
        if point is None:
            return None
        for m in self.monitors:
            if geometry.contains(m.rect, *point):
                return m
        return None

    def button_press(self, gx, gy):
        self.press = (gx, gy)
        self.current = (gx, gy)
        self.selection = None

    def button_release(self, gx, gy):
        if self.press is None:
            return
        was_drag = self.dragging
        self.press = None
        self.dragging = False
        self.moving = False
        if was_drag:
            sel = self.selection
            if sel and sel[2] >= 2 and sel[3] >= 2:
                self.finish({"kind": "area", "rect": tuple(int(round(v)) for v in sel)})
            else:
                self.selection = None
                self.redraw()
            return
        win = self.hovered_window() if (self.mode == "window" or self.allow_window) else None
        if win:
            rect = (win["x"], win["y"], win["w"], win["h"])
            self.finish({"kind": "window", "rect": rect, "window": win})
        else:
            self.capture_display_at(gx, gy)

    def capture_display_at(self, gx, gy):
        mon = self._monitor_for((gx, gy)) or (self.monitors[0] if self.monitors else None)
        if mon:
            self.finish({"kind": "fullscreen", "rect": mon.rect, "monitor": mon.connector})

    def key_pressed(self, keyval, state):
        name = Gdk.keyval_name(keyval) or ""
        if name in ("Shift_L", "Shift_R"):
            self.shift = True
            return True
        if name == "Escape":
            self.finish(None)
            return True
        if name == "space":
            if self.dragging:
                self.moving = True
            elif self.windows_info:
                self.mode = "window" if self.mode == "area" else "area"
                self.redraw()
            return True
        if name in ("f", "F") and self.cursor:
            self.capture_display_at(*self.cursor)
            return True
        if name in ("Return", "KP_Enter"):
            if self.selection and self.selection[2] >= 2 and self.selection[3] >= 2:
                self.finish({"kind": "area", "rect": tuple(int(round(v)) for v in self.selection)})
            elif self.cursor:
                self.capture_display_at(*self.cursor)
            return True
        return False

    def key_released(self, keyval, state):
        name = Gdk.keyval_name(keyval) or ""
        if name in ("Shift_L", "Shift_R"):
            self.shift = False
        elif name == "space":
            self.moving = False

    def finish(self, result):
        if self.finished:
            return
        self.finished = True
        for win in self.windows:
            win.destroy()
        if self.on_done:
            # Let the windows actually unmap before the caller does anything.
            GLib.idle_add(lambda: self.on_done(result) and False)


class OverlayWindow(Gtk.Window):
    def __init__(self, controller, monitor):
        super().__init__()
        self.controller = controller
        self.monitor = monitor
        self.set_title(kwin.place_title("overlay"))
        self.set_decorated(False)
        self.add_css_class("glint-overlay")
        self.view = SelectionView(controller, monitor)
        self.set_child(self.view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _code, st: controller.key_pressed(kv, st))
        keys.connect("key-released", lambda _c, kv, _code, st: controller.key_released(kv, st))
        self.add_controller(keys)

    def present_on_monitor(self):
        if self.monitor.gdk_monitor is not None:
            self.fullscreen_on_monitor(self.monitor.gdk_monitor)
        else:
            self.fullscreen()
        self.present()


class SelectionView(Gtk.Widget):
    def __init__(self, controller, monitor):
        super().__init__()
        self.c = controller
        self.monitor = monitor
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_cursor_from_name("crosshair")
        self.texture = None
        self.tex_ratio = 1.0
        if controller.shot is not None:
            pb = controller.shot.crop(monitor.rect)
            if pb is not None:
                self.texture = system.texture_from_pixbuf(pb)
                self.tex_ratio = pb.get_width() / max(1, monitor.width)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("enter", self._on_motion)
        self.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)

    def _global(self, x, y):
        return (self.monitor.x + x, self.monitor.y + y)

    def _on_motion(self, _ctrl, x, y):
        self.c.motion(*self._global(x, y))

    def _on_pressed(self, gesture, _n, x, y):
        button = gesture.get_current_button()
        if button == 3:
            self.c.finish(None)
            return
        if button != 1:
            return
        self.grab_focus()
        self.c.button_press(*self._global(x, y))
        self.c.motion(*self._global(x, y))

    def _on_released(self, gesture, _n, x, y):
        if gesture.get_current_button() != 1:
            return
        self.c.button_release(*self._global(x, y))

    # -- drawing -------------------------------------------------------------

    def _local(self, rect):
        return (rect[0] - self.monitor.x, rect[1] - self.monitor.y, rect[2], rect[3])

    def _layout(self, text, font="Sans Bold 10"):
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription.from_string(font))
        _ink, logical = layout.get_pixel_extents()
        return layout, logical.width, logical.height

    def _pill(self, snapshot, text, x, y, font="Sans Bold 10", pad=(8, 4)):
        layout, tw, th = self._layout(text, font)
        w, h = tw + pad[0] * 2, th + pad[1] * 2
        snapshot.push_rounded_clip(_rounded(x, y, w, h, h / 2))
        snapshot.append_color(LABEL_BG, _rect(x, y, w, h))
        snapshot.pop()
        snapshot.save()
        snapshot.translate(widgets.point(x + pad[0], y + pad[1]))
        snapshot.append_layout(layout, WHITE)
        snapshot.restore()
        return w, h

    def _frame(self, snapshot, x, y, w, h, color, width=1.0):
        snapshot.append_color(color, _rect(x, y, w, width))
        snapshot.append_color(color, _rect(x, y + h - width, w, width))
        snapshot.append_color(color, _rect(x, y, width, h))
        snapshot.append_color(color, _rect(x + w - width, y, width, h))

    def do_snapshot(self, snapshot):
        W, H = self.get_width(), self.get_height()
        c = self.c
        frozen = self.texture is not None
        if frozen:
            snapshot.append_texture(self.texture, _rect(0, 0, W, H))
        dim = system.rgba(0, 0, 0, 0.42 if frozen else 0.28)

        hl, kind = c.highlight_rect()
        local = None
        if hl:
            g = geometry.intersect(hl, self.monitor.rect)
            if g:
                local = self._local(g)
        if local:
            x, y, w, h = local
            snapshot.append_color(dim, _rect(0, 0, W, y))
            snapshot.append_color(dim, _rect(0, y + h, W, H - y - h))
            snapshot.append_color(dim, _rect(0, y, x, h))
            snapshot.append_color(dim, _rect(x + w, y, W - x - w, h))
            if kind == "window":
                snapshot.append_color(system.rgba(0.24, 0.56, 1.0, 0.16), _rect(x, y, w, h))
                self._frame(snapshot, x, y, w, h, ACCENT, 2)
            else:
                self._frame(snapshot, x - 1, y - 1, w + 2, h + 2, system.rgba(0, 0, 0, 0.5))
                self._frame(snapshot, x, y, w, h, WHITE)
            if kind == "area" and c.selection:
                sx, sy, sw, sh = c.selection
                label = f"{int(sw)} × {int(sh)}"
                lw, lh = self._layout(label)[1:]
                lx = min(max(4, x + w - lw - 16), W - lw - 20)
                ly = y + h + 8 if y + h + lh + 20 < H else y + h - lh - 16
                self._pill(snapshot, label, lx, ly)
        else:
            snapshot.append_color(dim, _rect(0, 0, W, H))

        cursor_here = c.cursor is not None and geometry.contains(self.monitor.rect, *c.cursor)
        if cursor_here:
            cx, cy = c.cursor[0] - self.monitor.x, c.cursor[1] - self.monitor.y
            if c.show_crosshair and not c.dragging and c.mode == "area":
                line = system.rgba(1, 1, 1, 0.55)
                snapshot.append_color(line, _rect(0, cy, W, 1))
                snapshot.append_color(line, _rect(cx, 0, 1, H))
            if c.hint:
                lw = self._layout(c.hint, "Sans 11")[1]
                self._pill(snapshot, c.hint, (W - lw) / 2 - 12, 28, "Sans 11", (12, 7))
            if c.mode == "window" and not c.dragging:
                w = c.hovered_window()
                if w and w.get("app"):
                    self._pill(snapshot, w["app"], cx + 16, cy + 16)
            if c.show_magnifier and self.texture is not None:
                self._loupe(snapshot, cx, cy, W, H)

    def _loupe(self, snapshot, cx, cy, W, H):
        L, Z = LOUPE_SIZE, LOUPE_ZOOM
        lx = cx + 28 if cx + 28 + L < W else cx - 28 - L
        ly = cy + 28 if cy + 28 + L + 30 < H else cy - 28 - L - 30
        tx = int(cx * self.tex_ratio)
        ty = int(cy * self.tex_ratio)
        tw, th = self.texture.get_width(), self.texture.get_height()

        snapshot.push_rounded_clip(_rounded(lx, ly, L, L, L / 2))
        snapshot.append_color(BLACK, _rect(lx, ly, L, L))
        dest = _rect(lx + L / 2 - (tx + 0.5) * Z, ly + L / 2 - (ty + 0.5) * Z, tw * Z, th * Z)
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.NEAREST, dest)
        mid = L / 2 - Z / 2
        self._frame(snapshot, lx + mid - 1, ly + mid - 1, Z + 2, Z + 2, BLACK)
        self._frame(snapshot, lx + mid, ly + mid, Z, Z, WHITE)
        snapshot.pop()
        snapshot.append_border(_rounded(lx, ly, L, L, L / 2), [2, 2, 2, 2], [WHITE] * 4)

        gx, gy = self.c.cursor
        text = f"{int(gx)}, {int(gy)}"
        color = self.c.pixel_hex(gx, gy)
        if color:
            text += f"   {color}"
        lw = self._layout(text)[1]
        self._pill(snapshot, text, lx + (L - lw) / 2 - 8, ly + L + 6)
