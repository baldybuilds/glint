"""Desktop integration helpers: clipboard, sounds, file manager, image I/O."""

import os
import shutil
import warnings

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402


def have(cmd):
    return shutil.which(cmd) is not None


def spawn(argv, stdin_path=None, on_exit=None):
    """Fire-and-forget subprocess. on_exit(success: bool) is optional."""
    try:
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDERR_SILENCE)
        if stdin_path:
            launcher.set_stdin_file_path(stdin_path)
        proc = launcher.spawnv(argv)
    except GLib.Error as exc:
        print(f"glint: failed to run {argv[0]}: {exc.message}")
        if on_exit:
            on_exit(False)
        return None
    if on_exit:
        def done(p, res):
            try:
                ok = p.wait_check_finish(res)
            except GLib.Error:
                ok = False
            on_exit(ok)
        proc.wait_check_async(None, done)
    return proc


def run_capture(argv, callback):
    """Run argv, callback(ok, stdout_text, stderr_text)."""
    try:
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    except GLib.Error as exc:
        callback(False, "", exc.message)
        return

    def done(p, res):
        try:
            _ok, out, err = p.communicate_utf8_finish(res)
        except GLib.Error as exc:
            callback(False, "", exc.message)
            return
        callback(p.get_successful(), out or "", err or "")

    proc.communicate_utf8_async(None, None, done)


# Clipboard -------------------------------------------------------------------

def _is_wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def copy_image(path):
    """Copy an image file to the clipboard.

    On Wayland we prefer wl-copy: it uses the data-control protocol and works
    even when none of our windows has keyboard focus (e.g. after a global
    shortcut), which Gdk's clipboard cannot guarantee.
    """
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    if _is_wayland() and have("wl-copy"):
        spawn(["wl-copy", "--type", mime], stdin_path=path)
        return
    texture = Gdk.Texture.new_from_filename(path)
    Gdk.Display.get_default().get_clipboard().set(texture)


def copy_text(text):
    if _is_wayland() and have("wl-copy"):
        spawn(["wl-copy", "--", text])
        return
    Gdk.Display.get_default().get_clipboard().set(text)


def copy_file(path):
    uri = Gio.File.new_for_path(path).get_uri()
    if _is_wayland() and have("wl-copy"):
        spawn(["wl-copy", "--type", "text/uri-list", "--", uri + "\r\n"])
        return
    Gdk.Display.get_default().get_clipboard().set(Gdk.FileList.new_from_list([Gio.File.new_for_path(path)]))


def read_clipboard_image(callback):
    """callback(path or None): saves the clipboard image to a temp PNG."""
    from . import paths

    out = os.path.join(paths.captures_cache_dir(), f"clipboard-{GLib.get_real_time()}.png")
    if _is_wayland() and have("wl-paste"):
        def got(ok, _out, _err):
            # wl-paste wrote to stdout which we redirected below; check file.
            callback(out if ok and os.path.exists(out) and os.path.getsize(out) > 0 else None)
        argv = ["sh", "-c", 'wl-paste --type image/png > "$1"', "sh", out]
        run_capture(argv, got)
        return
    clipboard = Gdk.Display.get_default().get_clipboard()

    def done(cb, res):
        try:
            texture = cb.read_texture_finish(res)
        except GLib.Error:
            texture = None
        if texture is None:
            callback(None)
            return
        texture.save_to_png(out)
        callback(out)

    clipboard.read_texture_async(None, done)


# Sounds / notifications ----------------------------------------------------------

def play_sound(event_id="screen-capture"):
    if have("canberra-gtk-play"):
        spawn(["canberra-gtk-play", "-i", event_id])
        return
    fallback = f"/usr/share/sounds/freedesktop/stereo/{event_id}.oga"
    if os.path.exists(fallback) and have("pw-play"):
        spawn(["pw-play", fallback])


def notify(app, title, body="", notification_id=None):
    n = Gio.Notification.new(title)
    if body:
        n.set_body(body)
    app.send_notification(notification_id, n)


# Files ---------------------------------------------------------------------------

def open_path(path):
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


def reveal_in_folder(path):
    uri = Gio.File.new_for_path(path).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        open_path(os.path.dirname(path))


# Images --------------------------------------------------------------------------

def load_pixbuf(path):
    return GdkPixbuf.Pixbuf.new_from_file(path)


def save_pixbuf(pixbuf, path, fmt="png", quality=90):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if fmt in ("jpg", "jpeg"):
        if pixbuf.get_has_alpha():
            flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pixbuf.get_width(), pixbuf.get_height())
            flat.fill(0xFFFFFFFF)
            pixbuf.composite(flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(), 0, 0, 1, 1,
                             GdkPixbuf.InterpType.NEAREST, 255)
            pixbuf = flat
        pixbuf.savev(path, "jpeg", ["quality"], [str(int(quality))])
    else:
        pixbuf.savev(path, "png", [], [])
    return path


def convert_image(src, dst, fmt, quality=90):
    if fmt == "png" and src.lower().endswith(".png"):
        shutil.copyfile(src, dst)
        return dst
    return save_pixbuf(load_pixbuf(src), dst, fmt, quality)


def texture_from_pixbuf(pixbuf):
    """Gdk.MemoryTexture from a pixbuf (avoids the deprecated new_for_pixbuf)."""
    pb = pixbuf.copy()
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())


def cairo_set_source_pixbuf(cr, pixbuf, x=0, y=0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)


def rgba(r, g, b, a=1.0):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c
