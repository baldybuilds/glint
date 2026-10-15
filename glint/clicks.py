"""Click highlighting for recordings.

Wayland clients cannot observe global pointer clicks, so Glint borrows
KWin's built-in "Mouse Click Animation" effect (``mouseclick``), which the
compositor draws into the frames we record. For the duration of a recording
we apply Glint's colour/size settings to the effect, load it, and afterwards
put everything back exactly as it was (the originals are also persisted so a
crash mid-recording is repaired on the next start).
"""

import shutil
import subprocess

from gi.repository import Gio, GLib

EFFECT = "mouseclick"
GROUP = "Effect-mouseclick"


def color_to_kconfig(rgba):
    """(r, g, b, a) floats -> KConfig colour string "r,g,b"."""
    return ",".join(str(max(0, min(255, round(c * 255)))) for c in rgba[:3])


class KConfig:
    """Reads/writes kwinrc through kreadconfig6/kwriteconfig6."""

    def __init__(self, file="kwinrc", group=GROUP):
        self.file = file
        self.group = group

    @staticmethod
    def available():
        return shutil.which("kreadconfig6") is not None and shutil.which("kwriteconfig6") is not None

    def read(self, key):
        """Current value, or None if the key is unset."""
        sentinel = "\x01glint-unset\x01"
        out = subprocess.run(["kreadconfig6", "--file", self.file, "--group", self.group, "--key", key,
                              "--default", sentinel], capture_output=True, text=True, check=False).stdout
        value = out.rstrip("\n")
        return None if value == sentinel else value

    def write(self, key, value):
        subprocess.run(["kwriteconfig6", "--file", self.file, "--group", self.group, "--key", key, str(value)],
                       check=False)

    def delete(self, key):
        subprocess.run(["kwriteconfig6", "--file", self.file, "--group", self.group, "--key", key, "--delete"],
                       check=False)


class KWinEffects:
    """org.kde.kwin.Effects over D-Bus."""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def _call(self, method, name):
        return self.bus.call_sync("org.kde.KWin", "/Effects", "org.kde.kwin.Effects", method,
                                  GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, 3000, None)

    def supported(self, name):
        try:
            return bool(self._call("isEffectSupported", name).unpack()[0])
        except GLib.Error:
            return False

    def loaded(self, name):
        try:
            return bool(self._call("isEffectLoaded", name).unpack()[0])
        except GLib.Error:
            return False

    def load(self, name):
        try:
            return bool(self._call("loadEffect", name).unpack()[0])
        except GLib.Error:
            return False

    def unload(self, name):
        try:
            self._call("unloadEffect", name)
        except GLib.Error:
            pass

    def reconfigure(self, name):
        try:
            self._call("reconfigureEffect", name)
        except GLib.Error:
            pass


class ClickHighlighter:
    """enable()/disable() around a recording. `config` is Glint's Config."""

    def __init__(self, config, kconfig=None, effects=None):
        self.config = config
        self.kconfig = kconfig or KConfig()
        self.effects = effects or KWinEffects()
        self.active = False

    def available(self):
        return self.kconfig.available() and self.effects.supported(EFFECT)

    def desired_settings(self):
        color = color_to_kconfig(self.config.get("recording.click_color"))
        return {
            # One colour for every mouse button.
            "Color1": color,
            "Color2": color,
            "Color3": color,
            "RingSize": int(self.config.get("recording.click_size")),
            "LineWidth": float(self.config.get("recording.click_line_width")),
            "RingCount": 2,
            "RingLife": 350,
            "ShowText": "false",
        }

    def enable(self):
        """Returns True if highlighting is now active."""
        if self.active:
            return True
        if not self.available():
            return False
        self.restore_pending()  # repair a previous crash before saving originals
        wanted = self.desired_settings()
        original = {key: self.kconfig.read(key) for key in wanted}
        was_loaded = self.effects.loaded(EFFECT)
        self.config.set("state.click_restore", {"settings": original, "was_loaded": was_loaded})
        for key, value in wanted.items():
            self.kconfig.write(key, value)
        if was_loaded:
            self.effects.reconfigure(EFFECT)
            self.active = True
        else:
            self.active = self.effects.load(EFFECT)
            if not self.active:
                self.restore_pending()
        return self.active

    def disable(self):
        if not self.active:
            return
        self.active = False
        self.restore_pending()

    def restore_pending(self):
        """Undo whatever enable() changed, if anything is recorded."""
        saved = self.config.get("state.click_restore")
        if not saved:
            return
        if not saved.get("was_loaded"):
            self.effects.unload(EFFECT)
        for key, value in (saved.get("settings") or {}).items():
            if value is None:
                self.kconfig.delete(key)
            else:
                self.kconfig.write(key, value)
        if saved.get("was_loaded"):
            self.effects.reconfigure(EFFECT)
        self.config.set("state.click_restore", None)
