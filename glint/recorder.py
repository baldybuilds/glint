"""Screen recording (MP4 / GIF) from a ScreenCast stream via GStreamer."""

import os
import shutil
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

from . import debug, kwin, naming, paths, screencast, system  # noqa: E402
from .clicks import ClickHighlighter  # noqa: E402

Gst.init(None)


def _have_element(name):
    return Gst.ElementFactory.find(name) is not None


def video_encoder(fps):
    if _have_element("x264enc"):
        return (f"x264enc tune=zerolatency speed-preset=veryfast pass=quant quantizer=21 bframes=0 "
                f"key-int-max={fps * 2}")
    if _have_element("openh264enc"):
        return "openh264enc complexity=high rate-control=quality"
    raise RuntimeError("No H.264 encoder found (install gstreamer1-plugins-ugly or openh264)")


def audio_encoder():
    for name, desc in (("fdkaacenc", "fdkaacenc bitrate=160000"), ("avenc_aac", "avenc_aac bitrate=160000"),
                       ("voaacenc", "voaacenc bitrate=160000")):
        if _have_element(name):
            return desc
    return None


def build_pipeline(fd, node_id, fps, out_path, mic=False, system_audio=False):
    # The crop is configured once the stream's real size is known; see
    # screencast.apply_crop_when_ready().
    video = (f"pipewiresrc name=src fd={fd} path={node_id} do-timestamp=true always-copy=true keepalive-time=1000 "
             f"! queue ! videoconvert ! videocrop name=crop "
             f"! videorate ! video/x-raw,framerate={fps}/1 ! videoconvert ! video/x-raw,format=I420 "
             f"! {video_encoder(fps)} ! h264parse ! queue ! mp4mux name=mux ! filesink location=\"{out_path}\"")
    sources = []
    if system_audio:
        sources.append("pulsesrc device=@DEFAULT_MONITOR@ do-timestamp=true")
    if mic:
        sources.append("pulsesrc do-timestamp=true")
    aenc = audio_encoder() if sources else None
    if not aenc:
        return video
    caps = "audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2"
    if len(sources) == 1:
        audio = f" {sources[0]} ! queue ! {caps} ! {aenc} ! aacparse ! queue ! mux."
    else:
        audio = (f" audiomixer name=amix ! audioconvert ! {aenc} ! aacparse ! queue ! mux."
                 + "".join(f" {s} ! queue ! {caps} ! amix." for s in sources))
    return video + audio


class RecordingControls(Gtk.Window):
    def __init__(self, app, recorder, position):
        super().__init__(application=app)
        self.recorder = recorder
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("glint-controls")
        self.set_title(kwin.place_title("rec", *position) if position else kwin.place_title("rec"))
        box = Gtk.Box(spacing=8, margin_start=12, margin_end=8, margin_top=6, margin_bottom=6)
        dot = Gtk.Label(label="●")
        dot.add_css_class("glint-rec-dot")
        self.time_label = Gtk.Label(label="00:00")
        self.time_label.add_css_class("numeric")
        stop = Gtk.Button(label="Stop")
        stop.add_css_class("destructive-action")
        stop.connect("clicked", lambda *_: recorder.stop())
        discard = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Discard recording")
        discard.add_css_class("flat")
        discard.connect("clicked", lambda *_: recorder.cancel())
        for w in (dot, self.time_label, stop, discard):
            box.append(w)
        self.set_child(box)

    def tick(self, seconds):
        self.time_label.set_label(f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}")


