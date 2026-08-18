"""The MoMo Snap overlay: dim the screen, pick a region, draw on it.

Coordinate note
---------------
Everything is stored in IMAGE pixels, never widget pixels. The display runs at
1.25 scaling, so the captured frame and the window are different sizes. Storing
image coordinates means the crop is exact no matter what the scale is; the only
conversion happens at the edges, in _to_image() and the draw handler.
"""
import math
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
# Registers pycairo with PyGObject. Without this, drawing onto a cairo surface
# we made ourselves fails with "could not find foreign type Context".
gi.require_foreign("cairo")
import cairo  # noqa: E402,F401

from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from .placement import place_toolbar

DIM_ALPHA = 0.45
MIN_SELECTION = 8          # image px; smaller than this counts as a stray click
HANDLE = 3.0

PALETTE = [
    ("#ef4444", "Red"),
    ("#f2a93b", "Amber"),
    ("#22c55e", "Green"),
    ("#3b82f6", "Blue"),
    ("#ffffff", "White"),
    ("#111827", "Black"),
]

TOOL_ARROW = "arrow"
TOOL_RECT = "rect"
TOOL_PEN = "pen"
TOOL_TEXT = "text"

# stroke size -> (line width, text size), all in image pixels
STROKES = {"s": (1.5, 13.0), "m": (3.0, 18.0), "l": (6.0, 28.0)}


def _rgba(hex_colour):
    c = Gdk.RGBA()
    c.parse(hex_colour)
    return c


