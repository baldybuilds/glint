"""Make Glint the desktop's screenshot tool (and undo it).

KDE Plasma: Spectacle owns Print, Meta+Shift+S and friends through KGlobalAccel.
We back up Spectacle's current keys, clear them, and give the same keys to the
matching actions of Glint's own KGlobalAccel component (registered from
io.github.baldybuilds.Glint.desktop like Spectacle's). Restoring reverses both sides.
Spectacle stays installed: Glint uses it as its capture backend.

GNOME: the Shell's screenshot keybindings are cleared via GSettings and the
same accelerators are re-created as custom keybindings running `glint …`.
"""

import os
import subprocess
from collections import OrderedDict

from gi.repository import Gio, GLib

SPECTACLE = "org.kde.spectacle.desktop"
GLINT = "io.github.baldybuilds.Glint.desktop"
LEGACY_GLINT = "app.glint.Glint.desktop"   # component name before 0.3.0

# Glint's desktop actions: (KGlobalAccel action id, CLI command, label)
GLINT_ACTIONS = [
    ("CaptureArea", "capture-area", "Capture Area"),
    ("CaptureFullscreen", "capture-fullscreen", "Capture Fullscreen"),
    ("CaptureWindow", "capture-window", "Capture Window"),
    ("CapturePreviousArea", "capture-previous-area", "Capture Previous Area"),
    ("ScrollingCapture", "scrolling-capture", "Scrolling Capture"),
    ("CaptureText", "capture-text", "Capture Text (OCR)"),
    ("RecordScreen", "record-screen", "Record Screen"),
]
GLINT_LABELS = {a: label for a, _c, label in GLINT_ACTIONS}

# Spectacle action -> Glint action that inherits its keys.
SPECTACLE_MAP = OrderedDict([
    ("_launch", "CaptureArea"),
    ("RectangularRegionScreenShot", "CaptureArea"),
    ("FullScreenScreenShot", "CaptureFullscreen"),
    ("CurrentMonitorScreenShot", "CaptureFullscreen"),
    ("ActiveWindowScreenShot", "CaptureWindow"),
    ("WindowUnderCursorScreenShot", "CaptureWindow"),
    ("RecordRegion", "RecordScreen"),
    ("RecordScreen", "RecordScreen"),
    ("RecordWindow", "RecordScreen"),
])

# -- Qt key codes ------------------------------------------------------------------

MODIFIERS = [(0x10000000, "Meta"), (0x04000000, "Ctrl"), (0x08000000, "Alt"), (0x02000000, "Shift")]
KEYPAD = 0x20000000
SPECIAL = {
    0x01000000: "Esc", 0x01000001: "Tab", 0x01000003: "Backspace", 0x01000004: "Return",
    0x01000005: "Enter", 0x01000006: "Ins", 0x01000007: "Del", 0x01000008: "Pause", 0x01000009: "Print",
    0x0100000A: "SysReq", 0x01000010: "Home", 0x01000011: "End", 0x01000012: "Left", 0x01000013: "Up",
    0x01000014: "Right", 0x01000015: "Down", 0x01000016: "PgUp", 0x01000017: "PgDown", 0x20: "Space",
}
SPECIAL.update({0x01000030 + i: f"F{i + 1}" for i in range(35)})
SPECIAL_BY_NAME = {v.lower(): k for k, v in SPECIAL.items()}


def key_to_string(code):
    """Qt key code (with modifier bits) -> "Meta+Shift+S"."""
    if not code:
        return ""
    parts = [name for bit, name in MODIFIERS if code & bit]
    key = code & ~(0x10000000 | 0x04000000 | 0x08000000 | 0x02000000 | KEYPAD)
    if key in SPECIAL:
        parts.append(SPECIAL[key])
    elif 0x20 < key < 0x7F:
        parts.append(chr(key).upper())
    else:
        parts.append(f"0x{key:x}")
    return "+".join(parts)


def parse_key(text):
    """ "Meta+Shift+S" -> Qt key code (inverse of key_to_string)."""
    code = 0
    names = {name.lower(): bit for bit, name in MODIFIERS}
    names.update({"super": 0x10000000, "control": 0x04000000})
    parts = text.split("+")
    for part in parts[:-1]:
        code |= names[part.strip().lower()]
    key = parts[-1].strip()
    if key.lower() in SPECIAL_BY_NAME:
        code |= SPECIAL_BY_NAME[key.lower()]
    elif len(key) == 1:
        code |= ord(key.upper())
    else:
        raise ValueError(f"unknown key {key!r}")
    return code


def _dedupe(seq):
    out = []
    for item in seq:
        if item and item not in out:
            out.append(item)
    return out


# -- KDE -----------------------------------------------------------------------------

