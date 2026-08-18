"""Pin window: the captured selection floating as a frameless window.

Wayland note: a normal app cannot force itself always-on-top — the compositor
decides stacking. GNOME still offers it per window: press Alt+Space on the pin
and choose "Always on Top". That is the closest an app can get without being
part of the shell.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


class PinWindow(Gtk.ApplicationWindow):
    """Frameless image window. Drag to move; Esc, right- or middle-click closes."""

    def __init__(self, app, pixbuf, logical_w, logical_h):
        super().__init__(application=app)
        self.set_decorated(False)
        self.set_default_size(max(1, logical_w), max(1, logical_h))
        self.set_title("MoMo Snap pin")

        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        pic = Gtk.Picture.new_for_paintable(texture)
        pic.set_can_shrink(True)
        pic.set_cursor(Gdk.Cursor.new_from_name("grab"))
        self.set_child(pic)

        move = Gtk.GestureClick()
        move.set_button(1)
        move.connect("pressed", self._on_press)
        pic.add_controller(move)

        for button in (2, 3):            # middle or right click closes
            g = Gtk.GestureClick()
            g.set_button(button)
            g.connect("pressed", lambda *_: self.close())
            pic.add_controller(g)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _on_press(self, gesture, n_press, x, y):
        if n_press >= 2:
            self.close()
            return
        surface = self.get_surface()
        event = gesture.get_current_event()
        if surface and event:
            # Hands the drag to the compositor, which is the only legal way
            # to move a window on Wayland.
            surface.begin_move(gesture.get_device(), 1, x, y, event.get_time())

    def _on_key(self, _c, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
