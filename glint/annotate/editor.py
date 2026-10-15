"""The annotation editor window."""

import math
import os

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import geometry, naming, paths, system  # noqa: E402
from . import background as bgmod  # noqa: E402
from .model import (PALETTE, TEXT_STYLES, TOOLS, BoxShape, CounterShape, Document,  # noqa: E402
                    LineShape, PathShape, TextShape)

HANDLE = 5          # handle radius in screen px
HIT_TOL = 6         # hit tolerance in screen px
CROP_RATIOS = [("Free", 0), ("Original", -1), ("1:1", 1), ("4:3", 4 / 3), ("3:2", 3 / 2),
               ("16:9", 16 / 9), ("9:16", 9 / 16)]
BOX_TOOLS = ("rect", "filled", "ellipse", "pixelate", "blur", "spotlight")


def _icon(tool):
    return f"glint-tool-{tool}-symbolic"


class AnnotateWindow(Adw.ApplicationWindow):
    def __init__(self, app, path, on_saved=None):
        super().__init__(application=app)
        self.app = app
        self.cfg = app.config
        self.source_path = path
        self.on_saved = on_saved
        self.doc = Document(system.load_pixbuf(path))
        self.doc.spotlight_opacity = self.cfg.get("annotate.spotlight_opacity")

        self.tool = self.cfg.get("annotate.tool") or "arrow"
        self.color = tuple(self.cfg.get("annotate.color"))
        self.width = self.cfg.get("annotate.width")
        self.text_style = self.cfg.get("annotate.text_style")
        self.counter_style = self.cfg.get("annotate.counter_style")
        self.intensity = self.cfg.get("annotate.pixelate_intensity")
        self.shadow = self.cfg.get("annotate.draw_shadow")
        self.crop_ratio = 0

        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.selected = None
        self.drag = None
        self.editing = None          # TextShape being edited
        self.fit = True
        self.scale = 1.0
        self.ox = self.oy = 0.0
        self.pointer = (0, 0)

        self.set_title("Annotate — Glint")
        self.set_default_size(*self._initial_size())
        self._build_ui()
        self._select_tool(self.tool)
        self.connect("close-request", self._on_close_request)

    # -- UI construction -----------------------------------------------------

    def _initial_size(self):
        mons = Gdk.Display.get_default().get_monitors()
        mw, mh = 1600, 1000
        if mons.get_n_items():
            g = mons.get_item(0).get_geometry()
            mw, mh = g.width * 0.85, g.height * 0.85
        w = min(max(self.doc.width + 80, 900), mw)
        h = min(max(self.doc.height + 160, 600), mh)
        return int(w), int(h)

    def _build_ui(self):
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        self.tool_buttons = {}
        tools = Gtk.Box(spacing=2)
        tools.add_css_class("linked")
        group = None
        for tool_id, label, key in TOOLS:
            btn = Gtk.ToggleButton()
            btn.set_icon_name(_icon(tool_id))
            btn.set_tooltip_text(f"{label} ({key.upper()})")
            if group:
                btn.set_group(group)
            else:
                group = btn
            btn.connect("toggled", self._on_tool_toggled, tool_id)
            tools.append(btn)
            self.tool_buttons[tool_id] = btn
        header.set_title_widget(tools)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Undo (Ctrl+Z)")
        self.undo_btn.connect("clicked", lambda *_: self.undo())
        self.redo_btn = Gtk.Button(icon_name="edit-redo-symbolic", tooltip_text="Redo (Ctrl+Shift+Z)")
        self.redo_btn.connect("clicked", lambda *_: self.redo())
        header.pack_start(self.undo_btn)
        header.pack_start(self.redo_btn)

        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.set_tooltip_text("Save and close (Ctrl+Enter)")
        done.connect("clicked", lambda *_: self.done())
        header.pack_end(done)

        menu = Gio.Menu()
        menu.append("Save As…", "win.save-as")
        menu.append("Save to Screenshots Folder", "win.save-copy")
        menu.append("Pin to Screen", "win.pin")
        menu.append("Copy Text (OCR)", "win.ocr")
        menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        header.pack_end(menu_btn)

        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy to clipboard (Ctrl+C)")
        copy_btn.connect("clicked", lambda *_: self.copy())
        header.pack_end(copy_btn)

        self.bg_btn = Gtk.ToggleButton(icon_name="glint-tool-background-symbolic", tooltip_text="Background")
        self.bg_btn.connect("toggled", self._on_bg_toggled)
        header.pack_end(self.bg_btn)

        for name, cb in (("save-as", self.save_as), ("save-copy", self.save_copy),
                         ("pin", self.pin), ("ocr", self.ocr)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, f=cb: f())
            self.add_action(action)

        tv.add_top_bar(header)

        # Canvas -------------------------------------------------------------
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_focusable(True)
        self.canvas.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.canvas.add_controller(drag)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_click)
        self.canvas.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.canvas.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.canvas.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # Text editing layer.
        self.fixed = Gtk.Fixed()
        self.fixed.set_can_target(True)
        overlay = Gtk.Overlay()
        overlay.set_child(self.canvas)
        overlay.add_overlay(self.fixed)
        overlay.set_clip_overlay(self.fixed, True)
        self.fixed.set_can_target(False)

        self.text_view = Gtk.TextView()
        self.text_view.add_css_class("glint-text-edit")
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.get_buffer().connect("changed", self._on_text_changed)
        tkeys = Gtk.EventControllerKey()
        tkeys.connect("key-pressed", self._on_text_key)
        self.text_view.add_controller(tkeys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.idle_add(self._commit_text))
        self.text_view.add_controller(focus)
        self.text_css = Gtk.CssProvider()
        self.text_view.get_style_context().add_provider(self.text_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        # Background side panel.
        self.bg_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.bg_revealer.set_child(self._build_bg_panel())

        content = Gtk.Box()
        content.append(overlay)
        content.append(self.bg_revealer)
        self.options_bar = self._build_options_bar()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(content)
        column.append(self.options_bar)
        tv.set_content(column)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(tv)
        self.set_content(self.toasts)
        self._update_undo_buttons()

    def _build_options_bar(self):
        bar = Gtk.ActionBar()
        self.swatches = []
        swatch_box = Gtk.Box(spacing=4)
        for color in PALETTE:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("glint-swatch")
            area = Gtk.DrawingArea(content_width=20, content_height=20)
            area.set_draw_func(self._draw_swatch, color)
            btn.set_child(area)
            btn.connect("clicked", lambda _b, c=color: self._set_color(c))
            swatch_box.append(btn)
            self.swatches.append((btn, area, color))
        self.color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True))
        self.color_button.set_rgba(system.rgba(*self.color))
        self.color_button.connect("notify::rgba", self._on_custom_color)
        swatch_box.append(self.color_button)
        bar.pack_start(swatch_box)

        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.width_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 20, 1)
        self.width_scale.set_size_request(130, -1)
        self.width_scale.set_value(self.width)
        self.width_scale.set_draw_value(True)
        self.width_scale.set_tooltip_text("Thickness ([ / ])")
        self.width_scale.connect("value-changed", self._on_width_changed)
        self.width_box = self._labeled("Size", self.width_scale)
        bar.pack_start(self.width_box)

        self.style_drop = Gtk.DropDown.new_from_strings([label for _id, label in TEXT_STYLES])
        self.style_drop.set_selected([i for i, (sid, _l) in enumerate(TEXT_STYLES) if sid == self.text_style][0]
                                     if any(sid == self.text_style for sid, _l in TEXT_STYLES) else 0)
        self.style_drop.connect("notify::selected", self._on_style_changed)
        self.style_box = self._labeled("Style", self.style_drop)
        bar.pack_start(self.style_box)

        self.counter_drop = Gtk.DropDown.new_from_strings(["1, 2, 3", "A, B, C"])
        self.counter_drop.set_selected(1 if self.counter_style == "letter" else 0)
        self.counter_drop.connect("notify::selected", self._on_counter_style)
        self.counter_box = self._labeled("Counter", self.counter_drop)
        bar.pack_start(self.counter_box)

        self.intensity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 4, 40, 1)
        self.intensity_scale.set_size_request(120, -1)
        self.intensity_scale.set_value(self.intensity)
        self.intensity_scale.connect("value-changed", self._on_intensity)
        self.intensity_box = self._labeled("Intensity", self.intensity_scale)
        bar.pack_start(self.intensity_box)

        self.opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.1, 0.9, 0.05)
        self.opacity_scale.set_size_request(120, -1)
        self.opacity_scale.set_value(self.doc.spotlight_opacity)
        self.opacity_scale.connect("value-changed", self._on_opacity)
        self.spot_shape = Gtk.DropDown.new_from_strings(["Rectangle", "Ellipse"])
        box = Gtk.Box(spacing=6)
        box.append(self.spot_shape)
        box.append(self.opacity_scale)
        self.opacity_box = self._labeled("Spotlight", box)
        bar.pack_start(self.opacity_box)

        self.ratio_drop = Gtk.DropDown.new_from_strings([label for label, _r in CROP_RATIOS])
        self.ratio_drop.connect("notify::selected", self._on_ratio)
        reset = Gtk.Button(label="Reset")
        reset.connect("clicked", lambda *_: self._reset_crop())
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda *_: self._select_tool("select"))
        box = Gtk.Box(spacing=6)
        box.append(self.ratio_drop)
        box.append(reset)
        box.append(apply_btn)
        self.crop_box = self._labeled("Aspect", box)
        bar.pack_start(self.crop_box)

        self.shadow_check = Gtk.CheckButton(label="Shadow", active=self.shadow)
        self.shadow_check.connect("toggled", self._on_shadow_toggled)
        bar.pack_end(self.shadow_check)

        self._zoom_text = "100%"
        self.zoom_label = Gtk.Label(label="100%", width_chars=5, xalign=1)
        self.zoom_label.add_css_class("dim-label")
        self.zoom_label.add_css_class("numeric")
        bar.pack_end(self.zoom_label)
        return bar

    def _labeled(self, text, widget):
        box = Gtk.Box(spacing=6)
        label = Gtk.Label(label=text)
        label.add_css_class("dim-label")
        box.append(label)
        box.append(widget)
        return box

    def _build_bg_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(250, -1)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        title = Gtk.Label(label="Background", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                           min_children_per_line=4, row_spacing=6, column_spacing=6)
        for preset in bgmod.PRESETS:
            btn = Gtk.Button(tooltip_text=bgmod.PRESET_LABELS[preset])
            btn.add_css_class("flat")
            area = Gtk.DrawingArea(content_width=44, content_height=32)
            area.set_draw_func(self._draw_preset, preset)
            btn.set_child(area)
            btn.connect("clicked", lambda _b, p=preset: self._set_bg(preset=p, enabled=True))
            flow.append(btn)
        box.append(flow)

        def scale_row(label, lo, hi, key):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lab = Gtk.Label(label=label, xalign=0)
            lab.add_css_class("dim-label")
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
            sc.set_value(self.doc.bg[key])
            sc.connect("value-changed", lambda s: self._set_bg(**{key: int(s.get_value())}))
            row.append(lab)
            row.append(sc)
            return row

        box.append(scale_row("Padding", 0, 240, "padding"))
        box.append(scale_row("Corner radius", 0, 48, "radius"))
        shadow = Gtk.CheckButton(label="Drop shadow", active=self.doc.bg["shadow"])
        shadow.connect("toggled", lambda b: self._set_bg(shadow=b.get_active()))
        box.append(shadow)
        off = Gtk.Button(label="Remove background")
        off.connect("clicked", lambda *_: self._set_bg(enabled=False))
        box.append(off)
        return box

    # -- drawing helpers -----------------------------------------------------

    def _draw_swatch(self, area, cr, w, h, color):
        cr.arc(w / 2, h / 2, min(w, h) / 2 - 2, 0, 2 * math.pi)
        cr.set_source_rgba(*color)
        cr.fill_preserve()
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
        cr.set_line_width(1)
        cr.stroke()
        if tuple(color) == tuple(self.color):
            cr.arc(w / 2, h / 2, min(w, h) / 2 - 0.5, 0, 2 * math.pi)
            cr.set_source_rgba(0.24, 0.56, 1.0, 1)
            cr.set_line_width(2)
            cr.stroke()

    def _draw_preset(self, area, cr, w, h, preset):
        bgmod.rounded_rect(cr, 1, 1, w - 2, h - 2, 6)
        cr.clip()
        if preset == "transparent":
            self._checkerboard(cr, 0, 0, w, h, 6)
        else:
            bgmod.paint_background(cr, w, h, preset)

    def _checkerboard(self, cr, x, y, w, h, size=10):
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.set_source_rgb(0.8, 0.8, 0.8)
        cr.paint()
        cr.set_source_rgb(0.62, 0.62, 0.62)
        for j in range(int(h // size) + 1):
            for i in range(int(w // size) + 1):
                if (i + j) % 2:
                    cr.rectangle(x + i * size, y + j * size, size, size)
        cr.fill()
        cr.restore()

    # -- view transform ------------------------------------------------------

    def _crop_mode(self):
        return self.tool == "crop"

    def _update_view(self, w, h):
        ow, oh = self.doc.output_size(self._crop_mode())
        if self.fit:
            margin = 32
            self.scale = min((w - 2 * margin) / max(ow, 1), (h - 2 * margin) / max(oh, 1), 1.0)
            self.scale = max(self.scale, 0.02)
            self.ox = (w - ow * self.scale) / 2
            self.oy = (h - oh * self.scale) / 2
        text = f"{round(self.scale * 100)}%"
        if text != self._zoom_text:
            # Never touch widgets from inside the draw pass: it invalidates
            # their allocation mid-snapshot and GTK then skips drawing them.
            self._zoom_text = text
            GLib.idle_add(lambda: self.zoom_label.set_label(text) and False)

    def to_image(self, wx, wy):
        ox = (wx - self.ox) / self.scale
        oy = (wy - self.oy) / self.scale
        return self.doc.to_image(ox, oy, self._crop_mode())

    def to_widget(self, ix, iy):
        ox, oy = self.doc.to_output(ix, iy, self._crop_mode())
        return self.ox + ox * self.scale, self.oy + oy * self.scale

    def _draw(self, area, cr, w, h):
        self._update_view(w, h)
        full = self._crop_mode()
        ow, oh = self.doc.output_size(full)
        cr.save()
        cr.translate(self.ox, self.oy)
        cr.scale(self.scale, self.scale)
        if self.doc.bg["enabled"] and self.doc.bg["preset"] == "transparent" and not full:
            self._checkerboard(cr, 0, 0, ow, oh, 10 / self.scale)
        cr.set_source_rgba(0, 0, 0, 0.3)
        cr.rectangle(0, 2 / self.scale, ow, oh)
        cr.fill()
        self.doc.render(cr, full=full)
        dx, dy = self.doc.offset(full)
        cr.translate(dx, dy)
        if full:
            self._draw_crop_ui(cr)
        elif self.selected is not None and self.selected in self.doc.shapes and self.editing is None:
            self._draw_selection(cr, self.selected)
        cr.restore()

    def _draw_selection(self, cr, shape):
        s = self.scale
        x, y, w, h = shape.bbox()
        pad = 4 / s
        cr.set_line_width(1 / s)
        cr.set_dash([4 / s, 3 / s])
        cr.set_source_rgba(0.24, 0.56, 1.0, 0.9)
        cr.rectangle(x - pad, y - pad, w + 2 * pad, h + 2 * pad)
        cr.stroke()
        cr.set_dash([])
        for _key, hx, hy in shape.handles():
            self._handle(cr, hx, hy)

    def _handle(self, cr, x, y):
        r = HANDLE / self.scale
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1)
        cr.fill_preserve()
        cr.set_source_rgba(0.24, 0.56, 1.0, 1)
        cr.set_line_width(1.5 / self.scale)
        cr.stroke()

    def _crop_rect(self):
        return self.doc.crop or (0, 0, self.doc.width, self.doc.height)

    def _crop_handles(self):
        x, y, w, h = self._crop_rect()
        return [("tl", x, y), ("tr", x + w, y), ("bl", x, y + h), ("br", x + w, y + h),
                ("t", x + w / 2, y), ("b", x + w / 2, y + h), ("l", x, y + h / 2), ("r", x + w, y + h / 2)]

    def _draw_crop_ui(self, cr):
        x, y, w, h = self._crop_rect()
        W, H = self.doc.width, self.doc.height
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, W, H)
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1 / self.scale)
        for i in (1, 2):
            cr.move_to(x + w * i / 3, y)
            cr.line_to(x + w * i / 3, y + h)
            cr.move_to(x, y + h * i / 3)
            cr.line_to(x + w, y + h * i / 3)
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2 / self.scale)
        cr.rectangle(x, y, w, h)
        cr.stroke()
        for _k, hx, hy in self._crop_handles():
            self._handle(cr, hx, hy)

    # -- tools & options -----------------------------------------------------

    def _on_tool_toggled(self, btn, tool_id):
        if btn.get_active() and self.tool != tool_id:
            self._select_tool(tool_id, from_button=True)

    def _select_tool(self, tool_id, from_button=False):
        self._commit_text()
        if self.tool == "crop" and tool_id != "crop":
            self.fit = True
        self.tool = tool_id
        if not from_button:
            self.tool_buttons[tool_id].set_active(True)
        if tool_id != "select":
            self.cfg.set("annotate.tool", tool_id)
        if tool_id == "crop":
            self.selected = None
            self.fit = True
        self.width_box.set_visible(tool_id not in ("pixelate", "blur", "spotlight", "crop"))
        self.style_box.set_visible(tool_id == "text")
        self.counter_box.set_visible(tool_id == "counter")
        self.intensity_box.set_visible(tool_id in ("pixelate", "blur"))
        self.opacity_box.set_visible(tool_id == "spotlight")
        self.crop_box.set_visible(tool_id == "crop")
        cursor = {"select": "default", "text": "text", "crop": "crosshair"}.get(tool_id, "crosshair")
        self.canvas.set_cursor_from_name(cursor)
        self.canvas.queue_draw()

    def _set_color(self, color):
        self.color = tuple(color)
        self.cfg.set("annotate.color", list(self.color))
        for _b, area, _c in self.swatches:
            area.queue_draw()
        if self.selected is not None and hasattr(self.selected, "color"):
            self._push_undo()
            self.selected.color = self.color
            self.canvas.queue_draw()

    def _on_custom_color(self, btn, _pspec):
        c = btn.get_rgba()
        color = (c.red, c.green, c.blue, c.alpha)
        if color != self.color:
            self._set_color(color)

    def _on_width_changed(self, scale):
        self.width = int(scale.get_value())
        self.cfg.set("annotate.width", self.width)
        sel = self.selected
        if sel is not None and sel.kind not in ("pixelate", "blur", "spotlight"):
            self._push_undo(coalesce="width")
            sel.width = self.width
            if isinstance(sel, TextShape):
                sel.size = max(14, self.width * 6)
            self.canvas.queue_draw()

    def _on_style_changed(self, drop, _pspec):
        self.text_style = TEXT_STYLES[drop.get_selected()][0]
        self.cfg.set("annotate.text_style", self.text_style)
        target = self.editing or (self.selected if isinstance(self.selected, TextShape) else None)
        if target is not None:
            if target is not self.editing:
                self._push_undo()
            target.style = self.text_style
            self._position_text_view()
            self.canvas.queue_draw()

    def _on_counter_style(self, drop, _pspec):
        self.counter_style = "letter" if drop.get_selected() == 1 else "number"
        self.cfg.set("annotate.counter_style", self.counter_style)
        changed = [s for s in self.doc.shapes if isinstance(s, CounterShape)]
        if changed:
            self._push_undo()
            for s in changed:
                s.style = self.counter_style
            self.canvas.queue_draw()

    def _on_intensity(self, scale):
        self.intensity = int(scale.get_value())
        self.cfg.set("annotate.pixelate_intensity", self.intensity)
        if self.selected is not None and self.selected.kind in ("pixelate", "blur"):
            self._push_undo(coalesce="intensity")
            self.selected.intensity = self.intensity
            self.canvas.queue_draw()

    def _on_opacity(self, scale):
        self._push_undo(coalesce="opacity")
        self.doc.spotlight_opacity = round(scale.get_value(), 2)
        self.cfg.set("annotate.spotlight_opacity", self.doc.spotlight_opacity)
        self.canvas.queue_draw()

    def _on_shadow_toggled(self, check):
        self.shadow = check.get_active()
        self.cfg.set("annotate.draw_shadow", self.shadow)
        if self.selected is not None:
            self._push_undo()
            self.selected.shadow = self.shadow
            self.canvas.queue_draw()

    def _on_bg_toggled(self, btn):
        self.bg_revealer.set_reveal_child(btn.get_active())
        if btn.get_active() and not self.doc.bg["enabled"]:
            self._set_bg(enabled=True)

    def _set_bg(self, **kw):
        self.doc.bg.update(kw)
        self.dirty = True
        self.fit = True
        self.canvas.queue_draw()

    def _on_ratio(self, drop, _pspec):
        _label, ratio = CROP_RATIOS[drop.get_selected()]
        if ratio == -1:
            ratio = self.doc.width / self.doc.height
        self.crop_ratio = ratio
        if ratio > 0:
            self._push_undo()
            self.doc.crop = geometry.apply_aspect(self._crop_rect(), ratio)
            self.canvas.queue_draw()

    def _reset_crop(self):
        self._push_undo()
        self.doc.crop = None
        self.canvas.queue_draw()

    # -- undo ----------------------------------------------------------------

    def _push_undo(self, coalesce=None):
        if coalesce and self.undo_stack and self.undo_stack[-1][1] == coalesce:
            return
        self.undo_stack.append((self.doc.state(), coalesce))
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True
        self._update_undo_buttons()

    def _drop_undo(self):
        if self.undo_stack:
            self.undo_stack.pop()
        self._update_undo_buttons()

    def undo(self):
        self._commit_text()
        if not self.undo_stack:
            return
        self.redo_stack.append(self.doc.state())
        state, _c = self.undo_stack.pop()
        self.doc.restore(state)
        self.selected = None
        self._update_undo_buttons()
        self.canvas.queue_draw()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.doc.state(), None))
        self.doc.restore(self.redo_stack.pop())
        self.selected = None
        self._update_undo_buttons()
        self.canvas.queue_draw()

    def _update_undo_buttons(self):
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))

    # -- pointer input -------------------------------------------------------

    def _shift(self, gesture):
        state = gesture.get_current_event_state()
        return bool(state & Gdk.ModifierType.SHIFT_MASK)

    def _hit_handle(self, handles, ix, iy):
        tol = (HANDLE + 3) / self.scale
        for key, hx, hy in handles:
            if abs(ix - hx) <= tol and abs(iy - hy) <= tol:
                return key
        return None

    def _on_motion(self, _ctrl, x, y):
        self.pointer = (x, y)
        if self.drag is not None:
            return
        ix, iy = self.to_image(x, y)
        name = {"select": "default", "text": "text"}.get(self.tool, "crosshair")
        if self._crop_mode():
            key = self._hit_handle(self._crop_handles(), ix, iy)
            if key:
                name = {"tl": "nw-resize", "tr": "ne-resize", "bl": "sw-resize", "br": "se-resize",
                        "t": "n-resize", "b": "s-resize", "l": "w-resize", "r": "e-resize"}[key]
            elif geometry.contains(self._crop_rect(), ix, iy):
                name = "move"
        elif self.tool not in ("counter",):
            if self.selected is not None and self._hit_handle(self.selected.handles(), ix, iy):
                name = "pointer"
            elif self.doc.shape_at(ix, iy, HIT_TOL / self.scale) is not None:
                name = "move"
        self.canvas.set_cursor_from_name(name)

    def _on_click(self, gesture, n_press, x, y):
        if n_press == 2:
            ix, iy = self.to_image(x, y)
            shape = self.doc.shape_at(ix, iy, HIT_TOL / self.scale)
            if isinstance(shape, TextShape):
                self._begin_text_edit(shape)

    def _on_drag_begin(self, gesture, x, y):
        self.canvas.grab_focus()
        if self.editing is not None:
            self._commit_text()
        ix, iy = self.to_image(x, y)
        self.drag = {"start": (ix, iy), "last": (ix, iy), "mode": None, "moved": False}

        if self._crop_mode():
            key = self._hit_handle(self._crop_handles(), ix, iy)
            self._push_undo()
            if key:
                self.drag.update(mode="crop-handle", key=key, rect=self._crop_rect())
            elif geometry.contains(self._crop_rect(), ix, iy) and self.doc.crop:
                self.drag.update(mode="crop-move")
            else:
                self.drag.update(mode="crop-new")
            return

        if self.tool == "text":
            shape = self.doc.shape_at(ix, iy, HIT_TOL / self.scale)
            if isinstance(shape, TextShape):
                self._push_undo()
                self.selected = shape
                self.drag.update(mode="move", shape=shape)
            else:
                self.drag["mode"] = "text-new"
            return

        if self.selected is not None and self.tool != "counter":
            key = self._hit_handle(self.selected.handles(), ix, iy)
            if key:
                self._push_undo()
                self.drag.update(mode="handle", key=key, shape=self.selected)
                return
        hit = self.doc.shape_at(ix, iy, HIT_TOL / self.scale) if self.tool != "counter" else None
        if hit is not None:
            self._push_undo()
            self.selected = hit
            self.drag.update(mode="move", shape=hit)
            self.canvas.queue_draw()
            return
        if self.tool == "select":
            self.selected = None
            self.canvas.queue_draw()
            return

        self._push_undo()
        shape = self._new_shape(ix, iy)
        self.doc.shapes.append(shape)
        self.selected = None
        self.drag.update(mode="create", shape=shape)
        self.canvas.queue_draw()

    def _new_shape(self, ix, iy):
        common = dict(color=self.color, width=self.width, shadow=self.shadow)
        t = self.tool
        if t in BOX_TOOLS:
            return BoxShape(t, ix, iy, intensity=self.intensity,
                            ellipse=(t == "spotlight" and self.spot_shape.get_selected() == 1), **common)
        if t in ("line", "arrow"):
            return LineShape(t, ix, iy, **common)
        if t in ("pen", "marker"):
            return PathShape(t, ix, iy, **common)
        if t == "counter":
            return CounterShape(ix, iy, number=self.doc.next_counter(), style=self.counter_style, **common)
        raise ValueError(t)

    def _on_drag_update(self, gesture, dx, dy):
        if self.drag is None or self.drag["mode"] is None:
            return
        ok, sx, sy = gesture.get_start_point()
        ix, iy = self.to_image(sx + dx, sy + dy)
        shift = self._shift(gesture)
        d = self.drag
        lx, ly = d["last"]
        d["last"] = (ix, iy)
        if abs(dx) + abs(dy) > 1:
            d["moved"] = True
        mode = d["mode"]
        if mode == "create":
            shape = d["shape"]
            if isinstance(shape, PathShape):
                shape.add_point(ix, iy, straight=shift)
            elif isinstance(shape, CounterShape):
                shape.x, shape.y = ix, iy
            else:
                if self.cfg.get("annotate.inverse_arrow") and isinstance(shape, LineShape) and shape.kind == "arrow":
                    shape.x0, shape.y0 = ix, iy
                    shape.x1, shape.y1 = d["start"]
                else:
                    shape.set_end(ix, iy, constrain=shift)
        elif mode == "move":
            d["shape"].translate(ix - lx, iy - ly)
        elif mode == "handle":
            d["shape"].move_handle(d["key"], ix, iy, constrain=shift)
        elif mode == "crop-new":
            sx0, sy0 = d["start"]
            rect = geometry.normalize(sx0, sy0, ix, iy)
            self.doc.crop = self._fit_crop(rect)
        elif mode == "crop-move":
            x, y, w, h = self._crop_rect()
            nx = min(max(0, x + ix - lx), self.doc.width - w)
            ny = min(max(0, y + iy - ly), self.doc.height - h)
            self.doc.crop = (nx, ny, w, h)
        elif mode == "crop-handle":
            x, y, w, h = d["rect"]
            left, top, right, bottom = x, y, x + w, y + h
            k = d["key"]
            if "l" in k:
                left = ix
            if "r" in k:
                right = ix
            if "t" in k:
                top = iy
            if "b" in k:
                bottom = iy
            rect = geometry.normalize(left, top, right, bottom)
            if self.crop_ratio > 0:
                rx, ry, rw, rh = rect
                if k in ("t", "b"):
                    rw = rh * self.crop_ratio
                else:
                    rh = rw / self.crop_ratio
                if "t" in k:
                    ry = bottom - rh
                if "l" in k:
                    rx = right - rw
                rect = (rx, ry, rw, rh)
            self.doc.crop = self._fit_crop(rect, keep_ratio=False)
        self.canvas.queue_draw()

    def _fit_crop(self, rect, keep_ratio=True):
        if keep_ratio and self.crop_ratio > 0:
            x, y, w, h = rect
            h = w / self.crop_ratio
            rect = (x, y, w, h)
        clipped = geometry.intersect(rect, (0, 0, self.doc.width, self.doc.height))
        if clipped is None or clipped[2] < 4 or clipped[3] < 4:
            return self.doc.crop
        return tuple(int(round(v)) for v in clipped)

    def _on_drag_end(self, gesture, dx, dy):
        d = self.drag
        self.drag = None
        if d is None or d["mode"] is None:
            return
        mode = d["mode"]
        if mode == "text-new":
            ix, iy = d["start"]
            shape = TextShape(ix, iy, text="", style=self.text_style, size=max(14, self.width * 6),
                              color=self.color, width=self.width, shadow=self.shadow)
            self._push_undo()
            self.doc.shapes.append(shape)
            self._begin_text_edit(shape, new=True)
            return
        if mode == "create":
            shape = d["shape"]
            if isinstance(shape, CounterShape):
                pass
            elif shape.is_degenerate():
                self.doc.shapes.remove(shape)
                self._drop_undo()
            elif not isinstance(shape, PathShape):
                self.selected = shape
        elif mode in ("move", "handle") and not d["moved"]:
            self._drop_undo()
        elif mode.startswith("crop") and not d["moved"]:
            self._drop_undo()
        self.canvas.queue_draw()

    def _on_scroll(self, ctrl, dx, dy):
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            px, py = self.pointer
            factor = 1.1 ** (-dy)
            new = min(max(self.scale * factor, 0.05), 16)
            self.ox = px - (px - self.ox) * new / self.scale
            self.oy = py - (py - self.oy) * new / self.scale
            self.scale = new
            self.fit = False
        else:
            if self.fit:
                return False
            self.ox -= dx * 30
            self.oy -= dy * 30
        self._position_text_view()
        self.canvas.queue_draw()
        return True

    # -- text editing --------------------------------------------------------

    def _begin_text_edit(self, shape, new=False):
        self.editing = shape
        self.selected = shape
        buf = self.text_view.get_buffer()
        self._loading_text = True
        buf.set_text(shape.text)
        self._loading_text = False
        if self.text_view.get_parent() is None:
            self.fixed.put(self.text_view, 0, 0)
        self.fixed.set_can_target(True)
        self._position_text_view()
        self.text_view.set_visible(True)
        self.text_view.grab_focus()
        self.canvas.queue_draw()

    def _position_text_view(self):
        shape = self.editing
        if shape is None:
            return
        wx, wy = self.to_widget(shape.x + shape.padding(), shape.y + shape.padding())
        size_px = max(6, shape.size * self.scale)
        family = "Monospace" if shape.style in ("mono", "monobox") else "Sans"
        r, g, b, _a = shape.color
        caret = "white" if (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.5 or shape.style in ("box", "rounded", "monobox") else "black"
        self.text_css.load_from_string(
            f"textview.glint-text-edit, textview.glint-text-edit text {{ background: transparent; color: transparent;"
            f" caret-color: {caret}; font-family: {family}; font-weight: bold; font-size: {size_px:.1f}px;"
            f" padding: 0; }}")
        tw, th = shape.text_extent()
        self.text_view.set_size_request(int(tw * self.scale) + 40, int(th * self.scale) + 8)
        self.fixed.move(self.text_view, wx, wy)

    def _on_text_changed(self, buf):
        if self.editing is None or getattr(self, "_loading_text", False):
            return
        self.editing.text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self._position_text_view()
        self.canvas.queue_draw()

    def _on_text_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval)
        if name == "Escape" or (name in ("Return", "KP_Enter") and not state & Gdk.ModifierType.SHIFT_MASK):
            self._commit_text()
            self.canvas.grab_focus()
            return True
        return False

    def _commit_text(self):
        shape = self.editing
        if shape is None:
            return False
        self.editing = None
        self.text_view.set_visible(False)
        self.fixed.set_can_target(False)
        if shape.is_degenerate() and shape in self.doc.shapes:
            self.doc.shapes.remove(shape)
            self._drop_undo()
            self.selected = None
        self.canvas.queue_draw()
        return False

    # -- keyboard ------------------------------------------------------------

    def _on_key(self, _ctrl, keyval, _code, state):
        if self.editing is not None:
            return False
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        lower = name.lower()
        if ctrl:
            if lower == "z" and shift or lower == "y":
                self.redo()
            elif lower == "z":
                self.undo()
            elif lower == "c":
                self.copy()
            elif lower == "s":
                self.save_as() if shift else self.save_copy()
            elif name in ("Return", "KP_Enter"):
                self.done()
            elif name in ("0", "KP_0"):
                self.fit = True
                self.canvas.queue_draw()
            elif name in ("plus", "equal", "KP_Add", "minus", "KP_Subtract"):
                self.fit = False
                self.scale *= 1.25 if name in ("plus", "equal", "KP_Add") else 0.8
                self.canvas.queue_draw()
            else:
                return False
            return True
        if name in ("Delete", "BackSpace") and self.selected is not None:
            self._push_undo()
            if self.selected in self.doc.shapes:
                self.doc.shapes.remove(self.selected)
            self.doc.renumber_counters()
            self.selected = None
            self.canvas.queue_draw()
            return True
        if name == "Escape":
            if self._crop_mode():
                self._select_tool("select")
            elif self.selected is not None:
                self.selected = None
                self.canvas.queue_draw()
            else:
                self.close()
            return True
        if name in ("Return", "KP_Enter") and self._crop_mode():
            self._select_tool("select")
            return True
        if name in ("Left", "Right", "Up", "Down") and self.selected is not None:
            step = 10 if shift else 1
            dx = {"Left": -step, "Right": step}.get(name, 0)
            dy = {"Up": -step, "Down": step}.get(name, 0)
            self._push_undo(coalesce="nudge")
            self.selected.translate(dx, dy)
            self.canvas.queue_draw()
            return True
        if name in ("bracketleft", "bracketright"):
            self.width_scale.set_value(self.width + (1 if name == "bracketright" else -1))
            return True
        for tool_id, _label, key in TOOLS:
            if key == ("shift+" + lower if shift else lower):
                self._select_tool(tool_id)
                return True
        return False

    # -- output --------------------------------------------------------------

    def _export_temp(self):
        out = os.path.join(paths.captures_cache_dir(), f"annotated-{GLib.get_real_time()}.png")
        return self.doc.export_png(out)

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=2))

    def copy(self):
        self._commit_text()
        path = self._export_temp()
        system.copy_image(path)
        self._toast("Copied to clipboard")

    def _write(self, dest):
        fmt = "jpg" if dest.lower().endswith((".jpg", ".jpeg")) else "png"
        tmp = self._export_temp()
        system.convert_image(tmp, dest, fmt, self.cfg.get("general.jpg_quality"))
        return dest

    def save_copy(self):
        self._commit_text()
        fmt = self.cfg.get("general.format")
        w, h = self.doc.output_size()
        base = naming.render(self.cfg.get("general.filename_template"), kind="Annotated", size=(w, h))
        os.makedirs(self.cfg.save_dir(), exist_ok=True)
        dest = self._write(naming.unique_path(self.cfg.save_dir(), base, fmt))
        self.dirty = False
        self._toast(f"Saved to {os.path.basename(dest)}")
        return dest

    def save_as(self):
        self._commit_text()
        dialog = Gtk.FileDialog(title="Save Screenshot", initial_name=os.path.splitext(
            os.path.basename(self.source_path))[0] + ".png")
        dialog.set_initial_folder(Gio.File.new_for_path(self.cfg.save_dir()))

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            if f:
                self._write(f.get_path())
                self.dirty = False
                self._toast(f"Saved to {f.get_basename()}")

        dialog.save(self, None, done)

    def pin(self):
        self._commit_text()
        self.app.pin_file(self._export_temp())

    def ocr(self):
        self._commit_text()
        self.app.ocr_file(self.source_path)

    def done(self):
        """Write the result back over the capture file and close."""
        self._commit_text()
        dest = self.source_path
        if dest.lower().endswith(".png") or dest.lower().endswith((".jpg", ".jpeg")):
            self._write(dest)
        else:
            dest = self._write(os.path.splitext(dest)[0] + ".png")
        self.dirty = False
        if self.on_saved:
            self.on_saved(dest)
        self.destroy()

    def _on_close_request(self, _win):
        if not self.dirty:
            return False
        dialog = Adw.AlertDialog(heading="Save changes?",
                                 body="Your annotations will be lost if you don't save them.")
        dialog.add_response("discard", "Discard")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def chosen(dlg, response):
            if response == "save":
                self.done()
            elif response == "discard":
                self.dirty = False
                self.destroy()

        dialog.connect("response", chosen)
        dialog.present(self)
        return True
