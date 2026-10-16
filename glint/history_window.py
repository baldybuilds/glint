"""Capture History window: recent captures grouped by day."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, system  # noqa: E402
from .history import RETENTION_LABELS, VIDEO_KINDS, day_label  # noqa: E402
from .widgets import ImageView  # noqa: E402

CARD_W, CARD_H = 240, 150
KIND_LABELS = {"screenshot": "Area", "area": "Area", "window": "Window", "fullscreen": "Fullscreen",
               "scrolling": "Scrolling", "video": "Recording", "gif": "GIF"}
FILTERS = [("all", "All"), ("images", "Screenshots"), ("videos", "Recordings")]


def format_duration(seconds):
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.history = app.history
        self.filter = "all"
        self.set_title("Capture History — Glint")
        self.set_default_size(1040, 700)
        self._build()
        self.history.connect(self.refresh)
        self.connect("close-request", lambda *_: self.history.disconnect(self.refresh) or False)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.refresh()

    # -- layout --------------------------------------------------------------

    def _build(self):
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        switcher = Gtk.Box(css_classes=["linked"])
        group = None
        for fid, label in FILTERS:
            btn = Gtk.ToggleButton(label=label, active=fid == "all")
            if group:
                btn.set_group(group)
            else:
                group = btn
            btn.connect("toggled", lambda b, f=fid: b.get_active() and self._set_filter(f))
            switcher.append(btn)
        header.set_title_widget(switcher)

        menu = Gio.Menu()
        menu.append("Open Screenshots Folder", "win.open-shots")
        menu.append("Open Recordings Folder", "win.open-videos")
        menu.append("History Settings…", "win.settings")
        danger = Gio.Menu()
        danger.append("Clear History…", "win.clear")
        menu.append_section(None, danger)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        for name, cb in (("open-shots", lambda: self._open_dir(self.app.config.save_dir())),
                         ("open-videos", lambda: self._open_dir(self.app.config.video_dir())),
                         ("settings", self.app.show_preferences),
                         ("clear", self._confirm_clear)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, f=cb: f())
            self.add_action(action)
        tv.add_top_bar(header)

        self.stack = Gtk.Stack()
        self.empty = Adw.StatusPage(icon_name=APP_ID)
        self.stack.add_named(self.empty, "empty")
        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=24,
                                margin_start=24, margin_end=24)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.sections)
        self.stack.add_named(scroller, "list")
        tv.set_content(self.stack)
        self.toasts = Adw.ToastOverlay(child=tv)
        self.set_content(self.toasts)

    def _set_filter(self, fid):
        self.filter = fid
        self.refresh()

    def _visible_entries(self):
        for e in self.history.entries:
            video = e.get("kind") in VIDEO_KINDS
            if self.filter == "images" and video or self.filter == "videos" and not video:
                continue
            yield e

    def refresh(self):
        child = self.sections.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sections.remove(child)
            child = nxt
        entries = list(self._visible_entries())
        if not entries:
            self._show_empty()
            return
        self.stack.set_visible_child_name("list")
        current, flow = None, None
        for entry in entries:
            label = day_label(entry["created"])
            if label != current:
                current = label
                heading = Gtk.Label(label=label, xalign=0, css_classes=["title-4"], margin_top=12)
                self.sections.append(heading)
                flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                   activate_on_single_click=False, max_children_per_line=12,
                                   column_spacing=14, row_spacing=14, valign=Gtk.Align.START)
                flow.connect("child-activated", lambda _f, c: c.get_child().open())
                self.sections.append(flow)
            flow.append(HistoryCard(self, entry))

    def _show_empty(self):
        retention = dict(RETENTION_LABELS).get(self.history.retention, "1 week")
        if not self.history.enabled:
            self.empty.set_title("Capture history is off")
            self.empty.set_description("Turn it on in Preferences → General → Capture History.")
        elif self.filter != "all" and self.history.entries:
            self.empty.set_title("Nothing here")
            self.empty.set_description("No captures of this kind yet.")
        else:
            self.empty.set_title("No captures yet")
            self.empty.set_description(f"Screenshots and recordings show up here and are kept for "
                                       f"{retention.lower()}. Press Print to capture something.")
        self.stack.set_visible_child_name("empty")

    # -- helpers used by cards ---------------------------------------------------

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=2))

    def _open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        system.open_path(path)

    def _confirm_clear(self):
        dialog = Adw.AlertDialog(
            heading="Clear capture history?",
            body="Captures saved to your folders stay there. Captures that were never saved are deleted.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear History")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "clear" and self.history.clear())
        dialog.present(self)

    def _focused_card(self):
        focus = self.get_focus()
        while focus is not None and not isinstance(focus, Gtk.FlowBoxChild):
            focus = focus.get_parent()
        return focus.get_child() if focus is not None else None

    def _on_key(self, _c, keyval, _code, state):
        name = Gdk.keyval_name(keyval)
        card = self._focused_card()
        if name == "Escape":
            self.close()
        elif card and name in ("Delete", "KP_Delete"):
            card.remove()
        elif card and state & Gdk.ModifierType.CONTROL_MASK and name in ("c", "C"):
            card.copy()
        else:
            return False
        return True


class HistoryCard(Gtk.Box):
    def __init__(self, window, entry):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.window = window
        self.app = window.app
        self.history = window.history
        self.entry = entry
        self.video = entry.get("kind") in VIDEO_KINDS
        self.owned = self.history.owns(entry["path"])

        thumb = self.history.fresh_thumb(entry)
        texture = Gdk.Texture.new_from_filename(thumb) if thumb else None
        self.image = ImageView(texture, CARD_W, CARD_H, radius=10, border=(0.5, 0.5, 0.5, 0.35), cover=True)
        overlay = Gtk.Overlay(child=self.image)

        if self.video:
            text = "GIF" if entry["path"].lower().endswith(".gif") else format_duration(entry.get("duration"))
            badge = Gtk.Label(label=text, css_classes=["glint-qa-badge"], halign=Gtk.Align.END,
                              valign=Gtk.Align.END, margin_end=8, margin_bottom=8, can_target=False)
            overlay.add_overlay(badge)

        buttons = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=6, margin_end=6)
        for icon, tip, cb in (("edit-copy-symbolic", "Copy", self.copy),
                              ("edit-cut-symbolic" if self.video else "glint-tool-arrow-symbolic",
                               "Trim" if self.video else "Annotate", self.open),
                              ("user-trash-symbolic", "Delete" if self.owned else "Remove from history", self.remove)):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["circular", "glint-qa-icon"])
            btn.connect("clicked", lambda *_a, f=cb: f())
            buttons.append(btn)
        self.revealer = Gtk.Revealer(child=buttons, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                     transition_duration=120, halign=Gtk.Align.END, valign=Gtk.Align.START)
        overlay.add_overlay(self.revealer)
        self.append(overlay)

        when = GLib.DateTime.new_from_unix_local(int(entry["created"])).format("%H:%M")
        kind = KIND_LABELS.get(entry.get("kind"), "Capture")
        detail = (f"{entry['width']} × {entry['height']}" if entry.get("width") and entry.get("height")
                  else os.path.basename(entry["path"]))
        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caption.append(Gtk.Label(label=f"{when} · {kind}", xalign=0, css_classes=["heading"]))
        caption.append(Gtk.Label(label=detail, xalign=0, css_classes=["dim-label", "caption"],
                                 ellipsize=Pango.EllipsizeMode.END, max_width_chars=28))
        self.append(caption)
        self.set_tooltip_text(entry["path"])

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self.revealer.set_reveal_child(True))
        hover.connect("leave", lambda *_: self.revealer.set_reveal_child(False))
        self.add_controller(hover)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_click)
        self.image.add_controller(click)

        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect("prepare", self._drag_prepare)
        drag.connect("drag-begin", lambda src, _d: texture and src.set_icon(texture, 20, 20))
        self.image.add_controller(drag)

        self._build_menu()

    # -- menu --------------------------------------------------------------------

    def _build_menu(self):
        menu = Gio.Menu()
        main = Gio.Menu()
        main.append("Trim / Convert…" if self.video else "Annotate…", "card.open")
        main.append("Copy", "card.copy")
        if not self.video:
            main.append("Pin to Screen", "card.pin")
        main.append("Open in Default App", "card.launch")
        main.append("Show in Folder", "card.reveal")
        menu.append_section(None, main)
        danger = Gio.Menu()
        if self.owned:
            danger.append("Delete", "card.remove")
        else:
            danger.append("Remove from History", "card.remove")
            danger.append("Move File to Trash", "card.trash")
        menu.append_section(None, danger)

        group = Gio.SimpleActionGroup()
        for name, cb in (("open", self.open), ("copy", self.copy), ("pin", self.pin), ("launch", self.launch),
                         ("reveal", self.reveal), ("remove", self.remove), ("trash", self.trash)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, f=cb: f())
            group.add_action(action)
        self.insert_action_group("card", group)
        self.popover = Gtk.PopoverMenu.new_from_model(menu)
        self.popover.set_parent(self.image)
        self.popover.set_has_arrow(False)

    def _on_click(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        if button == 3:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            self.popover.set_pointing_to(rect)
            self.popover.popup()
        elif button == 1 and n_press == 2:
            self.open()

    def _drag_prepare(self, _src, _x, _y):
        f = Gio.File.new_for_path(self.entry["path"])
        providers = [Gdk.ContentProvider.new_for_bytes("text/uri-list",
                                                       GLib.Bytes.new((f.get_uri() + "\r\n").encode()))]
        try:
            providers.append(Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([f])))
        except (TypeError, AttributeError):
            pass
        return Gdk.ContentProvider.new_union(providers)

    # -- actions -----------------------------------------------------------------

    def open(self):
        path = self.entry["path"]
        if self.video:
            self.app.open_trimmer(path, on_saved=lambda p: self.app.history_media_changed(path, p))
        else:
            self.app.open_annotate(path, on_saved=lambda p: self.app.history_media_changed(path, p))

    def copy(self):
        if self.video:
            system.copy_file(self.entry["path"])
        else:
            system.copy_image(self.entry["path"])
        self.window.toast("Copied to clipboard")

    def pin(self):
        if not self.video:
            self.app.pin_file(self.entry["path"])

    def launch(self):
        system.open_path(self.entry["path"])

    def reveal(self):
        system.reveal_in_folder(self.entry["path"])

    def remove(self):
        self.history.remove(self.entry["id"])
        self.window.toast("Deleted" if self.owned else "Removed from history (the file is still in your folder)")

    def trash(self):
        self.history.remove(self.entry["id"], delete_file=True)
        self.window.toast("Moved to Trash")
