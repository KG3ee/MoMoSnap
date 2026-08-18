# MoMo Snap — region screenshot tool for GNOME Wayland (Snipaste-style)

A lightweight screenshot tool for **Linux GNOME on Wayland**: press a hotkey,
drag a box, draw arrows on it, press `Ctrl+C` — **with no permission popup on
every capture**.

Built because Snipaste, Flameshot, Lightshot and most other screenshot tools
are broken or painful on GNOME Wayland. If your searches for *"Snipaste
alternative Linux"*, *"Flameshot not working Wayland"*, or *"GNOME screenshot
permission dialog every time"* brought you here — this is the fix.

## The problem this solves

On GNOME Wayland, apps cannot read the screen directly. Classic screenshot
tools ask through the `org.freedesktop.portal.Screenshot` API, and GNOME
answers with a **"Share your screen?" permission dialog on every single
capture**. Global hotkeys are also blocked for normal apps under Wayland.
That combination is why Snipaste, Flameshot and friends feel unusable there.

**MoMo Snap uses the ScreenCast portal with a `restore_token` instead**
(`persist_mode=2`). GNOME asks permission **once**, on the very first run.
Every capture after that is instant and silent. The hotkey problem is solved
by letting GNOME itself own the key: you bind a normal GNOME custom shortcut
to launch the tool.

## Features

- **One-time permission** — first run shows GNOME's Share dialog once; never again
- **Region select** — screen dims, crosshair cursor, drag a box
- **Adjust after release** — drag edges/corners to resize (proper resize
  cursors), drag the middle to move, arrow keys to nudge (`Shift` = 10 px)
- **Annotate** — arrow, rectangle, freehand pen, six colours, undo
- **Clipboard-first** — `Enter`/`Ctrl+C` copies; nothing is saved or uploaded
  unless you ask; `Ctrl+S` saves to `~/Pictures/Screenshots` with a notification
- **Correct on HiDPI / fractional scaling** (tested at 125%)
- **Single instance** — mashing the hotkey while a capture is open does nothing
- **Small** — plain Python + GTK4, ~800 lines, no daemon, no tray, no telemetry

## Install (Ubuntu / Debian)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
                 gstreamer1.0-pipewire libnotify-bin
git clone https://github.com/KG3ee/MoMoSnap.git
cd MoMoSnap
./momosnap-run   # first run: click "Share" in GNOME's dialog — once, ever
```

Fedora: `sudo dnf install python3-gobject python3-cairo gtk4 pipewire-gstreamer libnotify`.

### Bind it to a key (this replaces the global-hotkey problem)

```bash
P=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/momosnap/
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$P']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P name 'MoMo Snap'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P command "$PWD/momosnap-run"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P binding 'F1'
```

Change `'F1'` to any key you like (`'Print'`, `'<Super>s'`, ...). You can do the
same thing in **Settings → Keyboard → Custom Shortcuts** if you prefer clicking.
Note that binding `F1` takes it away from "Help" in other apps.

## Usage

| Input | What it does |
|-------|--------------|
| hotkey | Screen dims, crosshair appears. |
| drag | Select the region: press at one corner, release at the other. |
| drag an edge/corner | Resize the box (two-headed arrow cursor). |
| drag the middle | Move the whole box (when no drawing tool is active). |
| drag outside the box | Throw it away and draw a new one. |
| arrow keys | Nudge the box 1 px, `Shift`+arrows = 10 px. |
| toolbar | Pick arrow / rectangle / pen and a colour; click the tool again to go back to move mode. |
| `Enter` or `Ctrl+C` | Copy to clipboard and close. |
| `Ctrl+S` | Save PNG to `~/Pictures/Screenshots` (desktop notification confirms). |
| `Ctrl+Z` | Undo the last drawing. |
| `Esc` / right-click | Step back: first clears the box, second quits. |

## How it works (for the curious)

1. `capture.py` opens a ScreenCast portal session with `persist_mode=2`,
   stores the `restore_token` in `~/.config/momosnap/`, and pulls **one video
   frame** off PipeWire via GStreamer. That frame is the screenshot.
2. `overlay.py` shows it fullscreen under a dim layer, handles
   selection/move/resize/drawing entirely in **image-pixel coordinates** (so
   fractional display scaling cannot desynchronize the crop), and renders the
   final PNG with cairo.
3. The clipboard on Wayland dies with its owning process, so after `Ctrl+C`
   the process hides and lingers until another app takes the clipboard over.

No root, no shell-outs for capture, no screen recording — one frame per press.

## Troubleshooting

- **A permission dialog appears again** — the stored token was revoked
  (Settings → Apps → Screen Sharing, or a GNOME reinstall). Click Share once
  and it is remembered again.
- **Nothing happens on the hotkey** — run `./momosnap-run` from a terminal to
  see the error. Real failures also show a desktop notification.
- **Pasting gives nothing after you closed everything** — some app must take
  the clipboard before the holder exits; a clipboard manager makes this moot.

## Limitations

- GNOME on **Wayland** only (that is the whole point; on X11 just use Flameshot)
- Single monitor for now
- No pin-to-screen, text tool, or line-width control yet — planned

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it.
