"""Entry point: grab the screen, then hand it to the overlay."""
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from .capture import CaptureError, grab_screen, reset_token
from .overlay import Overlay


class MoMoSnap(Gtk.Application):
    def __init__(self):
        # NON_UNIQUE matters: with a unique app id, a second F1 press while a
        # previous instance is still holding the clipboard would forward the
        # activation to the OLD process, which would then show its OLD
        # screenshot. Every press must be its own process with a fresh frame.
        super().__init__(application_id="vip.momo.Snap",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.pixbuf = None

    def do_activate(self):
        win = Overlay(self, self.pixbuf)
        win.present()


def notify(summary, body=""):
    """Desktop notification; stderr is invisible when launched from a hotkey."""
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "MoMo Snap", summary, body])


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)

    # The screen is captured BEFORE any window exists, otherwise the overlay
    # would photograph itself.
    try:
        pixbuf = grab_screen()
    except CaptureError as exc:
        if str(exc) == "cancelled":
            return 0
        # A stale or revoked restore token is the usual cause. Drop it and try
        # once more; that brings the permission dialog back instead of failing.
        reset_token()
        try:
            pixbuf = grab_screen()
        except CaptureError as exc2:
            if str(exc2) == "cancelled":
                return 0
            print(f"MoMo Snap: could not capture the screen: {exc2}",
                  file=sys.stderr)
            notify("MoMo Snap could not capture the screen", str(exc2))
            return 1

    app = MoMoSnap()
    app.pixbuf = pixbuf
    return app.run([argv[0]])


if __name__ == "__main__":
    sys.exit(main())
