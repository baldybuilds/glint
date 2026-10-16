"""Quick Access Overlay: floating capture thumbnails stacked in a screen corner.

Hovering a card reveals Copy / Save in the middle and Close, Pin and Annotate
in the corners; dragging the thumbnail drops the file into other apps, and
double-clicking opens the editor. Closed cards can be restored.
"""

import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import capture, debug, geometry, kwin, system  # noqa: E402
from .widgets import ImageView  # noqa: E402

THUMB_WIDTH = 240


class CaptureItem:
    def __init__(self, path, kind="image", rect=None, saved=False, video=None):
        self.path = path            # image path (for videos: a poster frame)
        self.kind = kind            # image | video
        self.rect = rect            # logical rect it was captured from
        self.saved = saved
        self.video = video          # video/gif file for recordings

    @property
    def file(self):
        return self.video or self.path


class QuickAccessManager:
    def __init__(self, app):
        self.app = app
        self.cards = []
        self.closed = []

    def add(self, item):
        card = QuickAccessCard(self, item)
        self.cards.append(card)
        self.layout()
        card.present()
        debug("quick access card", card.get_title(), card.card_size)
        return card

    def remove(self, card, remember=True):
        if card in self.cards:
            self.cards.remove(card)
            if remember and os.path.exists(card.item.file):
                self.closed.append(card.item)
                self.closed = self.closed[-20:]
        card.destroy()
        self.layout()

    def close_all(self):
        for card in list(self.cards):
            self.remove(card)

    def restore_last(self):
        while self.closed:
            item = self.closed.pop()
            if os.path.exists(item.file):
                self.add(item)
                return True
        return False

    def set_hidden(self, hidden):
        for card in self.cards:
            card.set_visible(not hidden)

    @property
    def visible(self):
        return any(c.get_visible() for c in self.cards)

    def _area(self):
        """Work area (panels excluded) of the monitor the newest capture came from."""
        monitors = capture.current_monitors()
        if not monitors:
            return (0, 0, 1920, 1080)
        monitor = monitors[0]
        if self.cards and self.cards[-1].item.rect:
            x, y, w, h = self.cards[-1].item.rect
            monitor = capture.monitor_at(monitors, x + w / 2, y + h / 2) or monitor
        return self.app.kwin.work_area(monitor) if self.app.kwin else monitor.rect

    def layout(self):
        if not self.cards:
            return
        corner = self.app.config.get("quick_access.position")
        sizes = [c.card_size for c in self.cards]
        positions = geometry.stack_positions(self._area(), sizes, corner)
        for card, (x, y) in zip(self.cards, positions):
            card.move_to(x, y)


