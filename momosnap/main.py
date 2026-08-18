"""Entry point: grab the screen, then hand it to the overlay."""
import fcntl
import os
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

_LOCK_FD = None


def acquire_lock():
    """One overlay at a time: F1 while a capture is on screen is ignored."""
    global _LOCK_FD
    path = os.path.join(GLib.get_user_runtime_dir(), "momosnap.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _LOCK_FD = fd
    return True


def release_lock():
    """Called when the overlay leaves the screen. The clipboard holder keeps
    living without the lock, so the NEXT F1 press must work again."""
    global _LOCK_FD
    if _LOCK_FD is not None:
        try:
            fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
            os.close(_LOCK_FD)
        except OSError:
            pass
        _LOCK_FD = None

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

    if not acquire_lock():
        # An overlay is already open; repeated F1 presses are ignored.
        return 0

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
