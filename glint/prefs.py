"""Preferences dialog (General, Capture, Quick Access, Annotate, Recording, Shortcuts)."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import APP_ID, naming, shortcuts, system  # noqa: E402
from .annotate import background  # noqa: E402

AUTOSTART = os.path.join(GLib.get_user_config_dir(), "autostart", f"{APP_ID}.desktop")


def set_autostart(enabled):
    if not enabled:
        try:
            os.remove(AUTOSTART)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=Glint\nExec=glint --background\n"
                 f"Icon={APP_ID}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = app.config
        self.set_title("Glint Preferences")
        self.set_search_enabled(True)
        for page in (self._general(), self._capture(), self._quick_access(), self._annotate(),
                     self._recording(), self._shortcuts()):
            self.add(page)

    # -- row helpers -----------------------------------------------------------

    def _switch(self, title, key, subtitle=None, on_change=None):
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(bool(self.cfg.get(key)))

        def changed(r, _p):
            self.cfg.set(key, r.get_active())
            if on_change:
                on_change(r.get_active())

        row.connect("notify::active", changed)
        return row

    def _combo(self, title, key, options, subtitle=None):
        """options: list of (value, label)."""
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_model(Gtk.StringList.new([label for _v, label in options]))
        values = [v for v, _l in options]
        current = self.cfg.get(key)
        if current in values:
            row.set_selected(values.index(current))
        row.connect("notify::selected", lambda r, _p: self.cfg.set(key, values[r.get_selected()]))
        return row

    def _spin(self, title, key, lo, hi, step=1, subtitle=None):
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_value(self.cfg.get(key))
        row.connect("notify::value", lambda r, _p: self.cfg.set(key, int(r.get_value())))
        return row

    def _folder(self, title, key, getter):
        row = Adw.ActionRow(title=title, subtitle=getter())
        btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")

        def pick(*_a):
            dialog = Gtk.FileDialog(title=title)
            dialog.set_initial_folder(Gio.File.new_for_path(getter()))

            def done(dlg, res):
                try:
                    f = dlg.select_folder_finish(res)
                except GLib.Error:
                    return
                if f:
                    self.cfg.set(key, f.get_path())
                    row.set_subtitle(f.get_path())

            dialog.select_folder(self.get_root(), None, done)

        btn.connect("clicked", pick)
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        return row

    def _group(self, page, title, rows, description=None):
        group = Adw.PreferencesGroup(title=title)
        if description:
            group.set_description(description)
        for r in rows:
            group.add(r)
        page.add(group)
        return group

    # -- pages -----------------------------------------------------------------

    def _general(self):
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self._group(page, "Startup", [
            self._switch("Launch at login", "general.launch_at_login", on_change=set_autostart),
            self._switch("Play sounds", "general.play_sounds"),
        ])
        self._group(page, "Saving", [
            self._folder("Screenshots folder", "general.save_dir", self.cfg.save_dir),
            self._folder("Recordings folder", "general.video_dir", self.cfg.video_dir),
            self._combo("Image format", "general.format", [("png", "PNG"), ("jpg", "JPEG")]),
            self._spin("JPEG quality", "general.jpg_quality", 10, 100, 5),
            self._template_row(),
        ])
        self._group(page, "After capture", [
            self._switch("Show Quick Access Overlay", "after_capture.show_overlay"),
            self._switch("Save to folder", "after_capture.save"),
            self._switch("Copy to clipboard", "after_capture.copy"),
            self._switch("Open annotate tool", "after_capture.annotate"),
        ])
        return page

    def _template_row(self):
        row = Adw.EntryRow(title="File name template")
        row.set_text(self.cfg.get("general.filename_template"))
        row.set_show_apply_button(True)
        preview = Gtk.Label(css_classes=["dim-label", "caption"])

        def update(*_a):
            preview.set_label(naming.render(row.get_text(), kind="Screenshot", size=(1280, 720)) + ".png")

        row.connect("changed", update)
        row.connect("apply", lambda r: self.cfg.set("general.filename_template", r.get_text()))
        row.add_suffix(preview)
        update()
        row.set_tooltip_text("Tokens: " + " ".join("{" + t + "}" for t in naming.TOKENS))
        return row

    def _capture(self):
        page = Adw.PreferencesPage(title="Capture", icon_name="camera-photo-symbolic")
        self._group(page, "Selection", [
            self._switch("Freeze screen while selecting", "selection.freeze_screen",
                         "Shows a still image and enables the magnifier"),
            self._switch("Show crosshair", "selection.show_crosshair"),
            self._switch("Show magnifier", "selection.show_magnifier"),
            self._switch("Capture mouse pointer", "general.capture_cursor"),
        ])
        self._group(page, "Window captures", [
            self._switch("Add background", "window_capture.background",
                         "Frame window screenshots on a backdrop"),
            self._combo("Background", "window_capture.background_preset",
                        [(k, background.PRESET_LABELS[k]) for k in background.PRESETS]),
            self._spin("Padding", "window_capture.padding", 0, 240, 4),
            self._spin("Corner radius", "window_capture.radius", 0, 48, 1),
            self._switch("Drop shadow", "window_capture.shadow"),
        ])
        self._group(page, "Advanced", [
            self._combo("Capture backend", "general.backend",
                        [("auto", "Automatic"), ("spectacle", "Spectacle (KDE)"),
                         ("portal", "XDG Desktop Portal"), ("grim", "grim (wlroots)")]),
            self._spin("Self-timer (seconds)", "state.self_timer_seconds", 1, 60),
        ])
        return page

    def _quick_access(self):
        page = Adw.PreferencesPage(title="Quick Access", icon_name="view-grid-symbolic")
        self._group(page, "Overlay", [
            self._combo("Position", "quick_access.position",
                        [("bottom-left", "Bottom left"), ("bottom-right", "Bottom right"),
                         ("top-left", "Top left"), ("top-right", "Top right")]),
            self._switch("Close automatically", "quick_access.auto_close"),
            self._spin("Close after (seconds)", "quick_access.auto_close_seconds", 2, 120),
        ])
        self._group(page, "Behaviour", [
            self._switch("Close after dragging", "quick_access.close_after_drag"),
            self._switch("Close after copying", "quick_access.close_after_copy"),
            self._switch("Close after saving", "quick_access.close_after_save"),
        ])
        return page

    def _annotate(self):
        page = Adw.PreferencesPage(title="Annotate", icon_name="document-edit-symbolic")
        self._group(page, "Drawing", [
            self._switch("Draw shadows", "annotate.draw_shadow"),
            self._switch("Inverse arrow direction", "annotate.inverse_arrow",
                         "Drag from the arrow head towards its tail"),
        ])
        return page

    def _recording(self):
        page = Adw.PreferencesPage(title="Recording", icon_name="media-record-symbolic")
        self._group(page, "Video", [
            self._combo("Frame rate", "recording.fps", [(24, "24 fps"), (30, "30 fps"), (60, "60 fps")]),
            self._switch("Show mouse pointer", "recording.show_cursor"),
            self._switch("Record microphone", "recording.record_mic"),
            self._switch("Record computer audio", "recording.record_system_audio"),
        ])
        self._group(page, "GIF", [
            self._spin("GIF frame rate", "recording.gif_fps", 5, 30),
            self._spin("Maximum GIF width", "recording.gif_max_width", 320, 3840, 40),
        ])

        from .clicks import ClickHighlighter
        supported = ClickHighlighter(self.cfg).available()
        highlight = self._switch(
            "Highlight clicks", "recording.highlight_clicks",
            "Draws rings where you click, using KWin's Mouse Click Animation effect" if supported
            else "Unavailable: needs KDE Plasma's Mouse Click Animation effect")
        highlight.set_sensitive(supported)
        color_row = Adw.ActionRow(title="Click colour")
        color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        color_btn.set_rgba(system.rgba(*self.cfg.get("recording.click_color")))
        color_btn.connect("notify::rgba", lambda b, _p: self.cfg.set(
            "recording.click_color", [b.get_rgba().red, b.get_rgba().green, b.get_rgba().blue, 1.0]))
        color_row.add_suffix(color_btn)
        size = self._spin("Ring size", "recording.click_size", 10, 80, 2)
        width = Adw.SpinRow.new_with_range(1, 8, 0.5)
        width.set_title("Ring thickness")
        width.set_value(self.cfg.get("recording.click_line_width"))
        width.connect("notify::value", lambda r, _p: self.cfg.set("recording.click_line_width", r.get_value()))
        for row in (color_row, size, width):
            highlight.bind_property("active", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        self._group(page, "Clicks", [highlight, color_row, size, width])

        self._group(page, "After recording", [
            self._switch("Open trimmer", "recording.open_trimmer",
                         "Trim the recording or convert it to GIF right away"),
        ])

        reset = Adw.ActionRow(title="Forget shared screens",
                              subtitle="Ask again which screens may be recorded")
        btn = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)

        def do_reset(*_a):
            self.cfg.set("recording.restore_token", "")
            self.cfg.set("scrolling.restore_token", "")
            btn.set_sensitive(False)

        btn.connect("clicked", do_reset)
        reset.add_suffix(btn)
        self._group(page, "Permissions", [reset])
        return page

    def _shortcuts(self):
        page = Adw.PreferencesPage(title="Shortcuts", icon_name="preferences-desktop-keyboard-shortcuts-symbolic")
        from .takeover import get_takeover

        self._takeover = get_takeover(self.cfg)
        self._default_tool_group(page)

        self.shortcut_group = Adw.PreferencesGroup(
            title="Global shortcuts",
            description="These commands also work from a terminal or your own shortcuts, "
                        "e.g. “glint capture-area”.")
        btn = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self._configure_shortcuts())
        self.shortcut_group.set_header_suffix(btn)
        self.shortcut_rows = []
        page.add(self.shortcut_group)
        self._refresh_shortcut_rows()
        return page

    def _shortcut_values(self):
        """[(label, command, keys text)] from KGlobalAccel on KDE, else the portal."""
        from .takeover import GLINT, GLINT_ACTIONS, KGlobalAccel, key_to_string
        if self.app.kde_shortcuts:
            actions = KGlobalAccel().actions(GLINT)
            return [(label, cmd, ", ".join(key_to_string(k) for k in actions.get(aid, {}).get("keys", [])))
                    for aid, cmd, label in GLINT_ACTIONS]
        keys = self.app.shortcuts
        out = []
        for sid, desc, trigger in shortcuts.SHORTCUTS:
            current = keys.describe(sid) if keys else ""
            out.append((desc, sid, current or trigger.replace("LOGO", "Meta")))
        return out

    def _refresh_shortcut_rows(self):
        for row in self.shortcut_rows:
            self.shortcut_group.remove(row)
        self.shortcut_rows = []
        for label, cmd, keys in self._shortcut_values():
            row = Adw.ActionRow(title=label, subtitle=f"glint {cmd}")
            row.add_suffix(Gtk.Label(label=keys or "Unassigned", css_classes=["dim-label"], wrap=True))
            self.shortcut_group.add(row)
            self.shortcut_rows.append(row)

    def _configure_shortcuts(self):
        if self.app.kde_shortcuts:
            system.spawn(["systemsettings", "kcm_keys"])
        elif self.app.shortcuts:
            self.app.shortcuts.configure()

    def _default_tool_group(self, page):
        takeover = self._takeover
        tool = takeover.tool_name if takeover else "your desktop's screenshot tool"
        group = Adw.PreferencesGroup(
            title="Default screenshot tool",
            description=f"Take over Print Screen and the other screenshot shortcuts from {tool}. "
                        f"{tool} stays installed and can be restored at any time.")
        row = Adw.SwitchRow(title="Use Glint for Print Screen")
        self._default_row = row
        if takeover is None or not takeover.available():
            row.set_sensitive(False)
            row.set_subtitle(takeover.reason_unavailable() if takeover else
                             "Not supported on this desktop. Bind “glint capture-area” to Print yourself.")
        else:
            row.set_active(takeover.active())
            self._update_default_subtitle()
            row.connect("notify::active", self._on_default_toggled)
        group.add(row)
        page.add(group)

    def _update_default_subtitle(self):
        takeover = self._takeover
        plan = takeover.plan_from_backup() if takeover.desktop == "kde" and takeover.active() else takeover.plan()
        text = " · ".join(f"{key} → {label}" for key, label in plan[:6])
        if takeover.active():
            self._default_row.set_subtitle(text or "Glint handles the screenshot shortcuts")
        else:
            self._default_row.set_subtitle(f"Currently {takeover.tool_name}: {text}" if text else
                                           f"{takeover.tool_name} has no screenshot shortcuts set")

    def _on_default_toggled(self, row, _pspec):
        want = row.get_active()
        if want == self._takeover.active():
            return
        ok = self.app.set_default_tool() if want else self.app.unset_default_tool()
        if not ok:
            row.handler_block_by_func(self._on_default_toggled)
            row.set_active(self._takeover.active())
            row.handler_unblock_by_func(self._on_default_toggled)
        self._update_default_subtitle()
        self._refresh_shortcut_rows()
