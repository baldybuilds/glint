"""Glint application: command dispatch and capture workflows."""

import os
import sys
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, LEGACY_APP_ID, URL_SCHEME, VERSION, capture, kwin, naming, paths, system  # noqa: E402
from .config import Config  # noqa: E402
from .overlay import OverlayController  # noqa: E402
from .quickaccess import CaptureItem, QuickAccessManager  # noqa: E402

COMMANDS = {
    "capture-area": "Select an area to capture",
    "capture-previous-area": "Capture the last selected area again",
    "capture-fullscreen": "Capture the display under the pointer",
    "capture-window": "Pick a window to capture",
    "self-timer": "Select an area, then capture after a delay (seconds=N)",
    "scrolling-capture": "Capture a scrolling area into one tall image",
    "capture-text": "Select an area and copy its text (OCR)",
    "record-screen": "Record an area as video or GIF (format=mp4|gif); run again to stop",
    "stop-recording": "Stop the current recording",
    "open-annotate": "Open an image in the annotate tool (filepath=…)",
    "trim-video": "Trim a recording, or convert it to GIF (filepath=…)",
    "open-from-clipboard": "Annotate the image on the clipboard",
    "pin": "Pin an image to the screen (filepath=…)",
    "restore-recently-closed": "Re-open the last closed Quick Access item",
    "history": "Show capture history",
    "open-folder": "Open the screenshots folder",
    "set-default": "Make Glint the desktop's screenshot tool (takes over Print Screen)",
    "unset-default": "Give the screenshot shortcuts back to the desktop's own tool",
    "preferences": "Show preferences",
    "about": "About Glint",
    "quit": "Quit the background service",
}

HELP = f"""Usage: glint [COMMAND] [ARGS]
       glint {URL_SCHEME}://COMMAND?key=value

Runs in the background with a tray icon. Commands are sent to the running
instance (it starts if needed). Commands:
""" + "\n".join(f"  {name:<26}{desc}" for name, desc in COMMANDS.items()) + """

Options:
  --background   start without doing anything (for autostart)
  --version      print the version

Log file (attach it to bug reports): ~/.cache/glint/glint.log
"""


def parse_command(argv):
    """['capture-area'] / ['glint://pin?filepath=/a.png'] / ['pin', '/a.png'] -> (cmd, params)."""
    if not argv:
        return None, {}
    head, rest = argv[0], argv[1:]
    params = {}
    if "://" in head:
        url = urllib.parse.urlparse(head)
        head = (url.netloc or url.path.lstrip("/")).strip("/")
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
    elif "?" in head:
        head, query = head.split("?", 1)
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
    for arg in rest:
        if "=" in arg and not arg.startswith("/"):
            k, v = arg.split("=", 1)
            params[k.lstrip("-")] = v
        else:
            params.setdefault("filepath", arg)
    return head, params


