"""Full-desktop capture backends and the multi-monitor DesktopShot model.

Wayland forbids reading other clients' pixels directly, so every backend asks
something privileged to do it:

* spectacle  — KDE's tool, authorised for KWin's ScreenShot2 interface. Fast
               and silent (``-b -n``), so it is preferred on Plasma.
* portal     — org.freedesktop.portal.Screenshot; works everywhere but some
               desktops show a permission dialog.
* grim       — wlroots compositors (Sway, Hyprland).
"""

import os
import shutil

from gi.repository import Gdk, GLib

from . import geometry, paths, portal, system


class Monitor:
    def __init__(self, connector, x, y, w, h, scale=1.0, gdk_monitor=None):
        self.connector = connector
        self.rect = (x, y, w, h)
        self.scale = scale
        self.gdk_monitor = gdk_monitor

    @property
    def x(self):
        return self.rect[0]

    @property
    def y(self):
        return self.rect[1]

    @property
    def width(self):
        return self.rect[2]

    @property
    def height(self):
        return self.rect[3]

    def __repr__(self):
        return f"Monitor({self.connector}, {self.rect}, scale={self.scale})"


def current_monitors():
    display = Gdk.Display.get_default()
    model = display.get_monitors()
    out = []
    for i in range(model.get_n_items()):
        m = model.get_item(i)
        g = m.get_geometry()
        scale = m.get_scale() if hasattr(m, "get_scale") else m.get_scale_factor()
        out.append(Monitor(m.get_connector() or f"monitor-{i}", g.x, g.y, g.width, g.height, scale, m))
    return out


def monitor_at(monitors, x, y):
    for m in monitors:
        if geometry.contains(m.rect, x, y):
            return m
    return monitors[0] if monitors else None


class DesktopShot:
    """A screenshot of the whole desktop plus the monitor layout it maps to."""

    def __init__(self, path, monitors):
        self.path = path
        self.pixbuf = system.load_pixbuf(path)
        self.monitors = monitors
        self.bbox = geometry.union(m.rect for m in monitors)
        self.ratio = self.pixbuf.get_width() / max(1, self.bbox[2])

    @property
    def origin(self):
        return (self.bbox[0], self.bbox[1])

    def pixel_rect(self, rect):
        return geometry.to_pixels(rect, self.origin, self.ratio,
                                  (self.pixbuf.get_width(), self.pixbuf.get_height()))

    def crop(self, rect):
        """Pixbuf for a logical rect, or None if it is off-screen."""
        px = self.pixel_rect(rect)
        if px is None or px[2] < 1 or px[3] < 1:
            return None
        return self.pixbuf.new_subpixbuf(*px).copy()

    def crop_to_file(self, rect, path):
        pb = self.crop(rect)
        if pb is None:
            return None
        return system.save_pixbuf(pb, path)


def _tmp_png(prefix):
    return os.path.join(paths.captures_cache_dir(), f"{prefix}-{GLib.get_real_time()}.png")


class Backend:
    name = "base"

    @staticmethod
    def available():
        return False

    def grab(self, callback, cursor=False):
        """callback(path or None, error_message)."""
        raise NotImplementedError


class SpectacleBackend(Backend):
    name = "spectacle"

    @staticmethod
    def available():
        return shutil.which("spectacle") is not None and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "")

    def grab(self, callback, cursor=False):
        out = _tmp_png("desktop")
        argv = ["spectacle", "-b", "-n", "-f", "-o", out]
        if cursor:
            argv.append("-p")

        def done(ok, _stdout, stderr):
            if ok and os.path.exists(out):
                callback(out, None)
            else:
                callback(None, stderr.strip() or "spectacle failed")

        system.run_capture(argv, done)


class GrimBackend(Backend):
    name = "grim"

    @staticmethod
    def available():
        return shutil.which("grim") is not None and bool(os.environ.get("WAYLAND_DISPLAY"))

    def grab(self, callback, cursor=False):
        out = _tmp_png("desktop")
        argv = ["grim"] + (["-c"] if cursor else []) + [out]
        system.run_capture(argv, lambda ok, _o, err: callback(out if ok else None, None if ok else err))


class PortalBackend(Backend):
    name = "portal"

    @staticmethod
    def available():
        return True

    def grab(self, callback, cursor=False):
        def done(path):
            if not path:
                callback(None, "screenshot portal returned no image")
                return
            # The portal may hand us a file in ~/Pictures; move it into our cache.
            out = _tmp_png("desktop")
            try:
                shutil.move(path, out)
            except OSError:
                out = path
            callback(out, None)

        portal.screenshot(done, interactive=False)


BACKENDS = {b.name: b for b in (SpectacleBackend, GrimBackend, PortalBackend)}


def get_backend(preference="auto"):
    if preference in BACKENDS and BACKENDS[preference].available():
        return BACKENDS[preference]()
    for cls in (SpectacleBackend, GrimBackend, PortalBackend):
        if cls.available():
            return cls()
    return PortalBackend()


def grab_desktop(config, callback):
    """callback(DesktopShot or None, error)."""
    backend = get_backend(config.get("general.backend"))
    monitors = current_monitors()

    def done(path, err):
        if not path:
            callback(None, err)
            return
        try:
            callback(DesktopShot(path, monitors), None)
        except GLib.Error as exc:
            callback(None, exc.message)

    backend.grab(done, cursor=config.get("general.capture_cursor"))
