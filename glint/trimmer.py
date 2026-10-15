"""Video trimmer: pick an in/out range on a filmstrip timeline and export a
frame-accurate MP4 or GIF (optionally resized and/or muted) with ffmpeg."""

import json
import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk  # noqa: E402

from . import naming, paths, system  # noqa: E402
from .widgets import rect, rounded  # noqa: E402

MIN_LENGTH = 0.1          # seconds
HANDLE = 14               # px
STRIP_FRAMES = 14
RESOLUTIONS = [("Original", None), ("1080p", 1080), ("720p", 720), ("480p", 480)]
FORMATS = [("mp4", "MP4 video"), ("gif", "GIF")]
YELLOW = system.rgba(1.0, 0.8, 0.0, 1)


# -- pure helpers (unit-tested) ---------------------------------------------------

def format_time(seconds):
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes):02d}:{secs:04.1f}"


def adjust_range(start, end, which, t, duration, min_len=MIN_LENGTH):
    """Move the 'start' or 'end' handle to time t, keeping start < end."""
    t = max(0.0, min(duration, t))
    if which == "start":
        start = min(t, end - min_len)
        start = max(0.0, start)
    else:
        end = max(t, start + min_len)
        end = min(duration, end)
    return start, end


def pick_encoder(available):
    """Best H.264 encoder from the set of ffmpeg encoder names."""
    for name, opts in (("libx264", ["-preset", "veryfast", "-crf", "20"]),
                       ("libopenh264", ["-b:v", "8M"]),
                       ("mpeg4", ["-q:v", "3"])):
        if name in available:
            return name, opts
    return "libx264", ["-preset", "veryfast", "-crf", "20"]


def ffmpeg_args(src, dst, start, end, fmt="mp4", height=None, mute=False, gif_fps=15, gif_width=None,
                encoder=("libx264", ["-preset", "veryfast", "-crf", "20"]), has_audio=True):
    """argv for a frame-accurate trim (input seek + re-encode)."""
    duration = max(MIN_LENGTH, end - start)
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            "-ss", f"{start:.3f}", "-i", src, "-t", f"{duration:.3f}"]
    if fmt == "gif":
        if height:
            scale = f"scale=-2:{height}:flags=lanczos"
        elif gif_width:
            scale = f"scale='min({gif_width},iw)':-1:flags=lanczos"
        else:
            scale = "scale=iw:-1:flags=lanczos"
        vf = (f"fps={gif_fps},{scale},split[a][b];[a]palettegen=stats_mode=diff[p];"
              f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
        return argv + ["-vf", vf, "-loop", "0", dst]
    if height:
        argv += ["-vf", f"scale=-2:{height}:flags=lanczos"]
    name, opts = encoder
    argv += ["-c:v", name] + list(opts) + ["-pix_fmt", "yuv420p"]
    if mute or not has_audio:
        argv += ["-an"]
    else:
        argv += ["-c:a", "aac", "-b:a", "160k"]
    return argv + ["-movflags", "+faststart", dst]


def output_path(src, fmt, copy=False):
    """Where an export goes: in place for a same-format trim, otherwise a new
    file next to the source ("clip.gif", "clip (trimmed).mp4", …)."""
    directory = os.path.dirname(src)
    base, ext = os.path.splitext(os.path.basename(src))
    if not copy and ext.lower() == "." + fmt:
        return src
    name = f"{base} (trimmed)" if copy or ext.lower() == "." + fmt else base
    return naming.unique_path(directory, name, fmt)


def probe(path):
    """{duration, width, height, has_audio, fps} via ffprobe (None if unavailable)."""
    if not shutil.which("ffprobe"):
        return None
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True, check=False)
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        return None
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    num, _, den = (video.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    except ValueError:
        fps = 30.0
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
        "fps": fps or 30.0,
    }


_encoders_cache = None


def ffmpeg_encoders():
    global _encoders_cache
    if _encoders_cache is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                             check=False).stdout if shutil.which("ffmpeg") else ""
        _encoders_cache = {line.split()[1] for line in out.splitlines()
                           if len(line.split()) > 1 and line.startswith(" V")}
    return _encoders_cache


# -- timeline widget -----------------------------------------------------------------