class Overlay(Gtk.ApplicationWindow):
    def __init__(self, app, pixbuf):
        super().__init__(application=app)
        self.pixbuf = pixbuf
        self.img_w = pixbuf.get_width()
        self.img_h = pixbuf.get_height()

        self.mode = "select"          # "select" -> "edit"
        self.sel = None               # (x, y, w, h) in image px
        self.drag_origin = None
        self.shapes = []              # committed annotations
        self.preview = None           # shape being dragged right now
        self.tool = None              # None = dragging inside MOVES the box
        self.colour = PALETTE[0][0]
        self.line_width, self.font_size = STROKES["m"]
        self._text_entry = None       # live Gtk.Entry while typing a label
        self._text_pos = None         # image-px anchor of that entry
        self._clipboard_held = False

        self.set_decorated(False)
        self.fullscreen()

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._on_draw)
        self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

        overlay = Gtk.Overlay()
        overlay.set_child(self.area)
        self._overlay_box = overlay   # text entries are overlaid here too

        self.toolbar = self._build_toolbar()
        self.toolbar.set_visible(False)
        # The toolbar is added straight to the overlay and pinned to the top-left,
        # then moved with margins. Do NOT wrap it in a Gtk.Fixed: that container
        # fills the whole window and swallows every mouse event before the drawing
        # area can see it, which stops the drag-to-select working at all.
        self.toolbar.set_halign(Gtk.Align.START)
        self.toolbar.set_valign(Gtk.Align.START)
        overlay.add_overlay(self.toolbar)
        self.set_child(overlay)

        self.resize_edges = None      # e.g. {"l","t"} while dragging a corner
        self.resize_start = None
        self.move_start = None        # selection at the moment a move began
        self._cursor_name = "crosshair"

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        # Snipaste muscle memory: right-click steps back (clear, then quit).
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", lambda *_: self._escape())
        self.area.add_controller(rclick)

        # Live cursor feedback: resize arrows on edges, move hand inside.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ------------------------------------------------------------------ scale
    def _scale(self):
        """Image pixels per widget pixel."""
        w = self.area.get_width() or self.img_w
        h = self.area.get_height() or self.img_h
        return self.img_w / w, self.img_h / h

    def _to_image(self, wx, wy):
        sx, sy = self._scale()
        return wx * sx, wy * sy

    # ------------------------------------------------------------------- draw
    def _on_draw(self, _area, cr, width, height):
        if not width or not height:
            return
        fx = width / self.img_w
        fy = height / self.img_h

        cr.save()
        cr.scale(fx, fy)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.restore()

        cr.set_source_rgba(0, 0, 0, DIM_ALPHA)
        cr.paint()

        sel = self.preview_selection()
        if not sel:
            return
        x, y, w, h = sel
        rx, ry, rw, rh = x * fx, y * fy, w * fx, h * fy

        # Punch the selection back to full brightness.
        cr.save()
        cr.rectangle(rx, ry, rw, rh)
        cr.clip()
        cr.scale(fx, fy)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.restore()

        # Annotations live inside the selection only.
        cr.save()
        cr.rectangle(rx, ry, rw, rh)
        cr.clip()
        cr.scale(fx, fy)
        for shape in self.shapes:
            self._draw_shape(cr, shape)
        if self.preview and self.mode == "edit":
            self._draw_shape(cr, self.preview)
        cr.restore()

        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.set_line_width(1.0)
        cr.rectangle(rx + 0.5, ry + 0.5, rw - 1, rh - 1)
        cr.stroke()

        label = f"{int(w)} x {int(h)}"
        cr.select_font_face("sans")
        cr.set_font_size(13)
        ext = cr.text_extents(label)
        bx, by = rx, max(0, ry - 24)
        cr.set_source_rgba(0, 0, 0, 0.7)
        cr.rectangle(bx, by, ext.width + 12, 20)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.move_to(bx + 6, by + 14)
        cr.show_text(label)

    @staticmethod
    def _draw_shape(cr, shape):
        c = _rgba(shape["colour"])
        cr.set_source_rgba(c.red, c.green, c.blue, 1.0)
        cr.set_line_width(shape.get("width", 1.0))   # text has no stroke
        cr.set_line_cap(1)   # round
        cr.set_line_join(1)

        kind = shape["kind"]
        if kind == TOOL_RECT:
            x0, y0, x1, y1 = shape["box"]
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.stroke()
        elif kind == TOOL_ARROW:
            x0, y0, x1, y1 = shape["box"]
            Overlay._draw_arrow(cr, x0, y0, x1, y1, shape["width"])
        elif kind == TOOL_PEN:
            pts = shape["points"]
            if len(pts) < 2:
                return
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.stroke()
        elif kind == TOOL_TEXT:
            cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(shape["size"])
            x, y = shape["pos"]
            cr.move_to(x, y + shape["size"])
            cr.show_text(shape["text"])

    @staticmethod
    def _draw_arrow(cr, x0, y0, x1, y1, width):
        angle = math.atan2(y1 - y0, x1 - x0)
        head = max(10.0, width * 4.0)
        # Stop the shaft short so it does not poke through the head.
        sx = x1 - math.cos(angle) * head * 0.8
        sy = y1 - math.sin(angle) * head * 0.8
        cr.move_to(x0, y0)
        cr.line_to(sx, sy)
        cr.stroke()
        spread = math.radians(26)
        cr.move_to(x1, y1)
        cr.line_to(x1 - math.cos(angle - spread) * head,
                   y1 - math.sin(angle - spread) * head)
        cr.line_to(x1 - math.cos(angle + spread) * head,
                   y1 - math.sin(angle + spread) * head)
        cr.close_path()
        cr.fill()

    def preview_selection(self):
        return self.sel

    # ------------------------------------------------------------- gestures
    def _cursor_for(self, ix, iy):
        edges = self._hit_edges(ix, iy)
        if edges:
            if edges in ({"l", "t"}, {"r", "b"}):
                return "nwse-resize"
            if edges in ({"l", "b"}, {"r", "t"}):
                return "nesw-resize"
            if edges & {"l", "r"}:
                return "ew-resize"
            return "ns-resize"
        if self._inside_selection(ix, iy):
            if self.tool is None:
                return "move"
            return "text" if self.tool == TOOL_TEXT else "crosshair"
        return "crosshair"

    def _on_motion(self, _c, wx, wy):
        if self.mode != "edit" or self.drag_origin:
            return
        name = self._cursor_for(*self._to_image(wx, wy))
        if name != self._cursor_name:
            self._cursor_name = name
            self.area.set_cursor(Gdk.Cursor.new_from_name(name))

    def _hit_edges(self, ix, iy):
        """Which selection edges are under the point, within a grab margin."""
        if not self.sel:
            return None
        scale_x, _ = self._scale()
        t = 10 * scale_x                      # ~10 widget px, in image units
        x, y, w, h = self.sel
        if not (x - t <= ix <= x + w + t and y - t <= iy <= y + h + t):
            return None
        edges = set()
        if abs(ix - x) <= t:
            edges.add("l")
        if abs(ix - (x + w)) <= t:
            edges.add("r")
        if abs(iy - y) <= t:
            edges.add("t")
        if abs(iy - (y + h)) <= t:
            edges.add("b")
        return edges or None

    def _on_drag_begin(self, gesture, sx, sy):
        if self._text_entry is not None:
            # Clicking elsewhere finishes the label being typed, like editors do.
            self._commit_text()
        ix, iy = self._to_image(sx, sy)
        self.drag_origin = (ix, iy)
        if self.mode == "select":
            self.sel = (ix, iy, 0, 0)
            self.area.queue_draw()
            return

        edges = self._hit_edges(ix, iy)
        if edges:
            # Grabbing a border resizes the box instead of drawing.
            self.resize_edges = edges
            self.resize_start = self.sel
            self.toolbar.set_visible(False)
        elif self._inside_selection(ix, iy):
            if self.tool is None:
                # No tool chosen: dragging inside moves the whole box.
                self.move_start = self.sel
                self.toolbar.set_visible(False)
            elif self.tool == TOOL_TEXT:
                self._start_text_input(sx, sy, ix, iy)
                self.drag_origin = None
                return
            elif self.tool == TOOL_PEN:
                self.preview = {"kind": TOOL_PEN, "points": [(ix, iy)],
                                "colour": self.colour, "width": self.line_width}
            else:
                self.preview = {"kind": self.tool, "box": (ix, iy, ix, iy),
                                "colour": self.colour, "width": self.line_width}
        else:
            # Dragging outside the box starts a brand-new selection, so a
            # badly placed box costs one redraw, not an Esc round-trip.
            self.mode = "select"
            self.sel = (ix, iy, 0, 0)
            self.shapes.clear()
            self.toolbar.set_visible(False)
            self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.area.queue_draw()

    def _on_drag_update(self, gesture, ox, oy):
        if not self.drag_origin:
            return
        sx, sy = self._scale()
        ix = self.drag_origin[0] + ox * sx
        iy = self.drag_origin[1] + oy * sy

        if self.mode == "select":
            x0, y0 = self.drag_origin
            self.sel = (min(x0, ix), min(y0, iy), abs(ix - x0), abs(iy - y0))
        elif self.move_start:
            x, y, w, h = self.move_start
            nx = max(0.0, min(x + (ix - self.drag_origin[0]), self.img_w - w))
            ny = max(0.0, min(y + (iy - self.drag_origin[1]), self.img_h - h))
            self.sel = (nx, ny, w, h)
        elif self.resize_edges:
            x, y, w, h = self.resize_start
            x0, y0, x1, y1 = x, y, x + w, y + h
            if "l" in self.resize_edges:
                x0 = min(ix, x1 - MIN_SELECTION)
            if "r" in self.resize_edges:
                x1 = max(ix, x0 + MIN_SELECTION)
            if "t" in self.resize_edges:
                y0 = min(iy, y1 - MIN_SELECTION)
            if "b" in self.resize_edges:
                y1 = max(iy, y0 + MIN_SELECTION)
            self.sel = self._clamp_selection((x0, y0, x1 - x0, y1 - y0))
        elif self.preview:
            ix, iy = self._clamp_to_selection(ix, iy)
            if self.preview["kind"] == TOOL_PEN:
                self.preview["points"].append((ix, iy))
            else:
                x0, y0 = self.drag_origin
                self.preview["box"] = (x0, y0, ix, iy)
        self.area.queue_draw()

    def _on_drag_end(self, gesture, ox, oy):
        if not self.drag_origin:
            return
        if self.mode == "select":
            if self.sel and self.sel[2] >= MIN_SELECTION and self.sel[3] >= MIN_SELECTION:
                self.sel = self._clamp_selection(self.sel)
                self.mode = "edit"
                self.area.set_cursor(Gdk.Cursor.new_from_name("default"))
                self._show_toolbar()
            else:
                self.sel = None       # stray click, stay in select mode
        elif self.move_start:
            self.move_start = None
            self._show_toolbar()
        elif self.resize_edges:
            self.resize_edges = None
            self.resize_start = None
            self._show_toolbar()
        elif self.preview:
            # A plain click emits drag-begin+end with no movement; committing
            # that would stamp a junk arrowhead into the export.
            pv = self.preview
            if pv["kind"] == TOOL_PEN:
                keep = len(pv["points"]) >= 2
            else:
                x0, y0, x1, y1 = pv["box"]
                keep = abs(x1 - x0) + abs(y1 - y0) >= 3
            if keep:
                self.shapes.append(pv)
            self.preview = None
        self.drag_origin = None
        self.area.queue_draw()

    def _clamp_selection(self, sel):
        x, y, w, h = sel
        x = max(0, min(x, self.img_w))
        y = max(0, min(y, self.img_h))
        w = min(w, self.img_w - x)
        h = min(h, self.img_h - y)
        return (x, y, w, h)

    def _inside_selection(self, ix, iy):
        if not self.sel:
            return False
        x, y, w, h = self.sel
        return x <= ix <= x + w and y <= iy <= y + h

    def _clamp_to_selection(self, ix, iy):
        x, y, w, h = self.sel
        return max(x, min(ix, x + w)), max(y, min(iy, y + h))

    # ----------------------------------------------------------------- text
    def _start_text_input(self, wx, wy, ix, iy):
        entry = Gtk.Entry()
        entry.set_width_chars(14)
        entry.set_halign(Gtk.Align.START)
        entry.set_valign(Gtk.Align.START)
        entry.set_margin_start(max(0, int(wx)))
        entry.set_margin_top(max(0, int(wy)))
        entry.connect("activate", lambda *_: self._commit_text())

        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _kc, _state):
            if keyval == Gdk.KEY_Escape:
                self._remove_text_entry()   # cancel just the label
                return True
            return False

        keys.connect("key-pressed", on_key)
        entry.add_controller(keys)

        self._text_entry = entry
        self._text_pos = (ix, iy)
        self._overlay_box.add_overlay(entry)
        entry.grab_focus()

    def _commit_text(self):
        entry, pos = self._text_entry, self._text_pos
        if entry is None:
            return
        text = entry.get_text().strip()
        self._remove_text_entry()
        if text:
            self.shapes.append({"kind": TOOL_TEXT, "pos": pos, "text": text,
                                "colour": self.colour, "size": self.font_size})
            self.area.queue_draw()

    def _remove_text_entry(self):
        if self._text_entry is not None:
            self._overlay_box.remove_overlay(self._text_entry)
            self._text_entry = None
            self._text_pos = None

    # -------------------------------------------------------------- toolbar
    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class("toolbar")
        bar.add_css_class("osd")
        bar.set_halign(Gtk.Align.START)

        self._tool_buttons = {}
        for tool, icon, tip in (
            (TOOL_ARROW, "mail-forward-symbolic", "Arrow"),
            (TOOL_RECT, "checkbox-symbolic", "Rectangle"),
            (TOOL_PEN, "document-edit-symbolic", "Pen"),
            (TOOL_TEXT, "insert-text-symbolic", "Text"),
        ):
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip)
            b.connect("toggled", self._on_tool_toggled, tool)
            bar.append(b)
            self._tool_buttons[tool] = b

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._swatches = []
        for hex_colour, name in PALETTE:
            sw = Gtk.Button(tooltip_text=name)
            sw.set_size_request(22, 22)
            sw.add_css_class("momo-swatch")
            sw.connect("clicked", self._on_colour, hex_colour)
            ctx = sw.get_style_context()
            provider = Gtk.CssProvider()
            provider.load_from_string(
                f".momo-swatch {{ background: {hex_colour}; "
                f"min-width:18px; min-height:18px; padding:0; }} "
                f".momo-swatch.momo-active {{ "
                f"border: 2px solid #ffffff; }}"
            )
            ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            bar.append(sw)
            self._swatches.append(sw)
        self._swatches[0].add_css_class("momo-active")

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._stroke_buttons = {}
        for key, label, tip in (("s", "•", "Thin"),
                                ("m", "●", "Medium"),
                                ("l", "⬤", "Thick")):
            b = Gtk.ToggleButton(label=label, tooltip_text=tip)
            b.connect("toggled", self._on_stroke_toggled, key)
            bar.append(b)
            self._stroke_buttons[key] = b
        self._stroke_buttons["m"].set_active(True)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        for icon, tip, cb in (
            ("view-pin-symbolic", "Pin on screen (F3)", lambda *_: self.pin()),
            ("edit-undo-symbolic", "Undo (Ctrl+Z)", lambda *_: self.undo()),
            ("edit-copy-symbolic", "Copy (Ctrl+C)", lambda *_: self.copy_to_clipboard()),
            ("document-save-symbolic", "Save (Ctrl+S)", lambda *_: self.save()),
            ("window-close-symbolic", "Cancel (Esc)", lambda *_: self.close()),
        ):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", cb)
            bar.append(b)
        return bar

    def _on_tool_toggled(self, button, tool):
        if button.get_active():
            self.tool = tool
            for name, b in self._tool_buttons.items():
                if name != tool:
                    b.set_active(False)
        elif self.tool == tool:
            # Toggling the active tool off returns to move mode.
            self.tool = None

    def _on_stroke_toggled(self, button, key):
        if button.get_active():
            self.line_width, self.font_size = STROKES[key]
            for k, b in self._stroke_buttons.items():
                if k != key:
                    b.set_active(False)
        elif not any(b.get_active() for b in self._stroke_buttons.values()):
            button.set_active(True)   # one size is always selected

    def _on_colour(self, button, hex_colour):
        self.colour = hex_colour
        for sw in self._swatches:
            sw.remove_css_class("momo-active")
        button.add_css_class("momo-active")

    def _show_toolbar(self):
        self.toolbar.set_visible(True)
        # Measure first: placement needs the real toolbar size, not a guess.
        min_size, nat_size = self.toolbar.get_preferred_size()
        tw = max(nat_size.width, 1)
        th = max(nat_size.height, 1)

        fx = (self.area.get_width() or self.img_w) / self.img_w
        fy = (self.area.get_height() or self.img_h) / self.img_h
        x, y, w, h = self.sel
        sel_widget = (x * fx, y * fy, w * fx, h * fy)
        screen = (self.area.get_width(), self.area.get_height())

        tx, ty, _mode = place_toolbar(sel_widget, (tw, th), screen)
        self.toolbar.set_margin_start(tx)
        self.toolbar.set_margin_top(ty)

    # ---------------------------------------------------------------- export
    def _render_selection(self):
        """Flatten the selection plus its annotations into a GdkPixbuf."""
        fx, fy, fw, fh = self.sel
        x = max(0, math.floor(fx))
        y = max(0, math.floor(fy))
        w = max(1, min(self.img_w, math.ceil(fx + fw)) - x)
        h = max(1, min(self.img_h, math.ceil(fy + fh)) - y)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        cr.translate(-x, -y)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        for shape in self.shapes:
            self._draw_shape(cr, shape)
        surface.flush()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)

    def copy_to_clipboard(self):
        if not self.sel:
            return
        pixbuf = self._render_selection()
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        clipboard = self.get_display().get_clipboard()
        value = GObject.Value(Gdk.Texture, texture)
        ok = clipboard.set_content(Gdk.ContentProvider.new_for_value(value))
        if not ok:
            # The compositor refused the claim; pretending otherwise would
            # leave the user pasting nothing.
            from .main import notify
            notify("Copy failed", "The clipboard refused the image.")
            return

        # On Wayland the clipboard dies with the process that owns it, so the
        # window is hidden and the app stays alive until another program takes
        # the clipboard over. No timeout: a paste an hour later must work.
        # The F1 lock is released here: the overlay is gone, so the next
        # press must start a fresh capture even while this process lingers.
        from .main import release_lock
        release_lock()
        self._clipboard_held = True
        self.set_visible(False)
        clipboard.connect("changed", self._on_clipboard_changed)

    def _on_clipboard_changed(self, clipboard):
        if self._clipboard_held and not clipboard.is_local():
            self._release_clipboard()

    def _release_clipboard(self):
        if self._clipboard_held:
            self._clipboard_held = False
            self.close()
        return False

    def save(self):
        if not self.sel:
            return
        folder = os.path.join(
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
            or os.path.expanduser("~/Pictures"),
            "Screenshots",
        )
        os.makedirs(folder, exist_ok=True)
        name = time.strftime("MoMoSnap_%Y-%m-%d_%H-%M-%S.png")
        path = os.path.join(folder, name)
        self._render_selection().savev(path, "png", [], [])
        from .main import notify
        notify("Saved", f"Pictures/Screenshots/{name}")
        self.close()

    def pin(self):
        """Float the selection as a frameless window and leave the overlay."""
        if not self.sel:
            return
        if self._text_entry is not None:
            self._commit_text()
        pixbuf = self._render_selection()
        # Widget px per image px: the pin should appear at on-screen size.
        fx = (self.area.get_width() or self.img_w) / self.img_w
        from .main import release_lock
        from .pin import PinWindow
        win = PinWindow(self.get_application(), pixbuf,
                        int(pixbuf.get_width() * fx),
                        int(pixbuf.get_height() * fx))
        win.present()
        release_lock()               # the overlay is gone; F1 must work again
        self.close()

    def undo(self):
        if self.shapes:
            self.shapes.pop()
            self.area.queue_draw()

    # -------------------------------------------------------------- keyboard
    def _escape(self):
        if self.mode == "edit":
            # First step back drops the selection, the second quits.
            self.mode = "select"
            self.sel = None
            self.shapes.clear()
            self.toolbar.set_visible(False)
            self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
            self.area.queue_draw()
        else:
            self.close()

    _NUDGE = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
              Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1)}

    def _on_key(self, _c, keyval, _keycode, state):
        if self._text_entry is not None:
            return False              # the label entry owns the keyboard
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape:
            self._escape()
            return True
        if keyval == Gdk.KEY_F3 and self.mode == "edit":
            self.pin()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.mode == "edit":
            self.copy_to_clipboard()
            return True
        if self.mode == "edit" and self.sel and keyval in self._NUDGE:
            dx, dy = self._NUDGE[keyval]
            step = 10 if state & Gdk.ModifierType.SHIFT_MASK else 1
            x, y, w, h = self.sel
            x = max(0, min(x + dx * step, self.img_w - w))
            y = max(0, min(y + dy * step, self.img_h - h))
            self.sel = (x, y, w, h)
            self._show_toolbar()
            self.area.queue_draw()
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_to_clipboard()
            return True
        if ctrl and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.save()
            return True
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo()
            return True
        return False