class CountdownWindow(Gtk.Window):
    def __init__(self, app, seconds, rect, on_done):
        super().__init__(application=app)
        self.remaining = seconds
        self.on_done = on_done
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("glint-timer")
        self.label = Gtk.Label(label=str(seconds))
        self.label.add_css_class("glint-countdown")
        box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.set_size_request(150, 150)
        box.append(self.label)
        self.label.set_hexpand(True)
        self.set_child(box)
        x, y, w, h = rect
        self.set_title(kwin.place_title("timer", x + (w - 150) // 2, y + (h - 150) // 2))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._timer = GLib.timeout_add_seconds(1, self._tick)

    def _on_key(self, _c, keyval, _code, _state):
        if Gdk.keyval_name(keyval) == "Escape":
            GLib.source_remove(self._timer)
            self.destroy()
            self.on_done(False)
            return True
        return False

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self.destroy()
            self.on_done(True)
            return False
        self.label.set_label(str(self.remaining))
        return True


class GlintApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        self.config = None
        self.busy = False
        self.recorder = None
        self.scrolling = None
        self.shortcuts = None
        self.tray = None
        self.kwin = None
        self.qa = None
        self.kde_shortcuts = False
        self._sharing = False
        self._held = False

    # -- lifecycle -------------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        from . import log
        log.start()   # only the long-running primary instance gets here
        self.config = Config()
        self._migrate_app_id()
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(paths.icons_dir())
        css = Gtk.CssProvider()
        css.load_from_path(paths.data_file("style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name(APP_ID)

        conn = self.get_dbus_connection()
        self.kwin = kwin.KWinBridge(conn)
        self.kwin.install_rules()
        self.kwin.query_windows(lambda *_: None)  # warm the work-area cache
        if self.kwin.enabled:
            from .clicks import ClickHighlighter
            # Put KWin's click effect back if a previous run died mid-recording.
            ClickHighlighter(self.config).restore_pending()
        self.qa = QuickAccessManager(self)
        from .history import History
        self.history = History(retention=self.config.get("general.history_retention"))
        self.history_window = None
        try:
            from .tray import Tray
            self.tray = Tray(conn, self.run_command)
        except GLib.Error as exc:
            print(f"glint: tray unavailable: {exc.message}")
        # Lets `glint <command>` (and KDE shortcuts) reach us via a cheap
        # gdbus call instead of starting Python; see bin/glint.
        run = Gio.SimpleAction.new("run-command", GLib.VariantType.new("s"))
        run.connect("activate", lambda _a, param: self.run_command(param.get_string()))
        self.add_action(run)
        self._setup_shortcuts()
        self.hold()
        self._held = True
        if not os.environ.get("GLINT_NO_SHORTCUTS"):
            GLib.timeout_add_seconds(2, self._offer_default_tool)

    def _migrate_app_id(self):
        """One-time clean-up after the 0.3.0 app ID rename (shortcuts are
        migrated separately, once KDE has registered the new component)."""
        if self.config.get("state.app_id") == APP_ID:
            return
        from .prefs import set_autostart
        legacy_autostart = os.path.join(GLib.get_user_config_dir(), "autostart", f"{LEGACY_APP_ID}.desktop")
        if os.path.exists(legacy_autostart):
            os.remove(legacy_autostart)
            set_autostart(True)
        # Screen-sharing permission was granted to the old ID; ask again (with
        # the explanation) rather than surprising the user with KDE's dialog.
        self.config.set("recording.restore_token", "")
        self.config.set("state.app_id", APP_ID)

    def _setup_shortcuts(self):
        """KDE: shortcuts come from our .desktop file via KGlobalAccel (like
        Spectacle). Elsewhere: the XDG GlobalShortcuts portal."""
        from .shortcuts import SHORTCUTS, GlobalShortcuts
        from .takeover import (GLINT, KGlobalAccel, migrate_legacy_component, remove_stray_portal_shortcuts,
                               restore_default_keys)

        kga = KGlobalAccel()
        self.kde_shortcuts = kga.available()
        if self.kde_shortcuts:
            self.shortcuts = None
            removed = remove_stray_portal_shortcuts(kga, {sid: desc for sid, desc, _t in SHORTCUTS})
            if removed:
                print(f"glint: removed {len(removed)} stale portal shortcuts from "
                      f"{', '.join(sorted({c for c, _a in removed}))}")

            def settle():
                moved = migrate_legacy_component(kga)
                if moved:
                    print(f"glint: moved shortcuts from the pre-0.3.0 app ID: {', '.join(moved)}")
                if not self.config.get("state.takeover"):
                    fixed = restore_default_keys(kga)
                    if fixed:
                        print(f"glint: restored default shortcuts for {', '.join(fixed)}")
                return False

            if not kga.has_component(GLINT):
                # Freshly installed (or renamed): ask KDE to rescan desktop
                # files so our X-KDE-Shortcuts register without a re-login.
                system.spawn(["kbuildsycoca6"], on_exit=lambda _ok: GLib.timeout_add_seconds(2, settle))
            else:
                settle()
            return
        self.shortcuts = GlobalShortcuts(self.run_command)
        if not os.environ.get("GLINT_NO_SHORTCUTS"):
            self.shortcuts.start(self._on_shortcuts_bound)

    def _offer_default_tool(self):
        """One-time prompt to take over Print Screen from the desktop's tool."""
        if self.config.get("state.default_tool_offered"):
            return False
        from .takeover import get_takeover
        takeover = get_takeover(self.config)
        if takeover is None or not takeover.available() or takeover.active():
            return False
        plan = takeover.plan()
        if not plan:
            return False
        self.config.set("state.default_tool_offered", True)
        lines = "\n".join(f"{key}  →  {label}" for key, label in plan)
        dialog = Adw.AlertDialog(
            heading="Make Glint your screenshot tool?",
            body=f"Glint can take over these shortcuts from {takeover.tool_name}:\n\n{lines}\n\n"
                 f"{takeover.tool_name} stays installed, and you can switch back any time in "
                 "Preferences → Shortcuts.")
        dialog.add_response("later", "Not Now")
        dialog.add_response("use", "Use Glint")
        dialog.set_response_appearance("use", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("use")
        dialog.connect("response", lambda _d, r: self.set_default_tool() if r == "use" else None)
        dialog.present(None)
        return False

    def set_default_tool(self):
        from .takeover import get_takeover
        takeover = get_takeover(self.config)
        if takeover is None or not takeover.available():
            self._error("Can't change the screenshot shortcuts",
                        takeover.reason_unavailable() if takeover else "Unsupported desktop")
            return False
        try:
            plan = takeover.take_over()
        except (RuntimeError, GLib.Error) as exc:
            self._error("Can't change the screenshot shortcuts", str(exc))
            return False
        keys = ", ".join(key for key, _label in plan) or "its shortcuts"
        system.notify(self, "Glint is now your screenshot tool", f"{keys} now open Glint.", "default-tool")
        return True

    def unset_default_tool(self):
        from .takeover import get_takeover
        takeover = get_takeover(self.config)
        if takeover is None or not takeover.restore():
            system.notify(self, "Nothing to restore", "Glint isn't set as the screenshot tool.", "default-tool")
            return False
        system.notify(self, f"{takeover.tool_name} restored",
                      "The original screenshot shortcuts are back.", "default-tool")
        return True

    def _on_shortcuts_bound(self, ok, message):
        if not ok and message:
            print(f"glint: global shortcuts: {message}")
        elif ok and not self.config.get("state.shortcuts_bound"):
            self.config.set("state.shortcuts_bound", True)

    def do_shutdown(self):
        if self.recorder:
            self.recorder.stop()
            self.recorder.clicks.disable()
        if self.kwin:
            self.kwin.shutdown()
        if self.tray:
            self.tray.shutdown()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        pass

    def do_open(self, files, _n, _hint):
        for f in files:
            if f.get_uri_scheme() == URL_SCHEME:
                self.run_command(f.get_uri())
            elif f.get_path():
                self.open_annotate(f.get_path())

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if args and args[0] in ("-h", "--help", "help"):
            cmdline.print_literal(HELP)
            return 0
        if args and args[0] in ("-v", "--version"):
            cmdline.print_literal(f"{APP_NAME} {VERSION}\n")
            return 0
        if not args or args[0] in ("-b", "--background"):
            if not args and cmdline.get_is_remote():
                self.run_command("preferences")
            return 0
        cmd, params = parse_command(args)
        if "filepath" in params and not os.path.isabs(params["filepath"]):
            params["filepath"] = os.path.join(cmdline.get_cwd() or os.getcwd(), params["filepath"])
        if cmd not in COMMANDS:
            cmdline.printerr_literal(f"glint: unknown command '{cmd}'. See glint --help.\n")
            return 1
        GLib.idle_add(lambda: self.run_command(cmd, params) and False)
        return 0

    # -- dispatch --------------------------------------------------------------

    def run_command(self, command, params=None):
        cmd, parsed = parse_command([command])
        params = {**parsed, **(params or {})}
        handlers = {
            "capture-area": lambda: self.capture_area("area"),
            "capture-window": lambda: self.capture_area("window"),
            "capture-previous-area": self.capture_previous_area,
            "capture-fullscreen": self.capture_fullscreen,
            "self-timer": lambda: self.self_timer(int(params.get("seconds") or
                                                      self.config.get("state.self_timer_seconds"))),
            "scrolling-capture": self.scrolling_capture,
            "capture-text": self.capture_text,
            "record-screen": lambda: self.record_screen(params.get("format") or self.config.get("recording.format")),
            "stop-recording": self.stop_recording,
            "open-annotate": lambda: self.open_annotate(params.get("filepath")),
            "trim-video": lambda: self.open_trimmer(params.get("filepath")),
            "open-from-clipboard": self.open_from_clipboard,
            "pin": lambda: self.pin_file(params.get("filepath")),
            "restore-recently-closed": self.restore_recently_closed,
            "history": self.show_history,
            "open-folder": lambda: self._open_folder(),
            "set-default": self.set_default_tool,
            "unset-default": self.unset_default_tool,
            "preferences": self.show_preferences,
            "about": self.show_about,
            "quit": self.quit_app,
        }
        handler = handlers.get(cmd)
        if handler is None:
            print(f"glint: unknown command {cmd!r}")
            return False
        handler()
        return False

    # -- selection plumbing ----------------------------------------------------

    def _select(self, mode, on_result, freeze=None, hint=None, allow_window=True):
        """Show the selection overlay; on_result(result_dict or None, shot or None)."""
        if self.busy:
            return
        self.busy = True
        cfg = self.config
        freeze = cfg.get("selection.freeze_screen") if freeze is None else freeze
        hide_cards = self.qa.visible
        if hide_cards:
            self.qa.set_hidden(True)
        state = {"windows": None, "cursor": None, "shot": None if freeze else False, "error": None}

        def done(result):
            if hide_cards:
                self.qa.set_hidden(False)
            self.busy = False
            on_result(result, state["shot"] or None)

        def maybe_open():
            if state["windows"] is None or state["shot"] is None:
                return
            if freeze and not state["shot"]:
                self.busy = False
                if hide_cards:
                    self.qa.set_hidden(False)
                self._error("Screen capture failed", state["error"])
                return
            cursor = state["cursor"]
            ctrl = OverlayController(
                capture.current_monitors(), shot=state["shot"] or None, windows=state["windows"], mode=mode,
                hint=hint, show_crosshair=cfg.get("selection.show_crosshair"),
                show_magnifier=cfg.get("selection.show_magnifier"), on_done=done, allow_window=allow_window)
            if cursor:
                ctrl.cursor = (cursor["x"], cursor["y"])
            ctrl.present()

        def got_windows(windows, cursor):
            state["windows"], state["cursor"] = windows, cursor
            maybe_open()

        def got_shot(shot, err):
            state["shot"], state["error"] = (shot or False), err
            maybe_open()

        self.kwin.query_windows(got_windows)
        if freeze:
            GLib.timeout_add(180 if hide_cards else 0, lambda: capture.grab_desktop(cfg, got_shot) and False)

    def _grab_rect(self, rect, callback, delay_ms=None):
        """Capture `rect` from a fresh desktop grab. callback(pixbuf or None)."""
        delay = self.config.get("selection.live_capture_delay_ms") if delay_ms is None else delay_ms
        hide_cards = self.qa.visible
        if hide_cards:
            self.qa.set_hidden(True)
            delay = max(delay, 200)

        def got(shot, err):
            if hide_cards:
                self.qa.set_hidden(False)
            if shot is None:
                self._error("Screen capture failed", err)
                callback(None)
                return
            callback(shot.crop(rect))

        GLib.timeout_add(delay, lambda: capture.grab_desktop(self.config, got) and False)

    # -- capture commands ------------------------------------------------------

    def capture_area(self, mode="area"):
        freeze = self.config.get("selection.freeze_screen")

        def on_result(result, shot):
            if not result:
                return
            if shot is not None:
                self._finish_selection(result, shot.crop(result["rect"]))
            else:
                self._grab_rect(result["rect"], lambda pb: self._finish_selection(result, pb))

        hint = "Click a window to capture it · Space for area · Esc to cancel" if mode == "window" else None
        self._select(mode, on_result, freeze=freeze, hint=hint)

    def _finish_selection(self, result, pixbuf):
        if pixbuf is None:
            return
        kind = result["kind"]
        if kind == "area":
            self.config.set("state.last_area", list(result["rect"]))
        self.finish_capture(pixbuf, kind, result["rect"])

    def capture_previous_area(self):
        rect = self.config.get("state.last_area")
        if not rect:
            self.capture_area("area")
            return
        self._grab_rect(tuple(rect), lambda pb: pb and self.finish_capture(pb, "area", tuple(rect)), delay_ms=0)

    def capture_fullscreen(self):
        def got_cursor(_windows, cursor):
            monitors = capture.current_monitors()
            mon = capture.monitor_at(monitors, cursor["x"], cursor["y"]) if cursor else None
            mon = mon or (monitors[0] if monitors else None)
            if mon is None:
                return
            self._grab_rect(mon.rect, lambda pb: pb and self.finish_capture(pb, "fullscreen", mon.rect), delay_ms=0)

        self.kwin.query_windows(got_cursor)

    def self_timer(self, seconds):
        def on_result(result, _shot):
            if not result:
                return

            def countdown_done(go):
                if go:
                    self._grab_rect(result["rect"], lambda pb: self._finish_selection(result, pb))

            CountdownWindow(self, max(1, seconds), result["rect"], countdown_done).present()

        self._select("area", on_result, freeze=False,
                     hint=f"Select an area for the {seconds}s self-timer · click a window · F for full screen")

    def capture_text(self):
        def on_result(result, shot):
            if not result:
                return

            def ocr(pb):
                if pb is None:
                    return
                path = os.path.join(paths.captures_cache_dir(), f"ocr-{GLib.get_real_time()}.png")
                system.save_pixbuf(pb, path)
                self.ocr_file(path)

            if shot is not None:
                ocr(shot.crop(result["rect"]))
            else:
                self._grab_rect(result["rect"], ocr)

        self._select("area", on_result, hint="Select text to copy · Esc to cancel")

    # -- screen sharing (recording & scrolling capture) -------------------------

    def _with_screen_share(self, show_cursor, then):
        """Open a ScreenCast session *before* the area overlay, then call
        then(session).

        Wayland requires the desktop's consent dialog; Glint asks it to
        remember the choice, so it normally appears only the first time. Doing
        it first means the user's area selection is always the last step and
        recording starts the moment it's made.
        """
        from .screencast import TOKEN_KEY, ScreenCastSession

        if self.busy or self._sharing:
            return
        self._sharing = True
        session = ScreenCastSession(self.config, TOKEN_KEY)

        def started(ok, err):
            self._sharing = False
            if ok:
                then(session)
                return
            session.close()
            if err and "cancelled" not in err:
                self._error("Screen sharing failed", err)

        def start():
            session.start(started, show_cursor=show_cursor)

        if self.config.get(TOKEN_KEY):
            start()
        else:
            self._explain_screen_sharing(start, lambda: setattr(self, "_sharing", False))

    def _explain_screen_sharing(self, proceed, cancel):
        dialog = Adw.AlertDialog(
            heading="Allow Glint to record your screen",
            body="Your desktop will now ask which screen Glint may record. You only need to do this once.\n\n"
                 "• Choose your screen — or all of them, if you have more than one\n"
                 "• Don't pick “Region”: you'll select the area in Glint right after\n"
                 "• Let your desktop remember the choice, so you aren't asked again")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("continue", "Continue")
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("continue")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: proceed() if r == "continue" else cancel())
        dialog.present(None)

    def scrolling_capture(self):
        if self.scrolling is not None:
            self.scrolling.finish()
            return

        def with_session(session):
            def on_result(result, _shot):
                if not result:
                    session.close()
                    return
                from .scrolling import ScrollingSession

                rect = result["rect"]

                def finished(path, err):
                    self.scrolling = None
                    if err:
                        self._error("Scrolling capture failed", err)
                    if path:
                        self.finish_capture(system.load_pixbuf(path), "scrolling", rect)

                def begin():
                    self.scrolling = ScrollingSession(self, rect, capture.current_monitors(), finished,
                                                      session=session)
                    self.scrolling.start()
                    return False

                GLib.timeout_add(250, begin)  # let the overlay fade out of the stream

            self._select("area", on_result, freeze=False,
                         hint="Select the scrolling area, then scroll slowly · Esc to cancel")

        self._with_screen_share(False, with_session)

    def record_screen(self, fmt="mp4"):
        if self.recorder is not None:
            self.recorder.stop()
            return

        def with_session(session):
            def on_result(result, _shot):
                if not result:
                    session.close()
                    return
                from .recorder import Recorder

                def begin():
                    self.recorder = Recorder(
                        self, result["rect"], capture.current_monitors(), fmt=fmt, session=session,
                        on_finished=lambda path, err: self._recording_done(path, err, result["rect"]))
                    self.recorder.start()
                    return False

                GLib.timeout_add(250, begin)  # let the overlay fade out before the first frame

            label = "GIF" if fmt == "gif" else "video"
            self._select("area", on_result, freeze=False,
                         hint=f"Select an area to record as {label} · click a window · F for full screen")

        self._with_screen_share(self.config.get("recording.show_cursor"), with_session)

    def stop_recording(self):
        if self.recorder is not None:
            self.recorder.stop()

    def on_recording_state(self, active):
        if self.tray:
            self.tray.set_recording(active)

    def _recording_done(self, path, err, rect):
        self.recorder = None
        if err:
            self._error("Recording failed", err)
        if not path:
            return
        if self.config.get("general.play_sounds"):
            system.play_sound("complete")
        from .recorder import poster_frame

        def got_poster(poster):
            from .trimmer import probe
            info = probe(path) or {}
            self.history.add(path, kind="gif" if path.lower().endswith(".gif") else "video",
                             width=info.get("width"), height=info.get("height"),
                             duration=info.get("duration"), poster=poster)
            card = None
            if poster is None:
                system.notify(self, "Recording saved", os.path.basename(path))
            else:
                card = self.qa.add(CaptureItem(poster, kind="video", rect=rect, saved=True, video=path))
            if self.config.get("recording.open_trimmer"):
                if card is not None:
                    card.trim()
                else:
                    self.open_trimmer(path)

        poster_frame(path, got_poster)

    # -- results -----------------------------------------------------------------

    def finish_capture(self, pixbuf, kind, rect=None):
        if pixbuf is None:
            return
        cfg = self.config
        cache = os.path.join(paths.captures_cache_dir(), f"{kind}-{GLib.get_real_time()}.png")
        system.save_pixbuf(pixbuf, cache)
        if kind == "window" and cfg.get("window_capture.background"):
            from .annotate import background
            framed = cache.replace(".png", "-framed.png")
            background.compose_file(cache, framed, cfg.get("window_capture.background_preset"),
                                    cfg.get("window_capture.padding"), cfg.get("window_capture.radius"),
                                    cfg.get("window_capture.shadow"))
            cache = framed
        if cfg.get("general.play_sounds"):
            system.play_sound("screen-capture")

        item = CaptureItem(cache, kind="image", rect=rect)
        if cfg.get("after_capture.save"):
            item.path = self.save_capture_file(cache, kind=kind.capitalize())
            item.saved = True
        entry = self.history.add(item.path, kind="screenshot" if kind == "area" else kind, adopt=not item.saved,
                                 width=pixbuf.get_width(), height=pixbuf.get_height())
        if entry is not None:
            item.path = entry["path"]
        if cfg.get("after_capture.copy"):
            system.copy_image(item.path)
        self._prune_cache()

        card = None
        if cfg.get("after_capture.show_overlay"):
            card = self.qa.add(item)
        if cfg.get("after_capture.annotate"):
            def saved(path):
                item.path = path
                if card is not None and card in self.qa.cards:
                    card.refresh()
            self.open_annotate(item.path, on_saved=saved)
        if not card and not cfg.get("after_capture.annotate"):
            what = "saved to " + os.path.basename(item.path) if item.saved else "copied"
            system.notify(self, "Screenshot captured", what.capitalize(), "capture")

    def save_capture_file(self, src, kind="Screenshot"):
        cfg = self.config
        fmt = cfg.get("general.format")
        directory = cfg.save_dir()
        os.makedirs(directory, exist_ok=True)
        try:
            pb_size = system.load_pixbuf(src)
            size = (pb_size.get_width(), pb_size.get_height())
        except GLib.Error:
            size = None
        base = naming.render(cfg.get("general.filename_template"), kind=kind, size=size)
        dest = naming.unique_path(directory, base, fmt)
        system.convert_image(src, dest, fmt, cfg.get("general.jpg_quality"))
        return dest

    def _prune_cache(self):
        limit = max(5, int(self.config.get("general.history_limit")))
        directory = paths.captures_cache_dir()
        try:
            files = sorted((os.path.join(directory, f) for f in os.listdir(directory)), key=os.path.getmtime)
        except OSError:
            return
        keep = {c.item.path for c in self.qa.cards} | {i.path for i in self.qa.closed}
        for f in files[:-limit * 2]:
            if f not in keep:
                try:
                    os.remove(f)
                except OSError:
                    pass

    # -- tools ---------------------------------------------------------------------

    def _choose_image(self, title, callback):
        dialog = Gtk.FileDialog(title=title)
        flt = Gtk.FileFilter()
        flt.set_name("Images")
        flt.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        dialog.set_initial_folder(Gio.File.new_for_path(self.config.save_dir()))

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                callback(f.get_path())

        dialog.open(None, None, done)

    def open_annotate(self, path=None, on_saved=None):
        if not path:
            self._choose_image("Open Image to Annotate", lambda p: self.open_annotate(p))
            return
        if not os.path.exists(path):
            self._error("File not found", path)
            return
        from .annotate.editor import AnnotateWindow
        win = AnnotateWindow(self, path, on_saved=on_saved)
        win.present()

    def _choose_video(self, callback):
        dialog = Gtk.FileDialog(title="Open Video to Trim")
        flt = Gtk.FileFilter()
        flt.set_name("Videos and GIFs")
        for mime in ("video/*", "image/gif"):
            flt.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        dialog.set_initial_folder(Gio.File.new_for_path(self.config.video_dir()
                                                        if os.path.isdir(self.config.video_dir())
                                                        else GLib.get_home_dir()))

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                callback(f.get_path())

        dialog.open(None, None, done)

    def open_trimmer(self, path=None, on_saved=None):
        if not path:
            self._choose_video(lambda p: self.open_trimmer(p))
            return
        if not os.path.exists(path):
            self._error("File not found", path)
            return
        if not system.have("ffmpeg"):
            self._error("Trimming needs ffmpeg", "Install it with: sudo dnf install ffmpeg-free")
            return
        from .trimmer import TrimWindow
        TrimWindow(self, path, on_saved=on_saved).present()

    def open_from_clipboard(self):
        def got(path):
            if path:
                self.open_annotate(path)
            else:
                self._error("No image on the clipboard")
        system.read_clipboard_image(got)

    def pin_file(self, path=None, rect=None):
        if not path:
            self._choose_image("Pin Image to Screen", lambda p: self.pin_file(p))
            return
        from .pin import PinWindow
        position = (rect[0], rect[1]) if rect else None
        PinWindow(self, path, position).present()

    def ocr_file(self, path):
        if not system.have("tesseract"):
            self._error("Text recognition needs Tesseract", "Install it with: sudo dnf install tesseract")
            return

        def done(ok, out, err):
            text = out.strip()
            if not ok:
                self._error("Text recognition failed", err.strip()[:200])
            elif not text:
                system.notify(self, "No text found", "Glint couldn't find any text in the selection.", "ocr")
            else:
                system.copy_text(text)
                preview = text if len(text) < 120 else text[:117] + "…"
                system.notify(self, "Text copied", preview, "ocr")

        system.run_capture(["tesseract", path, "stdout", "--psm", "6"], done)

    def show_history(self):
        if self.history_window is None:
            from .history_window import HistoryWindow
            self.history_window = HistoryWindow(self)
            self.history_window.connect("destroy", lambda *_: setattr(self, "history_window", None))
        self.history_window.present()

    def history_media_changed(self, old_path, new_path=None):
        """A capture was edited (annotated / trimmed) or saved elsewhere."""
        entry = self.history.find_path(old_path)
        if entry is None:
            return
        if new_path and new_path != old_path:
            self.history.relink(old_path, new_path)
        if entry.get("kind") in ("video", "gif"):
            from .recorder import poster_frame

            def got(poster):
                if poster:
                    self.history.thumbnailer(poster, self.history.thumb_path(entry))
                self.history.touch(entry)

            poster_frame(entry["path"], got)
        else:
            self.history.touch(entry)

    def restore_recently_closed(self):
        if not self.qa.restore_last():
            system.notify(self, "Nothing to restore", "No recently closed captures.", "restore")

    def _open_folder(self):
        os.makedirs(self.config.save_dir(), exist_ok=True)
        system.open_path(self.config.save_dir())

    def show_preferences(self):
        from .prefs import PreferencesDialog
        PreferencesDialog(self).present(None)

    def show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                developer_name="baldybuilds", developers=["baldybuilds https://github.com/baldybuilds"],
                                website="https://github.com/baldybuilds/glint", issue_url="https://github.com/baldybuilds/glint/issues",
                                license_type=Gtk.License.MIT_X11,
                                comments="Screenshots, annotations and screen recordings for Linux.\n"
                                         "Inspired by CleanShot X for macOS.")
        about.present(None)

    def quit_app(self):
        if self._held:
            self.release()
            self._held = False
        self.quit()

    # -- feedback ------------------------------------------------------------------

    def toast(self, text):
        system.notify(self, text, "", "toast")

    def _error(self, title, detail=None):
        print(f"glint: {title}: {detail or ''}", file=sys.stderr)
        system.notify(self, title, detail or "", "error")


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    # Answer informational flags without registering (and becoming) the
    # long-running primary instance.
    if len(argv) > 1 and argv[1] in ("-h", "--help", "help"):
        print(HELP, end="")
        return 0
    if len(argv) > 1 and argv[1] in ("-v", "--version"):
        print(f"{APP_NAME} {VERSION}")
        return 0
    if len(argv) == 2 and argv[1] in ("set-default", "unset-default") and not _service_running():
        return _default_tool_cli(argv[1])
    app = GlintApp()
    return app.run(argv)


def _service_running():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                            GLib.Variant("(s)", (APP_ID,)), None, Gio.DBusCallFlags.NONE, 1000, None)
        return bool(res.unpack()[0])
    except GLib.Error:
        return False


def _default_tool_cli(command):
    """set-default / unset-default without starting the tray service (the
    running service does it itself, so its in-memory config stays in sync)."""
    from .takeover import get_takeover
    config = Config()
    takeover = get_takeover(config)
    if takeover is None:
        print("glint: no supported screenshot shortcuts found on this desktop", file=sys.stderr)
        return 1
    if command == "unset-default":
        if takeover.restore():
            print(f"Restored {takeover.tool_name}'s screenshot shortcuts.")
        else:
            print("Glint isn't set as the screenshot tool; nothing to restore.")
        return 0
    if not takeover.available():
        print(f"glint: {takeover.reason_unavailable()}", file=sys.stderr)
        return 1
    for key, label in takeover.take_over():
        print(f"{key:>18}  →  {label}")
    print("Glint is now the screenshot tool. Undo with: glint unset-default")
    return 0