class Timeline(Gtk.Widget):
    """Filmstrip with draggable in/out handles and a playhead."""

    def __init__(self, duration, on_range, on_seek):
        super().__init__()
        self.duration = max(duration, MIN_LENGTH)
        self.start = 0.0
        self.end = self.duration
        self.position = 0.0
        self.strip = None
        self.on_range = on_range
        self.on_seek = on_seek
        self._drag = None
        self.set_hexpand(True)
        self.set_cursor_from_name("pointer")

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_: setattr(self, "_drag", None))
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._hover)
        self.add_controller(motion)

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 300, 900, -1, -1
        return 64, 64, -1, -1

    # geometry
    def _track(self):
        return HANDLE, max(HANDLE + 1, self.get_width() - HANDLE)

    def x_for(self, t):
        x0, x1 = self._track()
        return x0 + (x1 - x0) * (t / self.duration)

    def t_for(self, x):
        x0, x1 = self._track()
        return max(0.0, min(self.duration, (x - x0) / max(1, x1 - x0) * self.duration))

    def _hit(self, x):
        xs, xe = self.x_for(self.start), self.x_for(self.end)
        if xs - HANDLE - 4 <= x <= xs + 4:
            return "start"
        if xe - 4 <= x <= xe + HANDLE + 4:
            return "end"
        return "seek"

    def _hover(self, _c, x, _y):
        self.set_cursor_from_name("ew-resize" if self._hit(x) in ("start", "end") else "pointer")

    def _begin(self, gesture, x, _y):
        self._drag = (self._hit(x), x)
        self._apply(x)

    def _update(self, gesture, dx, _dy):
        if self._drag:
            self._apply(self._drag[1] + dx)

    def _apply(self, x):
        which = self._drag[0]
        t = self.t_for(x)
        if which == "seek":
            t = max(self.start, min(self.end, t))
            self.position = t
            self.on_seek(t)
        else:
            self.start, self.end = adjust_range(self.start, self.end, which, t, self.duration)
            self.position = self.start if which == "start" else self.end
            self.on_range(self.start, self.end)
            self.on_seek(self.position)
        self.queue_draw()

    def set_position(self, t):
        self.position = t
        self.queue_draw()

    def do_snapshot(self, s):
        h = self.get_height()
        x0, x1 = self._track()
        xs, xe = self.x_for(self.start), self.x_for(self.end)
        s.push_rounded_clip(rounded(x0, 0, x1 - x0, h, 6))
        s.append_color(system.rgba(0.15, 0.15, 0.17, 1), rect(x0, 0, x1 - x0, h))
        if self.strip is not None:
            s.append_scaled_texture(self.strip, Gsk.ScalingFilter.LINEAR, rect(x0, 0, x1 - x0, h))
        dim = system.rgba(0, 0, 0, 0.62)
        s.append_color(dim, rect(x0, 0, max(0, xs - x0), h))
        s.append_color(dim, rect(xe, 0, max(0, x1 - xe), h))
        s.pop()
        # Selection frame + handles.
        s.append_color(YELLOW, rect(xs, 0, xe - xs, 3))
        s.append_color(YELLOW, rect(xs, h - 3, xe - xs, 3))
        grip = system.rgba(0.35, 0.27, 0, 1)
        for hx in (xs - HANDLE, xe):
            s.push_rounded_clip(rounded(hx, 0, HANDLE, h, 5))
            s.append_color(YELLOW, rect(hx, 0, HANDLE, h))
            s.pop()
            s.append_color(grip, rect(hx + HANDLE / 2 - 2, h / 2 - 9, 1.5, 18))
            s.append_color(grip, rect(hx + HANDLE / 2 + 1, h / 2 - 9, 1.5, 18))
        xp = self.x_for(self.position)
        s.append_color(system.rgba(0, 0, 0, 0.5), rect(xp - 2, -2, 4, h + 4))
        s.append_color(system.rgba(1, 1, 1, 1), rect(xp - 1, -2, 2, h + 4))


# -- window ----------------------------------------------------------------------------