class QuickAccessCard(Gtk.Window):
    def __init__(self, manager, item):
        super().__init__()
        self.manager = manager
        self.app = manager.app
        self.item = item
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("glint-qa")
        self._timer = 0
        self._hovered = False

        texture = Gdk.Texture.new_from_filename(item.path)
        tw, th = texture.get_width(), texture.get_height()
        h = int(min(max(THUMB_WIDTH * th / max(tw, 1), 96), 200))
        self.card_size = (THUMB_WIDTH, h)
        self.set_title(kwin.place_title("qa"))

        self.picture = ImageView(texture, *self.card_size, radius=12, border=(1, 1, 1, 0.25), cover=True)

        overlay = Gtk.Overlay()
        overlay.set_child(self.picture)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                     transition_duration=120, can_target=True)
        overlay.add_overlay(self.revealer)
        self.revealer.set_child(self._build_controls())

        if item.kind == "video":
            badge = Gtk.Label(label="GIF" if item.file.lower().endswith(".gif") else "VIDEO")
            badge.add_css_class("glint-qa-badge")
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.END)
            badge.set_margin_end(8)
            badge.set_margin_bottom(8)
            badge.set_can_target(False)
            overlay.add_overlay(badge)

        self.set_child(overlay)

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._on_enter)
        hover.connect("leave", self._on_leave)
        self.add_controller(hover)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.picture.add_controller(click)

        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare)
        drag.connect("drag-begin", lambda src, _d: src.set_icon(self.picture.texture, 20, 20))
        drag.connect("drag-end", self._on_drag_end)
        self.picture.add_controller(drag)

        self._schedule_autoclose()

    # -- UI ------------------------------------------------------------------

    def _btn(self, label=None, icon=None, tooltip=None, cb=None, css=()):
        btn = Gtk.Button(label=label) if label else Gtk.Button(icon_name=icon)
        if tooltip:
            btn.set_tooltip_text(tooltip)
        for c in css:
            btn.add_css_class(c)
        btn.connect("clicked", lambda *_: cb())
        return btn

    def _build_controls(self):
        grid = Gtk.Overlay()
        shade = Gtk.Box()
        shade.add_css_class("glint-qa-shade")
        shade.set_size_request(*self.card_size)
        grid.set_child(shade)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        center.append(self._btn("Copy", cb=self.copy, css=("glint-qa-pill",)))
        if self.item.kind == "video":
            center.append(self._btn("Open", cb=self.open, css=("glint-qa-pill",)))
        else:
            self.save_btn = self._btn("Show in Folder" if self.item.saved else "Save", cb=self.save,
                                      css=("glint-qa-pill",))
            center.append(self.save_btn)
        grid.add_overlay(center)

        def corner(widget, h, v):
            widget.set_halign(h)
            widget.set_valign(v)
            widget.set_margin_start(6)
            widget.set_margin_end(6)
            widget.set_margin_top(6)
            widget.set_margin_bottom(6)
            grid.add_overlay(widget)

        corner(self._btn(icon="window-close-symbolic", tooltip="Close", cb=self.close_card,
                         css=("circular", "glint-qa-icon")), Gtk.Align.START, Gtk.Align.START)
        if self.item.kind == "image":
            corner(self._btn(icon="glint-pin-symbolic", tooltip="Pin to screen", cb=self.pin,
                             css=("circular", "glint-qa-icon")), Gtk.Align.END, Gtk.Align.START)
            corner(self._btn(icon="glint-tool-arrow-symbolic", tooltip="Annotate", cb=self.annotate,
                             css=("circular", "glint-qa-icon")), Gtk.Align.START, Gtk.Align.END)
            corner(self._btn(icon="edit-find-symbolic", tooltip="Copy text (OCR)", cb=self.ocr,
                             css=("circular", "glint-qa-icon")), Gtk.Align.END, Gtk.Align.END)
        else:
            corner(self._btn(icon="folder-open-symbolic", tooltip="Show in folder", cb=self.reveal,
                             css=("circular", "glint-qa-icon")), Gtk.Align.END, Gtk.Align.START)
            corner(self._btn(icon="edit-cut-symbolic", tooltip="Trim", cb=self.trim,
                             css=("circular", "glint-qa-icon")), Gtk.Align.START, Gtk.Align.END)
        return grid

    def move_to(self, x, y):
        self.set_title(kwin.place_title("qa", x, y))

    def refresh(self):
        texture = Gdk.Texture.new_from_filename(self.item.path)
        self.picture.set_texture(texture)

    # -- hover / autoclose ---------------------------------------------------

    def _on_enter(self, *_a):
        self._hovered = True
        self.revealer.set_reveal_child(True)

    def _on_leave(self, *_a):
        self._hovered = False
        self.revealer.set_reveal_child(False)
        self._schedule_autoclose()

    def _schedule_autoclose(self):
        cfg = self.app.config
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if not cfg.get("quick_access.auto_close"):
            return

        def fire():
            self._timer = 0
            if not self._hovered:
                self.close_card()
            return False

        self._timer = GLib.timeout_add_seconds(max(2, int(cfg.get("quick_access.auto_close_seconds"))), fire)

    # -- actions -------------------------------------------------------------

    def _on_click(self, gesture, n_press, _x, _y):
        if n_press == 2:
            if self.item.kind == "image":
                self.annotate()
            else:
                self.trim()

    def _on_drag_prepare(self, _src, _x, _y):
        f = Gio.File.new_for_path(self.item.file)
        uri_list = Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new((f.get_uri() + "\r\n").encode()))
        providers = [uri_list]
        try:
            providers.append(Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([f])))
        except (TypeError, AttributeError):
            pass
        if self.item.kind == "image":
            providers.append(Gdk.ContentProvider.new_for_value(self.picture.texture))
        return Gdk.ContentProvider.new_union(providers)

    def _on_drag_end(self, _src, _drag, _delete):
        if self.app.config.get("quick_access.close_after_drag"):
            self.close_card()

    def close_card(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.manager.remove(self, remember=True)

    def copy(self):
        if self.item.kind == "image":
            system.copy_image(self.item.path)
        else:
            system.copy_file(self.item.file)
        system.notify(self.app, "Copied to the clipboard", "Paste it anywhere with Ctrl+V.", "toast")
        if self.app.config.get("quick_access.close_after_copy"):
            self.close_card()

    def save(self):
        if self.item.saved:
            system.reveal_in_folder(self.item.path)
            return
        dest = self.app.save_capture_file(self.item.path, kind="Screenshot")
        self.app.history.relink(self.item.path, dest)
        self.item.path = dest
        self.item.saved = True
        self.save_btn.set_label("Show in Folder")
        system.notify(self.app, "Screenshot saved",
                      f"Saved as {os.path.basename(dest)} in {system.display_dir(dest)}.", "toast")
        if self.app.config.get("quick_access.close_after_save"):
            self.close_card()

    def reveal(self):
        system.reveal_in_folder(self.item.file)

    def open(self):
        system.open_path(self.item.file)

    def trim(self):
        from .recorder import poster_frame

        def saved(path):
            self.app.history_media_changed(self.item.file, path)
            self.item.video = path

            def got_poster(poster):
                if poster:
                    self.item.path = poster
                    if self in self.manager.cards:
                        self.refresh()

            poster_frame(path, got_poster)

        self.app.open_trimmer(self.item.file, on_saved=saved)

    def pin(self):
        self.app.pin_file(self.item.path, self.item.rect)
        self.close_card()

    def ocr(self):
        self.app.ocr_file(self.item.path)

    def annotate(self):
        def saved(path):
            self.app.history_media_changed(self.item.path, path)
            self.item.path = path
            if self.get_visible() and self in self.manager.cards:
                self.refresh()
        self.app.open_annotate(self.item.path, on_saved=saved)
