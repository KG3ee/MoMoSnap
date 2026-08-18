"""Entry point: grab the screen, then hand it to the overlay."""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .capture import CaptureError, grab_screen
from .overlay import Overlay


class MoMoSnap(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="vip.momo.Snap")
        self.pixbuf = None

    def do_activate(self):
        win = Overlay(self, self.pixbuf)
        win.present()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)

    # The screen is captured BEFORE any window exists, otherwise the overlay
    # would photograph itself.
    try:
        pixbuf = grab_screen()
    except CaptureError as exc:
        if str(exc) == "cancelled":
            return 0
        print(f"MoMo Snap: could not capture the screen: {exc}", file=sys.stderr)
        return 1

    app = MoMoSnap()
    app.pixbuf = pixbuf
    return app.run([argv[0]])


if __name__ == "__main__":
    sys.exit(main())
