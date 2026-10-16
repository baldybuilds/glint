"""JSON-backed settings with nested defaults."""

import copy
import json
import os

from . import paths

DEFAULTS = {
    "general": {
        "save_dir": "",            # empty -> paths.default_screenshot_dir()
        "video_dir": "",           # empty -> paths.default_video_dir()
        "format": "png",           # png | jpg
        "jpg_quality": 90,
        "filename_template": "Glint {date} at {time}",
        "play_sounds": True,
        "capture_cursor": False,
        "backend": "auto",         # auto | spectacle | portal | grim
        "launch_at_login": False,
        "history_limit": 30,
        "history_retention": "week",   # off | day | week | month | forever
    },
    "after_capture": {
        "show_overlay": True,
        "save": True,
        "copy": False,
        "annotate": False,
    },
    "selection": {
        "freeze_screen": True,
        "show_crosshair": True,
        "show_magnifier": True,
        "live_capture_delay_ms": 350,
    },
    "quick_access": {
        "position": "bottom-left",  # bottom-left | bottom-right | top-left | top-right
        "auto_close": False,
        "auto_close_seconds": 15,
        "close_after_drag": True,
        "close_after_save": False,
        "close_after_copy": False,
    },
    "window_capture": {
        "background": True,
        "background_preset": "ocean",
        "padding": 48,
        "radius": 12,
        "shadow": True,
    },
    "annotate": {
        "tool": "arrow",
        "color": [1.0, 0.23, 0.19, 1.0],
        "width": 4,
        "text_style": "standard",
        "counter_style": "number",
        "pixelate_intensity": 12,
        "spotlight_opacity": 0.55,
        "draw_shadow": True,
        "inverse_arrow": False,
        "line_style": "solid",      # solid | dashed | dotted | dashdot
        "arrow_head": "standard",   # standard | classic | open | double
        "magnify_zoom": 2,
    },
    "recording": {
        "format": "mp4",           # mp4 | gif
        "fps": 30,
        "quality": "standard",     # high | standard | small
        "do_not_disturb": True,
        "gif_fps": 15,
        "gif_max_width": 1280,
        "show_cursor": True,
        "record_mic": False,
        "record_system_audio": False,
        "highlight_clicks": False,
        "click_color": [1.0, 0.76, 0.03, 1.0],
        "click_size": 30,
        "click_line_width": 3.0,
        "open_trimmer": False,
        "restore_token": "",
    },
    "scrolling": {
        "fps": 8,
        "restore_token": "",
    },
    "state": {
        "last_area": None,          # [x, y, w, h] in global logical coords
        "self_timer_seconds": 5,
        "shortcuts_bound": False,
        "click_restore": None,
        "app_id": None,             # last app ID seen; drives one-time rename migrations      # KWin effect settings to put back after a crash
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, path=None):
        self.path = path or paths.config_file()
        self._listeners = []
        stored = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            print(f"glint: ignoring unreadable config {self.path}: {exc}")
        self.data = _merge(DEFAULTS, stored)

    def get(self, key, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key, value, save=True):
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if node.get(parts[-1]) == value:
            return
        node[parts[-1]] = value
        if save:
            self.save()
        for cb in list(self._listeners):
            cb(key, value)

    def connect(self, callback):
        self._listeners.append(callback)

    def save(self):
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    # Convenience accessors -------------------------------------------------

    def save_dir(self):
        return os.path.expanduser(self.get("general.save_dir") or paths.default_screenshot_dir())

    def video_dir(self):
        return os.path.expanduser(self.get("general.video_dir") or paths.default_video_dir())
