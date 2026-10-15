"""Scrolling capture: sample a screen region while the user scrolls and stitch
the frames into one tall image."""

import os

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gst, Gtk  # noqa: E402

from . import kwin, paths, screencast, system  # noqa: E402
from .stitcher import Stitcher  # noqa: E402

Gst.init(None)


class ScrollingControls(Gtk.Window):
    def __init__(self, app, session, position):
        super().__init__(application=app)
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("glint-controls")
        self.set_title(kwin.place_title("scroll", *position) if position else kwin.place_title("scroll"))
        box = Gtk.Box(spacing=8, margin_start=12, margin_end=8, margin_top=6, margin_bottom=6)
        self.label = Gtk.Label(label="Scroll slowly to capture…")
        self.label.add_css_class("numeric")
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda *_: session.finish())
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("flat")
        cancel.connect("clicked", lambda *_: session.cancel())
        for w in (self.label, done, cancel):
            box.append(w)
        self.set_child(box)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key, session)
        self.add_controller(keys)

    def _on_key(self, _c, keyval, _code, _state, session):
        from gi.repository import Gdk
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            session.cancel()
            return True
        if name in ("Return", "KP_Enter"):
            session.finish()
            return True
        return False


class ScrollingSession:
    """on_finished(path or None, error)."""

    def __init__(self, app, rect, monitors, on_finished):
        self.app = app
        self.cfg = app.config
        self.rect = rect
        self.monitors = monitors
        self.on_finished = on_finished
        self.session = screencast.ScreenCastSession(self.cfg, "scrolling.restore_token")
        self.stitcher = Stitcher()
        self.pipeline = None
        self.controls = None
        self.size = None
        self._pending = None
        self._idle = 0
        self._done = False

    def start(self):
        self.session.start(self._on_session, show_cursor=False)

    def _on_session(self, ok, err):
        if not ok:
            self._end(None, err)
            return
        stream, monitor = self.session.stream_for(self.rect, self.monitors)
        crop, self.size = screencast.crop_for(self.rect, stream, monitor)
        left, top, right, bottom = crop
        fps = int(self.cfg.get("scrolling.fps"))
        desc = (f"pipewiresrc fd={self.session.fd} path={stream.node_id} always-copy=true keepalive-time=500 "
                f"! queue leaky=downstream max-size-buffers=2 ! videoconvert "
                f"! videocrop left={left} top={top} right={right} bottom={bottom} "
                f"! videorate drop-only=true ! video/x-raw,format=RGBA,framerate={fps}/1 "
                f"! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false")
        try:
            self.pipeline = Gst.parse_launch(desc)
        except GLib.Error as exc:
            self._end(None, f"Could not start scrolling capture: {exc.message}")
            return
        self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        self.pipeline.set_state(Gst.State.PLAYING)
        self._show_controls(monitor)

    def _show_controls(self, monitor):
        x, y, w, h = self.rect
        cw, ch = 330, 46
        pos = None
        if monitor is not None:
            mx, my, mw, mh = monitor.rect
            if y + h + ch + 16 <= my + mh:
                pos = (x + (w - cw) // 2, y + h + 12)
            elif y - ch - 16 >= my:
                pos = (x + (w - cw) // 2, y - ch - 12)
            elif x + w + cw + 16 <= mx + mw:
                pos = (x + w + 12, y + 12)
            else:
                pos = (mx + mw - cw - 16, my + 16)
            pos = (max(mx, min(pos[0], mx + mw - cw)), pos[1])
        self.controls = ScrollingControls(self.app, self, pos)
        self.controls.present()

    def _on_sample(self, sink):
        # Streaming thread: keep only the most recent frame for the main loop.
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        self._pending = sample
        if not self._idle:
            self._idle = GLib.idle_add(self._process)
        return Gst.FlowReturn.OK

    def _process(self):
        self._idle = 0
        sample, self._pending = self._pending, None
        if sample is None or self._done:
            return False
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        stride = len(data) // h if h else w * 4
        row = w * 4
        rows = [data[i * stride:i * stride + row] for i in range(h)]
        self.size = (w, h)
        if self.stitcher.add(rows) and self.controls:
            self.controls.label.set_label(f"Captured {self.stitcher.height} px — keep scrolling")
        return False

    def _on_error(self, _bus, msg):
        err, _dbg = msg.parse_error()
        self._end(None, err.message)

    def _stop_pipeline(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline = None
        self.session.close()
        if self.controls:
            self.controls.destroy()
            self.controls = None

    def cancel(self):
        self._end(None, None)

    def finish(self):
        if self._done:
            return
        self._process_last()
        rows = self.stitcher.result_rows()
        if not rows or not self.size:
            self._end(None, "No frames were captured")
            return
        w = self.size[0]
        data = b"".join(rows)
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8,
                                                 w, len(rows), w * 4)
        out = os.path.join(paths.captures_cache_dir(), f"scrolling-{GLib.get_real_time()}.png")
        system.save_pixbuf(pixbuf, out)
        self._end(out, None)

    def _process_last(self):
        if self._pending is not None:
            if self._idle:
                GLib.source_remove(self._idle)
            self._process()

    def _end(self, path, err):
        if self._done:
            return
        self._done = True
        self._stop_pipeline()
        if self.on_finished:
            self.on_finished(path, err)