class KGlobalAccel:
    """Thin D-Bus adapter for org.kde.KGlobalAccel."""

    BUS = "org.kde.kglobalaccel"

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def _call(self, path, iface, method, args=None, fmt=None, reply=None):
        return self.bus.call_sync(self.BUS, path, iface, method, GLib.Variant(fmt, args) if fmt else None,
                                  GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 3000, None)

    def available(self):
        try:
            res = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                     "NameHasOwner", GLib.Variant("(s)", (self.BUS,)), None,
                                     Gio.DBusCallFlags.NONE, 1000, None)
            return bool(res.unpack()[0])
        except GLib.Error:
            return False

    def _infos(self, path):
        return self._call(path, "org.kde.kglobalaccel.Component", "allShortcutInfos").unpack()[0]

    def has_component(self, component):
        return bool(self.actions(component))

    def actions(self, component):
        """{action: {"keys": [int], "friendly": str, "component_friendly": str}}; {} if absent."""
        try:
            path = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "getComponent", (component,), "(s)",
                              "(o)").unpack()[0]
            infos = self._infos(path)
        except GLib.Error:
            return {}
        return {i[0]: {"keys": [k for k in i[6] if k], "friendly": i[1], "component_friendly": i[3],
                       "defaults": [k for k in i[7] if k]}
                for i in infos}

    def all_infos(self):
        """Every (component, action, action_friendly) known to KGlobalAccel."""
        try:
            paths = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "allComponents").unpack()[0]
        except GLib.Error:
            return []
        out = []
        for path in paths:
            try:
                out += [(i[2], i[0], i[1]) for i in self._infos(path)]
            except GLib.Error:
                continue
        return out

    def set_keys(self, component, component_friendly, action, action_friendly, keys):
        sequences = [([k, 0, 0, 0],) for k in keys]
        self._call("/kglobalaccel", "org.kde.KGlobalAccel", "setForeignShortcutKeys",
                   ([component, action, component_friendly, action_friendly], sequences), "(asa(ai))")

    def unregister(self, component, action):
        try:
            self._call("/kglobalaccel", "org.kde.KGlobalAccel", "unregister", (component, action), "(ss)")
        except GLib.Error:
            pass

    def owners(self, key):
        try:
            rows = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "getGlobalShortcutsByKey", (key,), "(i)",
                              "(a(ssssssaiai))").unpack()[0]
        except GLib.Error:
            return []
        return [(r[2], r[0]) for r in rows]


class KDETakeover:
    desktop = "kde"
    tool_name = "Spectacle"

    def __init__(self, config, kga=None):
        self.config = config
        self.kga = kga or KGlobalAccel()

    def available(self):
        return self.kga.available() and self.kga.has_component(SPECTACLE) and self.kga.has_component(GLINT)

    def reason_unavailable(self):
        if not self.kga.available():
            return "KDE's global shortcut service is not running."
        if not self.kga.has_component(SPECTACLE):
            return "Spectacle's shortcuts were not found."
        if not self.kga.has_component(GLINT):
            return "Glint's shortcuts are not registered yet (install Glint, then log in again)."
        return ""

    def active(self):
        saved = self.config.get("state.takeover")
        return bool(saved) and saved.get("desktop") == self.desktop

    def plan(self):
        """[(key string, Glint action label)] that taking over would assign."""
        spectacle = self.kga.actions(SPECTACLE)
        rows = []
        for action, target in SPECTACLE_MAP.items():
            for key in spectacle.get(action, {}).get("keys", []):
                rows.append((key_to_string(key), GLINT_LABELS[target]))
        return rows

    def take_over(self):
        if self.active():
            return self.plan_from_backup()
        spectacle = self.kga.actions(SPECTACLE)
        glint = self.kga.actions(GLINT)
        if not spectacle or not glint:
            raise RuntimeError(self.reason_unavailable() or "Shortcut components missing")
        backup = {
            "desktop": self.desktop,
            "spectacle": {a: info["keys"] for a, info in spectacle.items()},
            "glint": {a: info["keys"] for a, info in glint.items()},
        }
        # Persist before touching anything so a crash can still be undone.
        self.config.set("state.takeover", backup)
        for action, info in spectacle.items():
            if info["keys"]:
                self.kga.set_keys(SPECTACLE, info["component_friendly"], action, info["friendly"], [])
        additions = OrderedDict()
        for action, target in SPECTACLE_MAP.items():
            additions.setdefault(target, []).extend(spectacle.get(action, {}).get("keys", []))
        for target, extra in additions.items():
            info = glint.get(target)
            if info is None or not extra:
                continue
            keys = _dedupe(info["keys"] + extra)
            self.kga.set_keys(GLINT, info["component_friendly"], target, info["friendly"], keys)
        return self.plan_from_backup()

    def plan_from_backup(self):
        saved = self.config.get("state.takeover") or {}
        rows = []
        for action, target in SPECTACLE_MAP.items():
            for key in (saved.get("spectacle") or {}).get(action, []):
                rows.append((key_to_string(key), GLINT_LABELS[target]))
        return rows

    def restore(self):
        saved = self.config.get("state.takeover")
        if not saved or saved.get("desktop") != self.desktop:
            return False
        glint = self.kga.actions(GLINT)
        spectacle = self.kga.actions(SPECTACLE)
        # Free the keys on Glint's side first, then hand them back.
        for action, keys in saved.get("glint", {}).items():
            info = glint.get(action)
            if info is not None and info["keys"] != keys:
                self.kga.set_keys(GLINT, info["component_friendly"], action, info["friendly"], keys)
        for action, keys in saved.get("spectacle", {}).items():
            info = spectacle.get(action)
            if info is not None and info["keys"] != keys:
                self.kga.set_keys(SPECTACLE, info["component_friendly"], action, info["friendly"], keys)
        self.config.set("state.takeover", None)
        return True

    def glint_shortcuts(self):
        """[(label, "Meta+Shift+4, Print")] for Glint's actions, for display."""
        glint = self.kga.actions(GLINT)
        return [(label, ", ".join(key_to_string(k) for k in glint.get(action, {}).get("keys", [])))
                for action, _cmd, label in GLINT_ACTIONS]