class TrimWindow(Adw.ApplicationWindow):
    def __init__(self, app, path, on_saved=None):
        super().__init__(application=app)
        self.app = app
        self.cfg = app.config
        self.path = path
        self.on_saved = on_saved
        self.info = probe(path) or {"duration": 0, "width": 0, "height": 0, "has_audio": False, "fps": 30}
        self.exporting = None
        self.set_title(f"Trim — {os.path.basename(path)}")
        self.set_default_size(1100, 760)

        self.media = Gtk.MediaFile.new_for_filename(path)
        self.media.connect("notify::prepared", self._on_prepared)
        self.media.connect("notify::playing", lambda *_: self._sync_play_button())

        duration = self.info["duration"] or 1.0
        self.timeline = Timeline(duration, self._on_range, self._on_seek)
        self._build_ui()
        self._load_filmstrip()
        self._tick_id = GLib.timeout_add(33, self._tick)
        self.connect("close-request", self._on_close)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- UI --------------------------------------------------------------------------

    def _build_ui(self):
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.set_tooltip_text("Trim and save (Ctrl+S)")
        save.connect("clicked", lambda *_: self.export(copy=False))
        self.save_btn = save
        header.pack_end(save)
        menu = Gio.Menu()
        menu.append("Save as Copy", "win.save-copy")
        menu.append("Show in Folder", "win.reveal")
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu))
        for name, cb in (("save-copy", lambda: self.export(copy=True)),
                         ("reveal", lambda: system.reveal_in_folder(self.path))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, f=cb: f())
            self.add_action(action)
        tv.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                       margin_start=16, margin_end=16)
        self.picture = Gtk.Picture(paintable=self.media, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   vexpand=True)
        self.picture.add_css_class("glint-trim-video")
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.toggle_play())
        self.picture.add_controller(click)
        body.append(self.picture)

        row = Gtk.Box(spacing=12)
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER)
        self.play_btn.add_css_class("circular")
        self.play_btn.set_tooltip_text("Play / pause (Space)")
        self.play_btn.connect("clicked", lambda *_: self.toggle_play())
        row.append(self.play_btn)
        row.append(self.timeline)
        body.append(row)

        info = Gtk.Box(spacing=12)
        self.time_label = Gtk.Label(xalign=0, css_classes=["numeric", "dim-label"])
        self.range_label = Gtk.Label(xalign=1, hexpand=True, css_classes=["numeric"])
        info.append(self.time_label)
        info.append(self.range_label)
        body.append(info)

        options = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        src_fmt = "gif" if self.path.lower().endswith(".gif") else "mp4"
        self.format_drop = Gtk.DropDown.new_from_strings([label for _f, label in FORMATS])
        self.format_drop.set_selected([f for f, _l in FORMATS].index(src_fmt))
        self.format_drop.connect("notify::selected", lambda *_: self._update_labels())
        options.append(self._labeled("Format", self.format_drop))
        heights = [(label, h) for label, h in RESOLUTIONS if h is None or h < (self.info["height"] or 99999)]
        self.resolutions = heights
        self.res_drop = Gtk.DropDown.new_from_strings([label for label, _h in heights])
        options.append(self._labeled("Size", self.res_drop))
        self.mute_check = Gtk.CheckButton(label="Remove audio", sensitive=self.info["has_audio"])
        self.mute_check.connect("toggled", lambda b: self.media.set_muted(b.get_active()))
        options.append(self.mute_check)
        body.append(options)

        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        body.append(self.progress)

        tv.set_content(body)
        self.toasts = Adw.ToastOverlay(child=tv)
        self.set_content(self.toasts)
        self._update_labels()

    def _labeled(self, text, widget):
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Label(label=text, css_classes=["dim-label"]))
        box.append(widget)
        return box

    def _load_filmstrip(self):
        duration = self.info["duration"]
        if not duration or not system.have("ffmpeg"):
            return
        out = os.path.join(paths.cache_dir(), f"filmstrip-{GLib.get_real_time()}.png")
        fps = STRIP_FRAMES / duration
        argv = ["ffmpeg", "-y", "-loglevel", "error", "-i", self.path, "-vf",
                f"fps={fps:.5f},scale=-2:64,tile={STRIP_FRAMES}x1", "-frames:v", "1", out]

        def done(ok, _o, _e):
            if ok and os.path.exists(out):
                self.timeline.strip = Gdk.Texture.new_from_filename(out)
                self.timeline.queue_draw()
                os.remove(out)

        system.run_capture(argv, done)

    # -- playback --------------------------------------------------------------------

    def _on_prepared(self, media, _pspec):
        if media.is_prepared() and not self.info["duration"]:
            self.info["duration"] = media.get_duration() / 1e6
            self.timeline.duration = self.timeline.end = max(self.info["duration"], MIN_LENGTH)
            self._update_labels()

    def toggle_play(self):
        if self.media.get_playing():
            self.media.pause()
            return
        pos = self.media.get_timestamp() / 1e6
        if pos < self.timeline.start or pos >= self.timeline.end - 0.05:
            self.media.seek(int(self.timeline.start * 1e6))
        self.media.play()

    def _sync_play_button(self):
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if self.media.get_playing()
                                    else "media-playback-start-symbolic")

    def _tick(self):
        pos = self.media.get_timestamp() / 1e6
        if self.media.get_playing() and pos >= self.timeline.end:
            # Loop the selection while previewing.
            self.media.seek(int(self.timeline.start * 1e6))
            pos = self.timeline.start
        if self.timeline._drag is None:
            self.timeline.set_position(pos)
        self.time_label.set_label(f"{format_time(pos)} / {format_time(self.info['duration'])}")
        return True

    def _on_seek(self, t):
        self.media.seek(int(t * 1e6))

    def _on_range(self, start, end):
        self._update_labels()

    def _update_labels(self):
        t = self.timeline
        self.range_label.set_label(f"Selection {format_time(t.start)} – {format_time(t.end)}"
                                   f"  ({t.end - t.start:.1f}s)")

    def _on_key(self, _c, keyval, _code, state):
        name = Gdk.keyval_name(keyval)
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        frame = 1 / max(1.0, self.info["fps"])
        pos = self.media.get_timestamp() / 1e6
        if name == "space":
            self.toggle_play()
        elif ctrl and name in ("s", "S"):
            self.export(copy=False)
        elif name in ("i", "I"):
            self.timeline.start, self.timeline.end = adjust_range(self.timeline.start, self.timeline.end, "start",
                                                                  pos, self.timeline.duration)
            self._update_labels()
            self.timeline.queue_draw()
        elif name in ("o", "O"):
            self.timeline.start, self.timeline.end = adjust_range(self.timeline.start, self.timeline.end, "end",
                                                                  pos, self.timeline.duration)
            self._update_labels()
            self.timeline.queue_draw()
        elif name in ("Left", "Right"):
            self.media.pause()
            step = frame * (10 if state & Gdk.ModifierType.SHIFT_MASK else 1)
            self._on_seek(max(0.0, min(self.info["duration"], pos + (step if name == "Right" else -step))))
        elif name == "Home":
            self._on_seek(self.timeline.start)
        elif name == "End":
            self._on_seek(self.timeline.end)
        elif name == "Escape":
            self.close()
        else:
            return False
        return True

    # -- export ----------------------------------------------------------------------

    def export(self, copy=False):
        if self.exporting is not None:
            return
        if not system.have("ffmpeg"):
            self.toasts.add_toast(Adw.Toast(title="ffmpeg is required to trim videos"))
            return
        self.media.pause()
        fmt = FORMATS[self.format_drop.get_selected()][0]
        height = self.resolutions[self.res_drop.get_selected()][1]
        dest = output_path(self.path, fmt, copy=copy)
        in_place = dest == self.path
        tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.glint-tmp.{fmt}") if in_place else dest
        argv = ffmpeg_args(self.path, tmp, self.timeline.start, self.timeline.end, fmt=fmt, height=height,
                           mute=self.mute_check.get_active(), gif_fps=int(self.cfg.get("recording.gif_fps")),
                           gif_width=None if height else int(self.cfg.get("recording.gif_max_width")),
                           encoder=pick_encoder(ffmpeg_encoders()), has_audio=self.info["has_audio"])
        self._run(argv, tmp, dest, in_place)

    def _run(self, argv, tmp, dest, in_place):
        duration = max(MIN_LENGTH, self.timeline.end - self.timeline.start)
        errlog = os.path.join(paths.cache_dir(), f"ffmpeg-{GLib.get_real_time()}.log")
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE)
        launcher.set_stderr_file_path(errlog)
        try:
            proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self.toasts.add_toast(Adw.Toast(title=f"Could not start ffmpeg: {exc.message}"))
            return
        self.exporting = proc
        self.save_btn.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.progress.set_text("Exporting…")
        stream = Gio.DataInputStream.new(proc.get_stdout_pipe())

        def read_line(s, res):
            try:
                line, _len = s.read_line_finish_utf8(res)
            except GLib.Error:
                line = None
            if line is None:
                return
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    done = int(line.split("=", 1)[1]) / 1e6
                    self.progress.set_fraction(max(0.0, min(1.0, done / duration)))
                    self.progress.set_text(f"Exporting… {int(100 * min(1.0, done / duration))}%")
                except ValueError:
                    pass
            s.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

        def finished(p, res):
            try:
                ok = p.wait_check_finish(res)
            except GLib.Error:
                ok = False
            self.exporting = None
            self.save_btn.set_sensitive(True)
            self.progress.set_visible(False)
            err = ""
            try:
                with open(errlog, encoding="utf-8", errors="replace") as fh:
                    err = fh.read().strip()
                os.remove(errlog)
            except OSError:
                pass
            if not ok or not os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                self.toasts.add_toast(Adw.Toast(title="Export failed", timeout=5))
                print(f"glint: trim failed: {err[:500]}")
                return
            if in_place:
                os.replace(tmp, dest)
            if self.on_saved:
                self.on_saved(dest)
            system.notify(self.app, "Video saved", os.path.basename(dest), "trim")
            self.destroy()

        proc.wait_check_async(None, finished)

    def _on_close(self, _win):
        if self.exporting is not None:
            self.exporting.force_exit()
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self.media.pause()
        return False
