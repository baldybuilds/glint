"""Filesystem locations (XDG-aware)."""

import os

from gi.repository import GLib

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(PACKAGE_DIR), "data")


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def config_dir():
    return _ensure(os.path.join(GLib.get_user_config_dir(), "glint"))


def config_file():
    return os.path.join(config_dir(), "config.json")


def data_dir():
    return _ensure(os.path.join(GLib.get_user_data_dir(), "glint"))


def cache_dir():
    return _ensure(os.path.join(GLib.get_user_cache_dir(), "glint"))


def captures_cache_dir():
    """Where captures live when the user has not saved them anywhere yet."""
    return _ensure(os.path.join(cache_dir(), "captures"))


def scripts_cache_dir():
    return _ensure(os.path.join(cache_dir(), "kwin"))


def _special(kind, fallback):
    path = GLib.get_user_special_dir(kind)
    return path or os.path.join(GLib.get_home_dir(), fallback)


def default_screenshot_dir():
    return os.path.join(_special(GLib.UserDirectory.DIRECTORY_PICTURES, "Pictures"), "Screenshots")


def default_video_dir():
    return os.path.join(_special(GLib.UserDirectory.DIRECTORY_VIDEOS, "Videos"), "Screencasts")


def icons_dir():
    return os.path.join(DATA_DIR, "icons")


def data_file(name):
    return os.path.join(DATA_DIR, name)
