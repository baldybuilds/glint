"""Pinned screenshots: borderless always-on-top image windows.

Drag to move, scroll to zoom, Ctrl+scroll to change opacity, double-click to
annotate, right-click for options, Esc to close.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import kwin, system  # noqa: E402
from .widgets import ImageView  # noqa: E402

MIN_SIZE = 48
BORDER = (0.24, 0.56, 1.0, 0.85)


class PinWindow(Gtk.Window):
    def __init__(self, app, path, position=None):
        super().__init__(application=app)
        self.app = app
        self.path = path
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("glint-pin")
        self.bordered = True
        self.rounded = True
        self.zoom = 1.0
        self.opacity = 1.0
        self._moving = False

        texture = Gdk.Texture.new_from_filename(path)
        self.base_size = (texture.get_width(), texture.get_height())
        self._fit_to_screen()
        self.picture = ImageView(texture, *self.base_size, radius=10, border=BORDER)
        self.set_child(self.picture)
        self._apply_size()

        x, y = position if position else (None, None)
        self.set_title(kwin.place_title("pin", x, y))

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: setattr(self, "_moving", False))
        self.add_controller(drag)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._build_menu()

    def _fit_to_screen(self):
        mons = Gdk.Display.get_default().get_monitors()
        if not mons.get_n_items():
            return
        g = mons.get_item(0).get_geometry()
        bw, bh = self.base_size
        fit = min(1.0, g.width * 0.8 / max(bw, 1), g.height * 0.8 / max(bh, 1))
        self.zoom = fit

    def _apply_size(self):
        w = max(MIN_SIZE, int(self.base_size[0] * self.zoom))
        h = max(MIN_SIZE, int(self.base_size[1] * self.zoom))
        self.picture.set_display_size(w, h)

    def _build_menu(self):
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Copy", "pin.copy")
        section.append("Save As…", "pin.save")
        section.append("Annotate", "pin.annotate")
        menu.append_section(None, section)
        view = Gio.Menu()
        view.append("Actual Size", "pin.actual")
        view.append("Toggle Border", "pin.border")
        view.append("Toggle Rounded Corners", "pin.rounded")
        opacity = Gio.Menu()
        for pct in (100, 75, 50, 25):
            opacity.append(f"{pct}%", f"pin.opacity({pct})")
        view.append_submenu("Opacity", opacity)
        menu.append_section(None, view)
        close = Gio.Menu()
        close.append("Close", "pin.close")
        menu.append_section(None, close)

        group = Gio.SimpleActionGroup()

        def add(name, cb, param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", cb)
            group.add_action(a)

        add("copy", lambda *_: system.copy_image(self.path))
        add("save", lambda *_: self._save_as())
        add("annotate", lambda *_: self._annotate())
        add("actual", lambda *_: self._set_zoom(1.0))
        add("border", lambda *_: self._toggle("bordered"))
        add("rounded", lambda *_: self._toggle("rounded"))
        add("opacity", lambda _a, p: self._set_opacity(p.get_int32() / 100), GLib.VariantType.new("i"))
        add("close", lambda *_: self.destroy())
        self.insert_action_group("pin", group)

        self.popover = Gtk.PopoverMenu.new_from_model(menu)
        self.popover.set_parent(self.picture)
        self.popover.set_has_arrow(False)

    def _toggle(self, name):
        setattr(self, name, not getattr(self, name))
        self.picture.radius = 10 if self.rounded else 0
        self.picture.border = BORDER if self.bordered else None
        self.picture.queue_draw()

    def _set_zoom(self, zoom):
        self.zoom = min(max(zoom, 0.1), 8.0)
        self._apply_size()

    def _set_opacity(self, value):
        self.opacity = min(max(value, 0.1), 1.0)
        self.set_opacity(self.opacity)

    def _on_drag_update(self, gesture, dx, dy):
        if self._moving or abs(dx) + abs(dy) < 4:
            return
        self._moving = True
        ok, sx, sy = gesture.get_start_point()
        surface = self.get_surface()
        if surface is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        surface.begin_move(gesture.get_device(), 1, sx, sy, gesture.get_current_event_time())

    def _on_click(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        if button == 3:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            self.popover.set_pointing_to(rect)
            self.popover.popup()
        elif button == 1 and n_press == 2:
            self._annotate()

    def _on_scroll(self, ctrl, _dx, dy):
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self._set_opacity(self.opacity - dy * 0.05)
        else:
            self._set_zoom(self.zoom * (1.1 ** (-dy)))
        return True

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            self.destroy()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and name in ("c", "C"):
            system.copy_image(self.path)
            return True
        if name in ("plus", "equal"):
            self._set_zoom(self.zoom * 1.1)
            return True
        if name == "minus":
            self._set_zoom(self.zoom / 1.1)
            return True
        if name == "0":
            self._set_zoom(1.0)
            return True
        return False

    def _annotate(self):
        def saved(path):
            self.path = path
            texture = Gdk.Texture.new_from_filename(path)
            self.base_size = (texture.get_width(), texture.get_height())
            self.picture.set_texture(texture)
            self._apply_size()
        self.app.open_annotate(self.path, on_saved=saved)

    def _save_as(self):
        dialog = Gtk.FileDialog(title="Save Pinned Image", initial_name="Pinned.png")
        dialog.set_initial_folder(Gio.File.new_for_path(self.app.config.save_dir()))

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            if f:
                fmt = "jpg" if f.get_path().lower().endswith((".jpg", ".jpeg")) else "png"
                system.convert_image(self.path, f.get_path(), fmt)

        dialog.save(self, None, done)
