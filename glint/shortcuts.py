"""Global keyboard shortcuts via org.freedesktop.portal.GlobalShortcuts.

The desktop owns the final key assignment: on Plasma the first bind shows a
confirmation dialog, and users can change triggers later in
System Settings → Shortcuts (or via "Configure…" in Glint's preferences).
"""

from gi.repository import Gio, GLib

from . import portal

IFACE = "org.freedesktop.portal.GlobalShortcuts"

# id, description, preferred trigger (XDG shortcuts spec syntax)
SHORTCUTS = [
    ("capture-area", "Capture area", "LOGO+SHIFT+4"),
    ("capture-fullscreen", "Capture fullscreen", "LOGO+SHIFT+3"),
    ("capture-window", "Capture window", "LOGO+SHIFT+5"),
    ("capture-previous-area", "Capture previous area", "LOGO+SHIFT+6"),
    ("scrolling-capture", "Scrolling capture", "LOGO+SHIFT+7"),
    ("capture-text", "Capture text (OCR)", "LOGO+SHIFT+2"),
    ("record-screen", "Record screen / stop recording", "LOGO+SHIFT+8"),
    ("open-annotate", "Open annotate tool", ""),
    ("restore-recently-closed", "Restore recently closed capture", ""),
]


class GlobalShortcuts:
    def __init__(self, on_activated):
        self.on_activated = on_activated
        self.session = None
        self.bound = {}
        self._sub = 0
        self.available = portal.get_version(IFACE) > 0

    def start(self, callback=None):
        """Create a session and bind our shortcuts. callback(ok, message)."""
        if not self.available:
            if callback:
                callback(False, "This desktop does not provide the GlobalShortcuts portal")
            return

        def on_create(code, results):
            if code != 0:
                if callback:
                    callback(False, "Could not create a global-shortcuts session")
                return
            self.session = results.get("session_handle")
            self._sub = portal.connection().signal_subscribe(
                None, IFACE, "Activated", portal.OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, self._on_activated)
            self._bind(callback)

        portal.request(IFACE, "CreateSession", "", (),
                       {"session_handle_token": GLib.Variant("s", portal.new_token("glintkeys"))}, on_create)

    def _bind(self, callback):
        shortcuts = []
        for sid, desc, trigger in SHORTCUTS:
            props = {"description": GLib.Variant("s", desc)}
            if trigger:
                props["preferred_trigger"] = GLib.Variant("s", trigger)
            shortcuts.append((sid, props))

        def on_bound(code, results):
            if code != 0:
                if callback:
                    callback(False, "Shortcut binding was cancelled" if code == 1 else "Could not bind shortcuts")
                return
            for sid, props in results.get("shortcuts", []):
                self.bound[sid] = props.get("trigger_description", "")
            if callback:
                callback(True, None)

        portal.request(IFACE, "BindShortcuts", "oa(sa{sv})s", (self.session, shortcuts, ""), {}, on_bound)

    def _on_activated(self, _conn, _sender, _path, _iface, _signal, params):
        session, shortcut_id, _ts, _opts = params.unpack()
        if session == self.session:
            GLib.idle_add(lambda: self.on_activated(shortcut_id) and False)

    def configure(self):
        """Open the desktop's shortcut editor (portal v2), else Plasma's KCM."""
        if self.session and portal.get_version(IFACE) >= 2:
            try:
                portal.connection().call_sync(portal.BUS_NAME, portal.OBJECT_PATH, IFACE, "ConfigureShortcuts",
                                              GLib.Variant("(osa{sv})", (self.session, "", {})), None,
                                              Gio.DBusCallFlags.NONE, 3000, None)
                return True
            except GLib.Error:
                pass
        try:
            Gio.Subprocess.new(["systemsettings", "kcm_keys"], Gio.SubprocessFlags.NONE)
            return True
        except GLib.Error:
            return False

    def describe(self, shortcut_id):
        return self.bound.get(shortcut_id, "")
