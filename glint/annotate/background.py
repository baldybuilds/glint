"""Backdrops for "beautified" screenshots (padding, gradient, rounded corners,
shadow) — used by the editor's Background panel and for window captures."""

import math
import os
import re

import cairo

PRESETS = {
    "ocean": ["#2193b0", "#6dd5ed"],
    "sunset": ["#ff9a8b", "#ff6a88", "#ff99ac"],
    "violet": ["#8e2de2", "#4a00e0"],
    "mint": ["#43e97b", "#38f9d7"],
    "peach": ["#f6d365", "#fda085"],
    "sky": ["#a1c4fd", "#c2e9fb"],
    "dusk": ["#0f2027", "#203a43", "#2c5364"],
    "graphite": ["#434343", "#000000"],
    "white": ["#ffffff"],
    "light": ["#eef0f3"],
    "dark": ["#1e1e22"],
    "transparent": [],
    "wallpaper": None,
}

PRESET_LABELS = {
    "ocean": "Ocean", "sunset": "Sunset", "violet": "Violet", "mint": "Mint",
    "peach": "Peach", "sky": "Sky", "dusk": "Dusk", "graphite": "Graphite",
    "white": "White", "light": "Light", "dark": "Dark",
    "transparent": "Transparent", "wallpaper": "Desktop wallpaper",
}


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def plasma_wallpaper():
    """Best-effort path to the current Plasma wallpaper image."""
    cfg = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")
    try:
        with open(cfg, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    for match in re.finditer(r"^Image=(.+)$", text, re.M):
        path = match.group(1).strip()
        if path.startswith("file://"):
            path = path[7:]
        if os.path.isdir(path):
            # Wallpaper package: pick the largest image in contents/images.
            images_dir = os.path.join(path, "contents", "images")
            try:
                files = sorted(os.listdir(images_dir), key=lambda f: [int(n) for n in re.findall(r"\d+", f)] or [0])
            except OSError:
                continue
            if files:
                return os.path.join(images_dir, files[-1])
        elif os.path.isfile(path):
            return path
    return None


_wallpaper_cache = {}


def _wallpaper_surface():
    path = plasma_wallpaper()
    if not path:
        return None
    if path in _wallpaper_cache:
        return _wallpaper_cache[path]
    surface = None
    try:
        if path.lower().endswith(".png"):
            surface = cairo.ImageSurface.create_from_png(path)
        else:
            from gi.repository import GdkPixbuf

            from .. import system

            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 2560, 2560, True)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pb.get_width(), pb.get_height())
            cr = cairo.Context(surface)
            system.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
    except Exception as exc:  # noqa: BLE001 — any decoder failure just disables the preset
        print(f"glint: wallpaper unavailable: {exc}")
    _wallpaper_cache[path] = surface
    return surface


def paint_background(cr, w, h, preset):
    colors = PRESETS.get(preset, PRESETS["ocean"])
    if preset == "wallpaper":
        surface = _wallpaper_surface()
        if surface is None:
            colors = PRESETS["ocean"]
        else:
            sw, sh = surface.get_width(), surface.get_height()
            scale = max(w / sw, h / sh)
            cr.save()
            cr.rectangle(0, 0, w, h)
            cr.clip()
            cr.translate((w - sw * scale) / 2, (h - sh * scale) / 2)
            cr.scale(scale, scale)
            cr.set_source_surface(surface, 0, 0)
            cr.paint()
            cr.restore()
            return
    if not colors:
        return
    if len(colors) == 1:
        cr.set_source_rgb(*hex_to_rgb(colors[0]))
    else:
        grad = cairo.LinearGradient(0, 0, w, h)
        for i, c in enumerate(colors):
            grad.add_color_stop_rgb(i / (len(colors) - 1), *hex_to_rgb(c))
        cr.set_source(grad)
    cr.rectangle(0, 0, w, h)
    cr.fill()


def rounded_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    if r == 0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def soft_shadow(cr, x, y, w, h, radius, strength=1.0, spread=28):
    """Approximate a blurred drop shadow with stacked translucent rects."""
    steps = 14
    for i in range(steps):
        t = i / steps
        grow = spread * (1 - t)
        alpha = 0.022 * strength * (0.4 + t)
        rounded_rect(cr, x - grow / 2, y - grow / 2 + spread * 0.35, w + grow, h + grow, radius + grow / 2)
        cr.set_source_rgba(0, 0, 0, alpha)
        cr.fill()


def compose(image_surface, preset="ocean", padding=48, radius=12, shadow=True):
    """New ARGB surface with `image_surface` framed on a background."""
    iw, ih = image_surface.get_width(), image_surface.get_height()
    W, H = iw + 2 * padding, ih + 2 * padding
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(out)
    paint_background(cr, W, H, preset)
    if shadow and padding > 0:
        soft_shadow(cr, padding, padding, iw, ih, radius)
    rounded_rect(cr, padding, padding, iw, ih, radius)
    cr.clip()
    cr.set_source_surface(image_surface, padding, padding)
    cr.paint()
    return out


def compose_file(src_png, dst_png, preset="ocean", padding=48, radius=12, shadow=True):
    surface = cairo.ImageSurface.create_from_png(src_png)
    compose(surface, preset, padding, radius, shadow).write_to_png(dst_png)
    return dst_png

