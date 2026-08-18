#!/usr/bin/env python3
"""Probe: can we capture the screen WITHOUT a permission popup every time?

This is the foundation test for the whole tool. It asks the ScreenCast portal
for a monitor stream with persist_mode=2 ("remember until revoked") and stores
the restore_token it hands back.

Run it twice:
  1st run -> GNOME shows a permission dialog. Click Share.
  2nd run -> must complete with NO dialog. That is what we need to prove.
"""
import json
import os
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".restore_token")
BUS_NAME = "org.freedesktop.portal.Desktop"
OBJ_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"

loop = GLib.MainLoop()
state = {"token": None, "node_id": None, "session": None, "failed": None}
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
sender = bus.get_unique_name()[1:].replace(".", "_")
counter = [0]


def request_path(token):
    return f"{OBJ_PATH}/request/{sender}/{token}"


def call(method, args, on_response):
    """Call a portal method and route its async Response signal to on_response."""
    counter[0] += 1
    token = f"probe{counter[0]}"
    path = request_path(token)

    sub = [None]

    def handler(_c, _s, _o, _i, _sig, params):
        code, results = params.unpack()
        if sub[0] is not None:
            bus.signal_unsubscribe(sub[0])
        if code != 0:
            state["failed"] = f"{method} denied or cancelled (code {code})"
            loop.quit()
            return
        on_response(results)

    sub[0] = bus.signal_subscribe(
        BUS_NAME, "org.freedesktop.portal.Request", "Response", path,
        None, Gio.DBusSignalFlags.NONE, handler,
    )

    opts = dict(args[-1])
    opts["handle_token"] = GLib.Variant("s", token)
    full = tuple(args[:-1]) + (opts,)
    bus.call_sync(
        BUS_NAME, OBJ_PATH, SCREENCAST, method,
        GLib.Variant(sig_for(method), full), None,
        Gio.DBusCallFlags.NONE, -1, None,
    )


def sig_for(method):
    return {
        "CreateSession": "(a{sv})",
        "SelectSources": "(oa{sv})",
        "Start": "(osa{sv})",
    }[method]


def on_started(results):
    streams = results.get("streams") or []
    if not streams:
        state["failed"] = "portal returned no streams"
        loop.quit()
        return
    state["node_id"] = streams[0][0]
    state["token"] = results.get("restore_token")
    loop.quit()


def on_sources_selected(_results):
    call("Start", (state["session"], "", {}), on_started)


def on_session_created(results):
    state["session"] = results["session_handle"]
    opts = {
        "types": GLib.Variant("u", 1),          # 1 = MONITOR
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", 1),    # 1 = HIDDEN
        "persist_mode": GLib.Variant("u", 2),   # 2 = persist until revoked
    }
    saved = load_token()
    if saved:
        opts["restore_token"] = GLib.Variant("s", saved)
    call("SelectSources", (state["session"], opts), on_sources_selected)


def load_token():
    try:
        with open(TOKEN_FILE) as fh:
            return json.load(fh).get("restore_token")
    except (OSError, ValueError):
        return None


def save_token(token):
    if not token:
        return
    with open(TOKEN_FILE, "w") as fh:
        json.dump({"restore_token": token}, fh)
    os.chmod(TOKEN_FILE, 0o600)


def grab_frame(node_id, out_path):
    """Pull exactly one frame off the PipeWire node and write it as PNG."""
    Gst.init(None)
    # OpenPipeWireRemote returns a handle INDEX plus a separate fd list; the
    # real descriptor has to be pulled out of that list.
    reply, fd_list = bus.call_with_unix_fd_list_sync(
        BUS_NAME, OBJ_PATH, SCREENCAST, "OpenPipeWireRemote",
        GLib.Variant("(oa{sv})", (state["session"], {})),
        GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
    )
    fd = fd_list.get(reply.unpack()[0])
    pipeline_desc = (
        f"pipewiresrc fd={fd} path={node_id} num-buffers=1 ! videoconvert ! "
        f"pngenc ! filesink location={out_path}"
    )
    pipeline = Gst.parse_launch(pipeline_desc)
    pipeline.set_state(Gst.State.PLAYING)
    b = pipeline.get_bus()
    msg = b.timed_pop_filtered(
        10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
    )
    pipeline.set_state(Gst.State.NULL)
    if msg is None:
        return "timed out waiting for a frame"
    if msg.type == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        return f"{err} / {dbg}"
    return None


def main():
    had_token = load_token() is not None
    print(f"stored token present : {had_token}")
    print("requesting screen access ...")

    call("CreateSession", ({"session_handle_token": GLib.Variant("s", "probe")},),
         on_session_created)
    GLib.timeout_add_seconds(45, lambda: (loop.quit(), False)[1])
    loop.run()

    if state["failed"]:
        print(f"FAILED: {state['failed']}")
        return 1
    if state["node_id"] is None:
        print("FAILED: no response (dialog was never answered?)")
        return 1

    print(f"got PipeWire node   : {state['node_id']}")
    new_token = state["token"]
    print(f"restore_token given : {bool(new_token)}")
    save_token(new_token)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_frame.png")
    err = grab_frame(state["node_id"], out)
    if err:
        print(f"frame grab FAILED   : {err}")
        return 1
    size = os.path.getsize(out) if os.path.exists(out) else 0
    print(f"frame written       : {out} ({size} bytes)")
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
