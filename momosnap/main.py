"""Entry point.

MoMo Snap runs as a small resident app. The first launch becomes the daemon:
it stays alive after the overlay closes, keeping Python, GTK and GStreamer
warm. Every later F1 press is a tiny client process that forwards an
"activate" to the daemon over the session bus and exits; the daemon only has
to pull one frame and map a window, which is what makes the capture feel
instant.

This uses GApplication uniqueness on purpose. The old stale-screenshot bug
happened because the frame was captured BEFORE run() in every process, so a
forwarded activation showed an old frame. Capture now happens INSIDE
do_activate, in the daemon, at press time — always fresh.

`momosnap-run --daemon` starts the resident process without showing an
overlay; put that in autostart so even the first press after login is fast.
"""
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .capture import CaptureError, grab_screen, reset_token
from .overlay import Overlay


def notify(summary, body=""):
    """Desktop notification; stderr is invisible when launched from a hotkey."""
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "MoMo Snap", summary, body])


class MoMoSnap(Gtk.Application):
    def __init__(self, daemon_start):
        super().__init__(application_id="vip.momo.Snap")
        self._boot_quietly = daemon_start
        self._overlay = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()   # stay resident after windows close
        # Pre-warm GStreamer so the FIRST capture is as fast as the rest:
        # registry load and plugin dlopen happen now, not at press time.
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        for factory in ("pipewiresrc", "videoconvert"):
            Gst.ElementFactory.make(factory, None)

    def do_activate(self):
        if self._boot_quietly:
            # --daemon: first activation only boots the resident process.
            self._boot_quietly = False
            from gi.repository import GLib

            def prewarm():
                # Throwaway capture: the first portal handshake and the first
                # PipeWire/GStreamer pipeline in a process are the expensive
                # ones. Paying them at login makes the first F1 press as fast
                # as every other. Failure here is fine; a real press retries.
                try:
                    grab_screen()
                except CaptureError:
                    pass
                # The first GTK window in a process also pays one-time costs
                # (GPU renderer, theme). Realizing a hidden dummy window pays
                # them now, without anything appearing on screen.
                w = Gtk.Window(application=self)
                w.set_default_size(1, 1)
                w.realize()
                w.destroy()
                return False

            GLib.idle_add(prewarm)
            return
        if self._overlay is not None and self._overlay.get_mapped():
            # A capture is already on screen; repeated F1 presses are no-ops.
            return
        try:
            pixbuf = grab_screen()
        except CaptureError as exc:
            if str(exc) == "cancelled":
                return
            # A stale/revoked token is the usual cause: drop it, try once
            # more so the permission dialog comes back instead of failing.
            reset_token()
            try:
                pixbuf = grab_screen()
            except CaptureError as exc2:
                if str(exc2) != "cancelled":
                    notify("MoMo Snap could not capture the screen", str(exc2))
                return
        self._overlay = Overlay(self, pixbuf)
        self._overlay.present()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    daemon_start = "--daemon" in argv
    app = MoMoSnap(daemon_start)
    # --gapplication-service is GLib's own flag (D-Bus service mode); it must
    # reach run(). Everything else is stripped.
    passthrough = [a for a in argv[1:] if a.startswith("--gapplication")]
    return app.run([argv[0]] + passthrough)


if __name__ == "__main__":
    sys.exit(main())
