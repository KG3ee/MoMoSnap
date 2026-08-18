"""Screen capture for MoMo Snap.

Uses the ScreenCast portal, NOT the Screenshot portal. That choice is the whole
reason this tool works: ScreenCast hands back a `restore_token`, so GNOME asks
permission exactly once. The Screenshot portal pops a dialog on every single
capture, which is what made Snipaste unusable here.
"""
import json
import os

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gst  # noqa: E402

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJ_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"

CONFIG_DIR = os.path.join(
    GLib.get_user_config_dir(), "momosnap"
)
TOKEN_FILE = os.path.join(CONFIG_DIR, "restore_token.json")


class CaptureError(Exception):
    pass


def reset_token():
    """Forget the stored permission token so the next run asks again."""
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def _load_token():
    try:
        with open(TOKEN_FILE) as fh:
            return json.load(fh).get("restore_token")
    except (OSError, ValueError):
        return None


def _save_token(token):
    if not token:
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as fh:
        json.dump({"restore_token": token}, fh)
    os.chmod(TOKEN_FILE, 0o600)


class _PortalSession:
    """Drives the CreateSession -> SelectSources -> Start handshake."""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.loop = GLib.MainLoop()
        self.counter = 0
        self.session = None
        self.node_id = None
        self.token = None
        self.error = None

    def _call(self, method, args, on_response):
        self.counter += 1
        handle = f"momosnap{self.counter}"
        path = f"{OBJ_PATH}/request/{self.sender}/{handle}"
        sub = []

        def handler(_c, _s, _o, _i, _sig, params):
            code, results = params.unpack()
            if sub:
                self.bus.signal_unsubscribe(sub[0])
            if code != 0:
                # code 1 = user cancelled, 2 = ended some other way
                self.error = "cancelled" if code == 1 else f"portal error {code}"
                self.loop.quit()
                return
            on_response(results)

        sub.append(self.bus.signal_subscribe(
            BUS_NAME, "org.freedesktop.portal.Request", "Response", path,
            None, Gio.DBusSignalFlags.NONE, handler,
        ))

        signature = {
            "CreateSession": "(a{sv})",
            "SelectSources": "(oa{sv})",
            "Start": "(osa{sv})",
        }[method]
        opts = dict(args[-1])
        opts["handle_token"] = GLib.Variant("s", handle)
        self.bus.call_sync(
            BUS_NAME, OBJ_PATH, SCREENCAST, method,
            GLib.Variant(signature, tuple(args[:-1]) + (opts,)), None,
            Gio.DBusCallFlags.NONE, -1, None,
        )

    def _on_started(self, results):
        streams = results.get("streams") or []
        if not streams:
            self.error = "portal returned no video stream"
        else:
            self.node_id = streams[0][0]
            self.token = results.get("restore_token")
        self.loop.quit()

    def _on_sources(self, _results):
        self._call("Start", (self.session, "", {}), self._on_started)

    def _on_session(self, results):
        self.session = results["session_handle"]
        opts = {
            "types": GLib.Variant("u", 1),         # monitors
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 1),   # hide pointer in the capture
            "persist_mode": GLib.Variant("u", 2),  # remember until revoked
        }
        saved = _load_token()
        if saved:
            opts["restore_token"] = GLib.Variant("s", saved)
        self._call("SelectSources", (self.session, opts), self._on_sources)

    def open(self, timeout_seconds=300):
        self._call(
            "CreateSession",
            ({"session_handle_token": GLib.Variant("s", "momosnap")},),
            self._on_session,
        )
        GLib.timeout_add_seconds(timeout_seconds, lambda: (self.loop.quit(), False)[1])
        self.loop.run()
        if self.error:
            raise CaptureError(self.error)
        if self.node_id is None:
            raise CaptureError("no response from the screen portal")
        _save_token(self.token)
        return self.node_id

    def pipewire_fd(self):
        reply, fd_list = self.bus.call_with_unix_fd_list_sync(
            BUS_NAME, OBJ_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
        )
        return fd_list.get(reply.unpack()[0])

    def close(self):
        if not self.session:
            return
        try:
            self.bus.call_sync(
                BUS_NAME, self.session, "org.freedesktop.portal.Session",
                "Close", None, None, Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error:
            pass


def _grab_pixbuf(fd, node_id, timeout_seconds=10):
    """Pull one raw RGBA frame off PipeWire. No PNG round-trip, so it stays fast."""
    Gst.init(None)
    desc = (
        f"pipewiresrc fd={fd} path={node_id} num-buffers=1 ! "
        f"videoconvert ! video/x-raw,format=RGBA ! "
        f"appsink name=sink emit-signals=false sync=false max-buffers=1 drop=false"
    )
    pipeline = Gst.parse_launch(desc)
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        sample = sink.emit("try-pull-sample", timeout_seconds * Gst.SECOND)
        if sample is None:
            raise CaptureError("timed out waiting for the screen frame")

        caps = sample.get_caps().get_structure(0)
        width = caps.get_value("width")
        height = caps.get_value("height")

        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise CaptureError("could not read the captured frame")
        try:
            # RGBA rows are 4-byte aligned by construction, so width*4 is the stride.
            data = GLib.Bytes.new(info.data)
        finally:
            buf.unmap(info)

        # Use the buffer's real row stride when the compositor pads rows;
        # assuming width*4 shears the image diagonally on some monitors.
        stride = width * 4
        try:
            gi.require_version("GstVideo", "1.0")
            from gi.repository import GstVideo
            meta = GstVideo.buffer_get_video_meta(buf)
            if meta and meta.stride[0]:
                stride = meta.stride[0]
        except (ImportError, ValueError):
            pass

        return GdkPixbuf.Pixbuf.new_from_bytes(
            data, GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
        )
    finally:
        pipeline.set_state(Gst.State.NULL)


def grab_screen():
    """Return a GdkPixbuf of the current screen, or raise CaptureError."""
    session = _PortalSession()
    try:
        node_id = session.open()
        return _grab_pixbuf(session.pipewire_fd(), node_id)
    finally:
        session.close()
