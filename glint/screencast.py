"""org.freedesktop.portal.ScreenCast session → PipeWire stream.

The first run shows the desktop's source picker; afterwards a persisted
restore token lets the session start silently.
"""

from gi.repository import Gio, GLib

from . import geometry, portal

IFACE = "org.freedesktop.portal.ScreenCast"
SOURCE_MONITOR = 1
CURSOR_HIDDEN, CURSOR_EMBEDDED, CURSOR_METADATA = 1, 2, 4
PERSIST_PERMANENT = 2


class Stream:
    def __init__(self, node_id, props):
        self.node_id = node_id
        pos = props.get("position")
        size = props.get("size")
        self.position = tuple(pos) if pos else None
        self.size = tuple(size) if size else None

    @property
    def rect(self):
        if self.position and self.size:
            return (*self.position, *self.size)
        return None

    def __repr__(self):
        return f"Stream({self.node_id}, rect={self.rect})"


class ScreenCastSession:
    def __init__(self, config, token_key):
        self.config = config
        self.token_key = token_key
        self.session = None
        self.fd = None
        self.streams = []

    def _cursor_mode(self, show_cursor):
        wanted = CURSOR_EMBEDDED if show_cursor else CURSOR_HIDDEN
        try:
            res = portal.connection().call_sync(
                portal.BUS_NAME, portal.OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (IFACE, "AvailableCursorModes")), None, Gio.DBusCallFlags.NONE, 2000, None)
            available = res.unpack()[0]
        except GLib.Error:
            return wanted
        if available & wanted:
            return wanted
        for mode in (CURSOR_EMBEDDED, CURSOR_HIDDEN, CURSOR_METADATA):
            if available & mode:
                return mode
        return wanted

    def start(self, callback, show_cursor=True):
        """callback(ok, error_message). On success self.fd / self.streams are set."""
        def fail(msg):
            self.close()
            callback(False, msg)

        def on_create(code, results):
            if code != 0:
                fail("Could not create a screen-cast session")
                return
            self.session = results.get("session_handle")
            opts = {
                "types": GLib.Variant("u", SOURCE_MONITOR),
                "multiple": GLib.Variant("b", True),
                "cursor_mode": GLib.Variant("u", self._cursor_mode(show_cursor)),
                "persist_mode": GLib.Variant("u", PERSIST_PERMANENT),
            }
            token = self.config.get(self.token_key)
            if token:
                opts["restore_token"] = GLib.Variant("s", token)
            portal.request(IFACE, "SelectSources", "o", (self.session,), opts, on_select)

        def on_select(code, results):
            if code != 0:
                fail("Screen selection was cancelled" if code == 1 else "Could not select screens")
                return
            portal.request(IFACE, "Start", "os", (self.session, ""), {}, on_start)

        def on_start(code, results):
            if code != 0:
                fail("Screen sharing was cancelled" if code == 1 else "Could not start screen sharing")
                return
            token = results.get("restore_token")
            if token:
                self.config.set(self.token_key, token)
            self.streams = [Stream(node, props) for node, props in results.get("streams", [])]
            if not self.streams:
                fail("No screens were shared")
                return
            try:
                res, fds = portal.connection().call_with_unix_fd_list_sync(
                    portal.BUS_NAME, portal.OBJECT_PATH, IFACE, "OpenPipeWireRemote",
                    GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
                    Gio.DBusCallFlags.NONE, 5000, None, None)
                self.fd = fds.get(res.unpack()[0])
            except GLib.Error as exc:
                fail(f"Could not open PipeWire remote: {exc.message}")
                return
            callback(True, None)

        portal.request(IFACE, "CreateSession", "", (),
                       {"session_handle_token": GLib.Variant("s", portal.new_token("glintsess"))}, on_create)

    def stream_for(self, rect, monitors):
        """Pick the stream showing `rect` and return (stream, monitor)."""
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        monitor = None
        for m in monitors:
            if geometry.contains(m.rect, cx, cy):
                monitor = m
        for s in self.streams:
            if s.rect and geometry.contains(s.rect, cx, cy):
                return s, monitor
        if monitor is not None:
            # Streams without positions: match by size.
            for s in self.streams:
                if s.size and tuple(s.size) == (monitor.width, monitor.height):
                    return s, monitor
        return self.streams[0], monitor

    def close(self):
        if self.session:
            try:
                portal.connection().call_sync(portal.BUS_NAME, self.session, "org.freedesktop.portal.Session",
                                              "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
            self.session = None
        if self.fd is not None:
            import os
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def crop_for(rect, stream, monitor):
    """videocrop (left, top, right, bottom) in stream pixels plus the output size."""
    base = stream.rect or (monitor.rect if monitor else (0, 0, rect[2], rect[3]))
    scale = monitor.scale if monitor else 1.0
    full_w, full_h = round(base[2] * scale), round(base[3] * scale)
    x, y, w, h = geometry.even(((rect[0] - base[0]) * scale, (rect[1] - base[1]) * scale,
                                rect[2] * scale, rect[3] * scale))
    x = max(0, min(x, full_w - 2))
    y = max(0, min(y, full_h - 2))
    w = min(w, full_w - x)
    h = min(h, full_h - y)
    w -= w % 2
    h -= h % 2
    return (x, y, full_w - x - w, full_h - y - h), (w, h)