def remove_stray_portal_shortcuts(kga, descriptions):
    """Delete Glint shortcuts that the portal filed under another app.

    When Glint was started from a terminal before its app ID was known to the
    portal, Plasma attributed the portal shortcuts to the terminal (e.g.
    org.kde.konsole). Only entries matching both a Glint shortcut id and its
    exact description are removed. Returns the removed (component, action).
    """
    removed = []
    for component, action, friendly in kga.all_infos():
        if component == GLINT:
            continue
        if descriptions.get(action) == friendly:
            kga.unregister(component, action)
            removed.append((component, action))
    return removed


def restore_default_keys(kga):
    """Give Glint actions with no keys their .desktop defaults back, if free.

    KGlobalAccel refuses a default that another component already owns; once
    the conflicting (stray) entry is gone we have to re-apply it ourselves.
    Returns the actions that were fixed.
    """
    fixed = []
    for action, info in kga.actions(GLINT).items():
        if info["keys"] or not info.get("defaults"):
            continue
        free = [k for k in info["defaults"] if not kga.owners(k)]
        if free:
            kga.set_keys(GLINT, info["component_friendly"], action, info["friendly"], free)
            fixed.append(action)
    return fixed


class LegacyShortcutStore:
    """The pre-0.3.0 component's section in ~/.config/kglobalshortcutsrc.

    Once the old .desktop file is removed (e.g. by an RPM upgrade) KDE drops
    the component from its live registry, but the user's keys stay in this
    section. It only lists actions whose keys differ from the defaults.
    """

    GROUP = f"[services][{LEGACY_GLINT}]"

    def __init__(self, path=None):
        self.path = path or os.path.join(GLib.get_user_config_dir(), "kglobalshortcutsrc")

    def read(self):
        """{action: [key codes]} from the saved section ({} if absent)."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return {}
        out, inside = {}, False
        for line in lines:
            if line.startswith("["):
                inside = line.strip() == self.GROUP
                continue
            if not inside or "=" not in line:
                continue
            action, _, value = line.partition("=")
            keys = []
            for text in value.split("\\t"):
                text = text.strip()
                if text and text != "none":
                    try:
                        keys.append(parse_key(text))
                    except (KeyError, ValueError):
                        pass
            out[action.strip()] = keys
        return out

    def clear(self, actions):
        for action in actions:
            subprocess.run(["kwriteconfig6", "--file", os.path.basename(self.path), "--group", "services",
                            "--group", LEGACY_GLINT, "--key", action, "--delete"], check=False)


def migrate_legacy_component(kga, legacy=LEGACY_GLINT, store=None):
    """Move shortcuts from the pre-0.3.0 component to the current one.

    The app ID changed in 0.3.0. The user's keys (their customisations, and
    Print & co. if Glint took over from Spectacle) live either in the old
    component, if KDE still has it registered, or only in its saved section
    of kglobalshortcutsrc, if the old .desktop file is already gone. Give
    them to the same-named new actions, where no one else owns them, then
    forget the old ones. Nothing is touched until the new component exists.

    Returns the moved actions, or None if the new component isn't there yet.
    """
    store = store or LegacyShortcutStore()
    live = kga.actions(legacy)
    saved = store.read()
    old = {a: info["keys"] for a, info in live.items()} if live else {}
    for action, keys in saved.items():
        old.setdefault(action, keys)
    if not old:
        return []
    new = kga.actions(GLINT)
    if not new:
        return None
    for action in live:
        kga.unregister(legacy, action)
    moved = []
    for action, keys in old.items():
        target = new.get(action)
        if target is None or not keys:
            continue
        free = [k for k in keys if k in target["keys"] or not kga.owners(k)]
        merged = _dedupe(target["keys"] + free)
        if merged != target["keys"]:
            kga.set_keys(GLINT, target["component_friendly"], action, target["friendly"], merged)
            moved.append(action)
    if saved:
        store.clear(list(saved))
    return moved


# -- GNOME ---------------------------------------------------------------------------

class GSettingsStore:
    """Minimal GSettings adapter (swapped for a fake in tests)."""

    def has_schema(self, schema):
        source = Gio.SettingsSchemaSource.get_default()
        return source is not None and source.lookup(schema, True) is not None

    def get_strv(self, schema, key, path=None):
        return list(self._settings(schema, path).get_strv(key))

    def set_strv(self, schema, key, value, path=None):
        self._settings(schema, path).set_strv(key, value)

    def set_string(self, schema, key, value, path=None):
        self._settings(schema, path).set_string(key, value)

    def reset(self, schema, key, path=None):
        self._settings(schema, path).reset(key)

    @staticmethod
    def _settings(schema, path):
        return Gio.Settings.new_with_path(schema, path) if path else Gio.Settings.new(schema)


class GnomeTakeover:
    desktop = "gnome"
    tool_name = "GNOME Screenshot"
    SHELL = "org.gnome.shell.keybindings"
    MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
    CUSTOM = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
    BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    MAP = OrderedDict([
        ("show-screenshot-ui", "capture-area"),
        ("screenshot", "capture-fullscreen"),
        ("screenshot-window", "capture-window"),
        ("show-screencast-ui", "record-screen"),
    ])
    LABELS = {cmd: label for _a, cmd, label in GLINT_ACTIONS}

    def __init__(self, config, store=None):
        self.config = config
        self.store = store or GSettingsStore()

    def available(self):
        return self.store.has_schema(self.SHELL) and self.store.has_schema(self.MEDIA)

    def reason_unavailable(self):
        return "" if self.available() else "GNOME Shell keybinding settings were not found."

    def active(self):
        saved = self.config.get("state.takeover")
        return bool(saved) and saved.get("desktop") == self.desktop

    def plan(self):
        rows = []
        for key, cmd in self.MAP.items():
            for accel in self.store.get_strv(self.SHELL, key):
                rows.append((accel, self.LABELS[cmd]))
        return rows

    def take_over(self):
        if self.active():
            return self.plan()
        shell = {key: self.store.get_strv(self.SHELL, key) for key in self.MAP}
        existing = self.store.get_strv(self.MEDIA, "custom-keybindings")
        created = []
        plan = []
        for key, cmd in self.MAP.items():
            for i, accel in enumerate(shell[key]):
                path = f"{self.BASE}glint-{cmd}-{i}/"
                created.append(path)
                plan.append((accel, self.LABELS[cmd]))
        self.config.set("state.takeover", {"desktop": self.desktop, "shell": shell, "created": created})
        for key in self.MAP:
            self.store.set_strv(self.SHELL, key, [])
        for key, cmd in self.MAP.items():
            for i, accel in enumerate(shell[key]):
                path = f"{self.BASE}glint-{cmd}-{i}/"
                self.store.set_string(self.CUSTOM, "name", f"Glint: {self.LABELS[cmd]}", path)
                self.store.set_string(self.CUSTOM, "command", f"glint {cmd}", path)
                self.store.set_string(self.CUSTOM, "binding", accel, path)
        self.store.set_strv(self.MEDIA, "custom-keybindings", existing + [p for p in created if p not in existing])
        return plan

    def restore(self):
        saved = self.config.get("state.takeover")
        if not saved or saved.get("desktop") != self.desktop:
            return False
        created = saved.get("created", [])
        remaining = [p for p in self.store.get_strv(self.MEDIA, "custom-keybindings") if p not in created]
        self.store.set_strv(self.MEDIA, "custom-keybindings", remaining)
        for path in created:
            for key in ("name", "command", "binding"):
                self.store.reset(self.CUSTOM, key, path)
        for key, value in saved.get("shell", {}).items():
            self.store.set_strv(self.SHELL, key, value)
        self.config.set("state.takeover", None)
        return True

    def glint_shortcuts(self):
        saved = self.config.get("state.takeover") or {}
        shell = saved.get("shell", {})
        return [(self.LABELS[cmd], ", ".join(shell.get(key, []))) for key, cmd in self.MAP.items()]


def get_takeover(config):
    """The takeover strategy for this desktop, or None."""
    kde = KDETakeover(config)
    if kde.kga.available():
        return kde
    gnome = GnomeTakeover(config)
    if gnome.available():
        return gnome
    return None