class Recorder:
    """One recording. on_finished(path or None, error)."""

    def __init__(self, app, rect, monitors, fmt="mp4", on_finished=None, session=None):
        self.app = app
        self.cfg = app.config
        self.rect = rect
        self.monitors = monitors
        self.fmt = fmt
        self.on_finished = on_finished
        # Normally handed an already-open session (see GlintApp._with_screen_share).
        self.session = session or screencast.ScreenCastSession(self.cfg, screencast.TOKEN_KEY)
        self.pipeline = None
        self.controls = None
        self.started_at = None
        self._tick_id = 0
        self._stopping = False
        self._cancelled = False
        self.clicks = ClickHighlighter(self.cfg)
        self.tmp_path = os.path.join(paths.cache_dir(), f"recording-{GLib.get_real_time()}.mp4")

    @property
    def active(self):
        return self.pipeline is not None and not self._stopping

    def start(self):
        if self.session.fd is not None:
            self._on_session(True, None)
        else:
            self.session.start(self._on_session, show_cursor=self.cfg.get("recording.show_cursor"))

    def _on_session(self, ok, err):
        if not ok:
            self._finish(None, err)
            return
        stream, monitor = self.session.stream_for(self.rect, self.monitors)
        base = stream.rect or (monitor.rect if monitor else None)
        gif = self.fmt == "gif"
        fps = int(self.cfg.get("recording.gif_fps" if gif else "recording.fps"))
        try:
            desc = build_pipeline(self.session.fd, stream.node_id, fps, self.tmp_path,
                                  mic=self.cfg.get("recording.record_mic") and not gif,
                                  system_audio=self.cfg.get("recording.record_system_audio") and not gif)
            self.pipeline = Gst.parse_launch(desc)
        except (GLib.Error, RuntimeError) as exc:
            self._finish(None, f"Could not build recording pipeline: {exc}")
            return
        screencast.apply_crop_when_ready(self.pipeline.get_by_name("src"), self.pipeline.get_by_name("crop"),
                                         self.rect, base, self._on_crop_plan)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._finish(None, "Recording pipeline failed to start")
            return
        self.started_at = time.monotonic()
        if self.cfg.get("recording.highlight_clicks") and not self.clicks.enable():
            print("glint: click highlighting unavailable (needs KWin's Mouse Click Animation effect)")
        self._show_controls(monitor)
        self._tick_id = GLib.timeout_add(500, self._tick)
        self.app.on_recording_state(True)

    def _on_crop_plan(self, plan):
        debug("recording crop", plan)
        if plan.whole:
            warn_unmatched_source(self.app, screencast.TOKEN_KEY)

    def _show_controls(self, monitor):
        if monitor is None:
            return
        x, y, w, h = self.rect
        mx, my, mw, mh = monitor.rect
        cw, ch = 230, 46
        covers = w >= mw - 4 and h >= mh - 4
        if covers:
            others = [m for m in self.monitors if m is not monitor]
            if not others:
                system.notify(self.app, "Recording started",
                              "Stop it from the tray icon or run the record command again.", "recording")
                return
            o = others[0]
            pos = (o.x + (o.width - cw) // 2, o.y + o.height - ch - 60)
        elif y + h + ch + 16 <= my + mh:
            pos = (x + (w - cw) // 2, y + h + 12)
        elif y - ch - 16 >= my:
            pos = (x + (w - cw) // 2, y - ch - 12)
        else:
            pos = (mx + (mw - cw) // 2, my + 16)
        pos = (max(mx, min(pos[0], mx + mw - cw)), pos[1])
        self.controls = RecordingControls(self.app, self, pos)
        self.controls.present()

    def _tick(self):
        if self.controls and self.started_at:
            self.controls.tick(time.monotonic() - self.started_at)
        return self.pipeline is not None and not self._stopping

    def elapsed(self):
        return time.monotonic() - self.started_at if self.started_at else 0

    def stop(self):
        if self.pipeline is None or self._stopping:
            return
        self._stopping = True
        if self.controls:
            self.controls.destroy()
            self.controls = None
        self.pipeline.send_event(Gst.Event.new_eos())
        GLib.timeout_add_seconds(8, self._eos_timeout)

    def cancel(self):
        self._cancelled = True
        if self.pipeline is None:
            self._finish(None, None)
            return
        self.stop()

    def _eos_timeout(self):
        if self.pipeline is not None:
            self._teardown()
            self._post_process()
        return False

    def _on_message(self, _bus, msg):
        if msg.type == Gst.MessageType.EOS:
            self._teardown()
            self._post_process()
        elif msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"glint: recording error: {err.message} ({debug})")
            self._teardown()
            self._finish(None, err.message)

    def _teardown(self):
        self.clicks.disable()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline = None
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self.session.close()

    def _post_process(self):
        if self._cancelled or not os.path.exists(self.tmp_path) or os.path.getsize(self.tmp_path) == 0:
            self._cleanup_tmp()
            self._finish(None, None if self._cancelled else "Recording produced no video")
            return
        out_dir = self.cfg.video_dir()
        os.makedirs(out_dir, exist_ok=True)
        base = naming.render(self.cfg.get("general.filename_template"), kind="Recording")
        if self.fmt == "gif":
            dest = naming.unique_path(out_dir, base, "gif")
            self._make_gif(self.tmp_path, dest)
        else:
            dest = naming.unique_path(out_dir, base, "mp4")
            shutil.move(self.tmp_path, dest)
            self._finish(dest, None)

    def _make_gif(self, src, dest):
        if not system.have("ffmpeg"):
            self._finish(None, "ffmpeg is required to create GIFs")
            return
        fps = int(self.cfg.get("recording.gif_fps"))
        width = int(self.cfg.get("recording.gif_max_width"))
        vf = (f"fps={fps},scale='min({width},iw)':-1:flags=lanczos,split[a][b];"
              f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
        argv = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", vf, "-loop", "0", dest]

        def done(ok, _out, err):
            self._cleanup_tmp()
            if not ok:
                self._finish(None, f"GIF conversion failed: {err.strip()[:200]}")
                return
            if system.have("gifsicle"):
                system.run_capture(["gifsicle", "-O3", "--batch", dest], lambda *_: self._finish(dest, None))
            else:
                self._finish(dest, None)

        system.run_capture(argv, done)

    def _cleanup_tmp(self):
        try:
            os.remove(self.tmp_path)
        except OSError:
            pass

    def _finish(self, path, err):
        self.clicks.disable()
        if self.controls:
            self.controls.destroy()
            self.controls = None
        self.session.close()
        self.app.on_recording_state(False)
        if self.on_finished:
            self.on_finished(path, err)
            self.on_finished = None


def warn_unmatched_source(app, token_key):
    """The shared source wasn't a full screen we could crop (e.g. a region or
    window picked in the portal dialog). We use all of it, forget the saved
    choice so the picker appears next time, and say why."""
    app.config.set(token_key, "")
    system.notify(app, "Using the area you shared",
                  "It didn't match a full screen, so Glint used all of it instead of your selection. "
                  "Next time, choose a whole screen in the sharing dialog.", "shared-source")


def poster_frame(video_path, callback):
    """callback(png path or None): first frame of a video/GIF for thumbnails."""
    out = os.path.join(paths.captures_cache_dir(), f"poster-{GLib.get_real_time()}.png")
    if video_path.lower().endswith(".gif"):
        try:
            system.save_pixbuf(system.load_pixbuf(video_path), out)
            callback(out)
            return
        except GLib.Error:
            pass
    if not system.have("ffmpeg"):
        callback(None)
        return
    system.run_capture(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-frames:v", "1", out],
                       lambda ok, _o, _e: callback(out if ok and os.path.exists(out) else None))
