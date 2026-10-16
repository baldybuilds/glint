"""System tray icon: org.kde.StatusNotifierItem + com.canonical.dbusmenu.

GTK 4 has no tray API, so we export both interfaces ourselves.
"""

from gi.repository import Gio, GLib

from . import APP_NAME, paths

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

SNI_XML = """
<node>
 <interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewTitle"/>
  <signal name="NewIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
 </interface>
</node>
"""

MENU_XML = """
<node>
 <interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
   <arg type="i" name="parentId" direction="in"/>
   <arg type="i" name="recursionDepth" direction="in"/>
   <arg type="as" name="propertyNames" direction="in"/>
   <arg type="u" name="revision" direction="out"/>
   <arg type="(ia{sv}av)" name="layout" direction="out"/>
  </method>
  <method name="GetGroupProperties">
   <arg type="ai" name="ids" direction="in"/>
   <arg type="as" name="propertyNames" direction="in"/>
   <arg type="a(ia{sv})" name="properties" direction="out"/>
  </method>
  <method name="GetProperty">
   <arg type="i" name="id" direction="in"/>
   <arg type="s" name="name" direction="in"/>
   <arg type="v" name="value" direction="out"/>
  </method>
  <method name="Event">
   <arg type="i" name="id" direction="in"/>
   <arg type="s" name="eventId" direction="in"/>
   <arg type="v" name="data" direction="in"/>
   <arg type="u" name="timestamp" direction="in"/>
  </method>
  <method name="EventGroup">
   <arg type="a(isvu)" name="events" direction="in"/>
   <arg type="ai" name="idErrors" direction="out"/>
  </method>
  <method name="AboutToShow">
   <arg type="i" name="id" direction="in"/>
   <arg type="b" name="needUpdate" direction="out"/>
  </method>
  <method name="AboutToShowGroup">
   <arg type="ai" name="ids" direction="in"/>
   <arg type="ai" name="updatesNeeded" direction="out"/>
   <arg type="ai" name="idErrors" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated">
   <arg type="a(ia{sv})" name="updatedProps"/>
   <arg type="a(ias)" name="removedProps"/>
  </signal>
  <signal name="LayoutUpdated">
   <arg type="u" name="revision"/>
   <arg type="i" name="parent"/>
  </signal>
  <signal name="ItemActivationRequested">
   <arg type="i" name="id"/>
   <arg type="u" name="timestamp"/>
  </signal>
 </interface>
</node>
"""


class MenuItem:
    def __init__(self, label=None, command=None, separator=False, children=None, enabled=True, icon=None):
        self.id = 0
        self.label = label
        self.command = command
        self.separator = separator
        self.children = children or []
        self.enabled = enabled
        self.icon = icon

    def props(self):
        p = {}
        if self.separator:
            p["type"] = GLib.Variant("s", "separator")
            return p
        p["label"] = GLib.Variant("s", self.label or "")
        p["enabled"] = GLib.Variant("b", self.enabled)
        p["visible"] = GLib.Variant("b", True)
        if self.icon:
            p["icon-name"] = GLib.Variant("s", self.icon)
        if self.children:
            p["children-display"] = GLib.Variant("s", "submenu")
        return p


