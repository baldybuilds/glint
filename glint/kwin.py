"""KWin scripting bridge.

Wayland clients cannot position their own windows, keep them above others or
learn where other windows are. On Plasma we work around that by loading small
KWin scripts that:

* report the stacking order and geometry of windows (window-capture mode), and
* apply rules to our own windows: any window whose title starts with
  ``glint:`` is kept above, hidden from the task bar/switcher and, when the
  title carries coordinates (``glint:<role>:<x>:<y>``), moved there. Changing
  the title later moves the window again.
"""

import json
import os

from gi.repository import Gio, GLib

from . import APP_ID, paths

KWIN = "org.kde.KWin"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"
BRIDGE_PATH = "/io/github/baldybuilds/Glint/KWinBridge"
BRIDGE_IFACE = "io.github.baldybuilds.Glint.KWinBridge"

_BRIDGE_XML = f"""
<node>
  <interface name="{BRIDGE_IFACE}">
    <method name="WindowList"><arg type="s" name="json" direction="in"/></method>
  </interface>
</node>
"""

_QUERY_JS = """
const out = [];
const list = workspace.stackingOrder || workspace.windowList();
for (const w of list) {
  if (w.minimized || w.deleted) continue;
  if (!(w.normalWindow || w.dialog || w.utility)) continue;
  const onCurrent = w.onAllDesktops || !w.desktops ||
      w.desktops.some(d => d === workspace.currentDesktop || (d && workspace.currentDesktop && d.id === workspace.currentDesktop.id));
  if (!onCurrent) continue;
  const g = w.frameGeometry;
  out.push({caption: w.caption || "", app: w.resourceClass || "",
            x: g.x, y: g.y, w: g.width, h: g.height});
}
const cp = workspace.cursorPos;
const areas = [];
try {
  for (const s of workspace.screens) {
    const a = workspace.clientArea(KWin.PlacementArea, s, workspace.currentDesktop);
    areas.push({name: s.name, x: a.x, y: a.y, w: a.width, h: a.height});
  }
} catch (e) {}
callDBus("%(service)s", "%(path)s", "%(iface)s", "WindowList",
         JSON.stringify({windows: out, cursor: cp ? {x: cp.x, y: cp.y} : null, areas: areas}));
"""

_RULES_JS = """
function glintApply(w) {
  const c = w.caption || "";
  if (c.indexOf("glint:") !== 0) return;
  const parts = c.split(":");
  w.keepAbove = true;
  w.skipTaskbar = true;
  w.skipPager = true;
  w.skipSwitcher = true;
  if (parts.length >= 4) {
    const x = parseInt(parts[2], 10), y = parseInt(parts[3], 10);
    if (!isNaN(x) && !isNaN(y)) {
      const g = w.frameGeometry;
      w.frameGeometry = {x: x, y: y, width: g.width, height: g.height};
    }
  }
}
function glintWatch(w) {
  glintApply(w);
  w.captionChanged.connect(function () { glintApply(w); });
}
workspace.windowAdded.connect(glintWatch);
for (const w of workspace.windowList()) glintWatch(w);
"""


def available():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "NameHasOwner", GLib.Variant("(s)", (KWIN,)), None, Gio.DBusCallFlags.NONE, 1000, None)
        return bool(res.unpack()[0])
    except GLib.Error:
        return False


class KWinBridge:
    def __init__(self, connection):
        self.bus = connection
        self.enabled = available()
        self._pending = []
        self._seq = 0
        self._rules_loaded = False
        self.work_areas = {}   # connector -> usable rect (excludes panels)
        if self.enabled:
            info = Gio.DBusNodeInfo.new_for_xml(_BRIDGE_XML).interfaces[0]
            self.bus.register_object(BRIDGE_PATH, info, self._on_call, None, None)

    # -- D-Bus plumbing ------------------------------------------------------

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "WindowList":
            try:
                data = json.loads(params.unpack()[0])
            except ValueError:
                data = {}
            for area in data.get("areas") or []:
                self.work_areas[area["name"]] = (area["x"], area["y"], area["w"], area["h"])
            pending, self._pending = self._pending, []
            for cb in pending:
                cb(data.get("windows") or [], data.get("cursor"))
        invocation.return_value(None)

    def _scripting(self, method, args, fmt, reply=None):
        return self.bus.call_sync(KWIN, "/Scripting", SCRIPTING_IFACE, method,
                                  GLib.Variant(fmt, args), GLib.VariantType(reply) if reply else None,
                                  Gio.DBusCallFlags.NONE, 3000, None)

    def _run_script(self, source, plugin_name):
        path = os.path.join(paths.scripts_cache_dir(), plugin_name + ".js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        try:
            self._scripting("unloadScript", (plugin_name,), "(s)")
        except GLib.Error:
            pass
        script_id = self._scripting("loadScript", (path, plugin_name), "(ss)", "(i)").unpack()[0]
        for obj in (f"/Scripting/Script{script_id}", f"/{script_id}"):
            try:
                self.bus.call_sync(KWIN, obj, "org.kde.kwin.Script", "run", None, None,
                                   Gio.DBusCallFlags.NONE, 3000, None)
                return True
            except GLib.Error:
                continue
        return False

    def _unload(self, plugin_name):
        try:
            self._scripting("unloadScript", (plugin_name,), "(s)")
        except GLib.Error:
            pass

    # -- public API ----------------------------------------------------------

    def query_windows(self, callback, timeout_ms=1500):
        """callback(windows, cursor).

        windows: list of {caption, app, x, y, w, h} in stacking order
        (bottom-most first); cursor: {x, y} or None. Calls back with
        ([], None) if KWin is unavailable.
        """
        if not self.enabled:
            GLib.idle_add(lambda: callback([], None) and False)
            return
        self._seq += 1
        plugin = f"glint-query-{os.getpid()}-{self._seq}"
        state = {"done": False}

        def finish(windows, cursor=None):
            if state["done"]:
                return
            state["done"] = True
            if finish in self._pending:
                self._pending.remove(finish)
            self._unload(plugin)
            callback([w for w in windows if not w.get("caption", "").startswith("glint:")], cursor)

        self._pending.append(finish)
        source = _QUERY_JS % {"service": APP_ID, "path": BRIDGE_PATH, "iface": BRIDGE_IFACE}
        try:
            if not self._run_script(source, plugin):
                finish([])
                return
        except GLib.Error as exc:
            print(f"glint: KWin query failed: {exc.message}")
            finish([])
            return
        GLib.timeout_add(timeout_ms, lambda: finish([]) and False)

    def work_area(self, monitor):
        """Usable area of a capture.Monitor (panels excluded) if KWin told us."""
        return self.work_areas.get(monitor.connector, monitor.rect)

    def install_rules(self):
        if not self.enabled or self._rules_loaded:
            return
        try:
            self._rules_loaded = self._run_script(_RULES_JS, "glint-window-rules")
        except GLib.Error as exc:
            print(f"glint: could not load KWin rules: {exc.message}")

    def shutdown(self):
        if self._rules_loaded:
            self._unload("glint-window-rules")
            self._rules_loaded = False


def place_title(role, x=None, y=None):
    """Window title that the rules script understands."""
    if x is None or y is None:
        return f"glint:{role}"
    return f"glint:{role}:{int(x)}:{int(y)}"
