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
# One remembered screen-sharing choice for recording and scrolling capture,
# so the desktop's consent dialog appears once rather than once per feature.
TOKEN_KEY = "recording.restore_token"


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


class CropPlan:
    """How to crop a stream of px_w x px_h pixels down to the selection."""

    def __init__(self, left, top, right, bottom, width, height, whole):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom
        self.width, self.height = width, height
        self.whole = whole   # True: the stream didn't match the selection, so all of it is used

    def __repr__(self):
        return (f"CropPlan(l={self.left}, t={self.top}, r={self.right}, b={self.bottom}, "
                f"{self.width}x{self.height}, whole={self.whole})")


def plan_crop(rect, base, px_w, px_h):
    """Crop for the logical `rect`, given a stream of px_w x px_h pixels that is
    expected to show the logical area `base` (the stream's own geometry, or the
    monitor it belongs to).

    If the stream's shape doesn't match `base`, it isn't the full screen we
    assumed (e.g. the user shared a region or window in the portal dialog), so
    we keep the whole stream. Output sizes are always even (4:2:0 encoders).
    """
    def whole():
        return CropPlan(0, 0, px_w % 2, px_h % 2, px_w - px_w % 2, px_h - px_h % 2, True)

    if base is None or px_w < 2 or px_h < 2 or base[2] <= 0 or base[3] <= 0:
        return whole()
    if abs(px_w / px_h - base[2] / base[3]) > 0.02:
        return whole()
    scale = px_w / base[2]
    x0 = max(0, round((rect[0] - base[0]) * scale))
    y0 = max(0, round((rect[1] - base[1]) * scale))
    x1 = min(px_w, round((rect[0] + rect[2] - base[0]) * scale))
    y1 = min(px_h, round((rect[1] + rect[3] - base[1]) * scale))
    w, h = x1 - x0, y1 - y0
    if w < 2 or h < 2:
        return whole()  # selection isn't on this stream at all
    w -= w % 2
    h -= h % 2
    return CropPlan(x0, y0, px_w - x0 - w, px_h - y0 - h, w, h, False)


def apply_crop_when_ready(source, crop, rect, base, on_plan):
    """Set `crop` (a videocrop element) from the real stream size.

    The portal often omits stream geometry, so the pixel size is only known
    once pipewiresrc negotiates. A probe catches that first CAPS event and
    configures videocrop before it is negotiated downstream. on_plan(plan) is
    then called on the main loop.
    """
    from gi.repository import Gst

    def probe(_pad, info):
        event = info.get_event()
        if event is None or event.type != Gst.EventType.CAPS:
            return Gst.PadProbeReturn.OK
        st = event.parse_caps().get_structure(0)
        ok_w, px_w = st.get_int("width")
        ok_h, px_h = st.get_int("height")
        if not (ok_w and ok_h):
            return Gst.PadProbeReturn.OK
        plan = plan_crop(rect, base, px_w, px_h)
        for prop in ("left", "top", "right", "bottom"):
            crop.set_property(prop, getattr(plan, prop))
        GLib.idle_add(lambda: on_plan(plan) and False)
        return Gst.PadProbeReturn.REMOVE

    source.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, probe)