class Tray:
    def __init__(self, connection, on_command):
        self.bus = connection
        self.on_command = on_command
        self.revision = 1
        self.recording = False
        self.items = {}
        self.root = MenuItem("root")
        self._ids = []
        self._registered = False
        self.rebuild()

        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._ids.append(self.bus.register_object(SNI_PATH, sni, self._sni_call, self._sni_get, None))
        self._ids.append(self.bus.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None))

        # (Re-)register whenever a StatusNotifierWatcher appears (e.g. plasmashell restarts).
        self._watch = Gio.bus_watch_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, None)

    def _on_watcher_appeared(self, conn, name, owner):
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                  "RegisterStatusNotifierItem", GLib.Variant("(s)", (conn.get_unique_name(),)),
                  None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        self._registered = True

    # -- menu model ------------------------------------------------------------

    def _menu(self):
        if self.recording:
            top = [MenuItem("Stop Recording", "stop-recording", icon="media-playback-stop"),
                   MenuItem(separator=True)]
        else:
            top = []
        timer = MenuItem("Self-Timer", children=[
            MenuItem("3 seconds", "self-timer?seconds=3"),
            MenuItem("5 seconds", "self-timer?seconds=5"),
            MenuItem("10 seconds", "self-timer?seconds=10"),
        ])
        record = MenuItem("Record Screen", children=[
            MenuItem("Record Video", "record-screen?format=mp4"),
            MenuItem("Record GIF", "record-screen?format=gif"),
        ], enabled=not self.recording)
        return top + [
            MenuItem("Capture Area", "capture-area"),
            MenuItem("Capture Previous Area", "capture-previous-area"),
            MenuItem("Capture Fullscreen", "capture-fullscreen"),
            MenuItem("Capture Window", "capture-window"),
            timer,
            MenuItem("Scrolling Capture", "scrolling-capture"),
            MenuItem("Capture Text (OCR)", "capture-text"),
            record,
            MenuItem(separator=True),
            MenuItem("Open Annotate Tool…", "open-annotate"),
            MenuItem("Trim Video…", "trim-video"),
            MenuItem("Open from Clipboard", "open-from-clipboard"),
            MenuItem("Pin to the Screen…", "pin"),
            MenuItem("Capture History…", "history"),
            MenuItem("Restore Recently Closed", "restore-recently-closed"),
            MenuItem(separator=True),
            MenuItem("Open Screenshots Folder", "open-folder"),
            MenuItem("Preferences…", "preferences"),
            MenuItem("About Glint", "about"),
            MenuItem(f"Quit {APP_NAME}", "quit"),
        ]

    def rebuild(self):
        self.items = {0: self.root}
        self.root.children = self._menu()
        counter = [1]

        def assign(item):
            item.id = counter[0]
            counter[0] += 1
            self.items[item.id] = item
            for child in item.children:
                assign(child)

        for child in self.root.children:
            assign(child)
        self.revision += 1

    def set_recording(self, recording):
        if recording == self.recording:
            return
        self.recording = recording
        self.rebuild()
        self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))
        self._emit(SNI_PATH, "org.kde.StatusNotifierItem", "NewIcon", None)
        self._emit(SNI_PATH, "org.kde.StatusNotifierItem", "NewStatus",
                   GLib.Variant("(s)", ("NeedsAttention" if recording else "Active",)))

    def _emit(self, path, iface, signal, params):
        try:
            self.bus.emit_signal(None, path, iface, signal, params)
        except GLib.Error:
            pass

    def _layout(self, item, depth):
        children = []
        if depth != 0:
            for child in item.children:
                children.append(GLib.Variant("(ia{sv}av)", self._layout(child, depth - 1)))
        return (item.id, item.props() if item.id else {"children-display": GLib.Variant("s", "submenu")}, children)

    # -- D-Bus handlers --------------------------------------------------------

    def _icon(self):
        return "media-record" if self.recording else "io.github.baldybuilds.Glint-tray-symbolic"

    def _sni_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "glint"),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", "NeedsAttention" if self.recording else "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self._icon()),
            "IconThemePath": GLib.Variant("s", paths.icons_dir()),
            "AttentionIconName": GLib.Variant("s", "media-record"),
            "OverlayIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], APP_NAME,
                                                     "Recording — click to stop" if self.recording
                                                     else "Screenshots and recordings")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _sni_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "Activate":
            self.on_command("stop-recording" if self.recording else "capture-area")
        elif method == "SecondaryActivate":
            self.on_command("capture-fullscreen")
        invocation.return_value(None)

    def _menu_get(self, _conn, _sender, _path, _iface, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [paths.icons_dir()]),
        }.get(prop)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            parent, depth, _names = params.unpack()
            item = self.items.get(parent, self.root)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(item, depth))))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = [(i, self.items[i].props()) for i in ids if i in self.items and i != 0]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            props = self.items[item_id].props() if item_id in self.items else {}
            invocation.return_value(GLib.Variant("(v)", (props.get(name, GLib.Variant("s", "")),)))
        elif method == "Event":
            item_id, event, _data, _ts = params.unpack()
            if event == "clicked":
                self._activate(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            errors = []
            for item_id, event, _data, _ts in events:
                if item_id not in self.items:
                    errors.append(item_id)
                elif event == "clicked":
                    self._activate(item_id)
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _activate(self, item_id):
        item = self.items.get(item_id)
        if item and item.command:
            # Let the menu close before overlays grab the screen.
            GLib.timeout_add(250, lambda: self.on_command(item.command) and False)

    def shutdown(self):
        for reg in self._ids:
            self.bus.unregister_object(reg)
        self._ids = []
        Gio.bus_unwatch_name(self._watch)
