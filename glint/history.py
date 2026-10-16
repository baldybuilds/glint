"""Capture history: a persistent, retention-limited list of recent captures.

Entries live in ~/.local/share/glint/history/index.json, with thumbnails in
thumbs/. A capture that was saved to the user's folders is only *referenced*
and never deleted by history. A capture that exists only in Glint's temporary
cache is *adopted*: moved into history/files/ so it survives cache clean-ups,
and deleted again when it expires ("owned" entries).
"""

import datetime
import json
import os
import shutil
import time
import uuid

from . import paths

# Setting value -> days to keep (None: forever, 0: history off)
RETENTION = {"off": 0, "day": 1, "week": 7, "month": 30, "forever": None}
RETENTION_LABELS = [("off", "Off"), ("day", "1 day"), ("week", "1 week"), ("month", "1 month"),
                    ("forever", "Forever")]
VIDEO_KINDS = ("video", "gif")
THUMB_SIZE = 480


def default_thumbnailer(src, dest):
    """Write a PNG thumbnail of image `src` to `dest`. Returns True on success."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, THUMB_SIZE, THUMB_SIZE, True)
        pb.savev(dest, "png", [], [])
        return True
    except GLib.Error:
        return False


def day_label(ts, now=None):
    """"Today", "Yesterday", a weekday within the last week, else a date."""
    now = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    day = datetime.datetime.fromtimestamp(ts).date()
    delta = (now.date() - day).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return day.strftime("%A")
    return f"{day.day} {day.strftime('%B %Y')}"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class History:
    def __init__(self, root=None, retention="week", clock=time.time, thumbnailer=default_thumbnailer):
        self.root = root or os.path.join(paths.data_dir(), "history")
        self.files_dir = os.path.join(self.root, "files")
        self.thumbs_dir = os.path.join(self.root, "thumbs")
        for d in (self.root, self.files_dir, self.thumbs_dir):
            os.makedirs(d, exist_ok=True)
        self.index = os.path.join(self.root, "index.json")
        self.retention = retention
        self.clock = clock
        self.thumbnailer = thumbnailer
        self._listeners = []
        self.entries = self._load()
        if self.prune():
            self._save()

    # -- persistence -----------------------------------------------------------

    def _load(self):
        try:
            with open(self.index, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = [e for e in data.get("entries", []) if isinstance(e, dict) and "path" in e and "id" in e]
        except (OSError, ValueError, AttributeError):
            entries = []
        return sorted(entries, key=lambda e: e.get("created", 0), reverse=True)

    def _save(self):
        tmp = self.index + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "entries": self.entries}, fh, indent=1)
        os.replace(tmp, self.index)

    def connect(self, callback):
        self._listeners.append(callback)

    def disconnect(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _changed(self):
        self._save()
        for cb in list(self._listeners):
            cb()

    # -- queries ---------------------------------------------------------------

    @property
    def enabled(self):
        return RETENTION.get(self.retention, 7) != 0

    def find(self, entry_id):
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def find_path(self, path):
        return next((e for e in self.entries if e["path"] == path), None)

    def thumb_path(self, entry):
        return os.path.join(self.thumbs_dir, f"{entry['id']}.png")

    def owns(self, path):
        return os.path.abspath(path).startswith(os.path.abspath(self.files_dir) + os.sep)

    def disk_usage(self):
        total = 0
        for d in (self.files_dir, self.thumbs_dir):
            for name in os.listdir(d):
                try:
                    total += os.path.getsize(os.path.join(d, name))
                except OSError:
                    pass
        return total

    def fresh_thumb(self, entry):
        """Thumbnail path, regenerated if the capture changed since (e.g. it
        was annotated). Videos keep the poster frame they were added with."""
        thumb = self.thumb_path(entry)
        if entry.get("kind") in VIDEO_KINDS:
            return thumb if os.path.exists(thumb) else None
        try:
            stale = not os.path.exists(thumb) or os.path.getmtime(entry["path"]) > os.path.getmtime(thumb)
        except OSError:
            return thumb if os.path.exists(thumb) else None
        if stale and not self.thumbnailer(entry["path"], thumb):
            return None
        return thumb

    # -- changes ---------------------------------------------------------------

    def add(self, path, kind="screenshot", adopt=False, width=None, height=None, duration=None, poster=None):
        """Record a capture. With adopt=True the file is moved into history's
        own storage (use for captures that only exist in the temp cache).
        Returns the entry (whose "path" may have changed), or None if off."""
        if not self.enabled or not os.path.exists(path):
            return None
        entry_id = uuid.uuid4().hex[:12]
        if adopt and not self.owns(path):
            dest = os.path.join(self.files_dir, entry_id + os.path.splitext(path)[1].lower())
            shutil.move(path, dest)
            path = dest
        entry = {"id": entry_id, "path": path, "kind": kind, "created": self.clock(),
                 "width": width, "height": height, "duration": duration}
        self.thumbnailer(poster or path, self.thumb_path(entry))
        self.entries.insert(0, entry)
        self.prune()
        self._changed()
        return entry

    def touch(self, entry):
        """The capture's file changed (annotated, trimmed): tell listeners."""
        if entry in self.entries:
            self._changed()

    def relink(self, old_path, new_path):
        """The capture was saved elsewhere (e.g. "Save" in Quick Access): point
        the entry at the saved copy and drop history's own copy."""
        entry = self.find_path(old_path)
        if entry is None or old_path == new_path:
            return entry
        if self.owns(old_path):
            _remove(old_path)
        entry["path"] = new_path
        self._changed()
        return entry

    def remove(self, entry_id, delete_file=False):
        """Forget an entry. History's own copies are always deleted; files in
        the user's folders only when delete_file=True (moved to the Trash)."""
        entry = self.find(entry_id)
        if entry is None:
            return False
        self._drop(entry, trash_user_file=delete_file)
        self._changed()
        return True

    def clear(self):
        for entry in list(self.entries):
            self._drop(entry, trash_user_file=False)
        self._changed()

    def set_retention(self, retention):
        self.retention = retention
        if not self.enabled:
            self.clear()
        elif self.prune():
            self._changed()

    def prune(self):
        """Drop expired entries and entries whose file is gone. Returns True if
        anything changed (the caller saves)."""
        days = RETENTION.get(self.retention, 7)
        cutoff = None if days is None else self.clock() - days * 86400
        changed = False
        for entry in list(self.entries):
            expired = cutoff is not None and entry.get("created", 0) < cutoff
            if expired or not os.path.exists(entry["path"]):
                self._drop(entry, trash_user_file=False)
                changed = True
        return changed

    def _drop(self, entry, trash_user_file):
        if entry in self.entries:
            self.entries.remove(entry)
        _remove(self.thumb_path(entry))
        if self.owns(entry["path"]):
            _remove(entry["path"])
        elif trash_user_file:
            _trash(entry["path"])


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _trash(path):
    from gi.repository import Gio, GLib
    try:
        Gio.File.new_for_path(path).trash(None)
    except GLib.Error:
        pass
