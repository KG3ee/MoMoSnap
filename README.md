# MoMo Snap

A small region screenshot tool for GNOME Wayland.

Press `F1`, drag a box, draw on it, press `Ctrl+C`.

## Why this exists

Snipaste, Flameshot and Lightshot all fail on GNOME Wayland. They ask the system
for a raw screenshot, and GNOME answers with a permission window every single
time. MoMo Snap asks a different way, so it is allowed **once** and then stays
silent forever.

## How to use it

| Key | What it does |
|-----|--------------|
| `F1` | Start a capture. The screen dims and the cursor becomes a crosshair. |
| drag | Click the first corner, drag, release at the second corner. |
| drag an edge | Resize the box after release. |
| drag outside | Throw the box away and draw a new one. |
| arrow keys | Nudge the box by 1 px (`Shift` = 10 px). |
| `Enter` or `Ctrl+C` | Copy the selection to the clipboard. Nothing is copied before this. |
| `Ctrl+S` | Save to `~/Pictures/Screenshots` (shows a notification). |
| `Ctrl+Z` | Undo the last thing you drew. |
| `Esc` or right-click | First press clears the selection. Second press quits. |

Drawing tools: **arrow**, **rectangle**, **pen**, with six colours.
Nothing is ever saved or copied on its own. You decide.

## Requirements

- GNOME on Wayland
- `python3-gi`, `python3-gi-cairo`, GTK 4, GStreamer, PipeWire

## Layout

    momosnap/capture.py     screen capture through the ScreenCast portal
    momosnap/overlay.py     the dim overlay, selection, toolbar and drawing
    momosnap/placement.py   where the floating toolbar goes
    momosnap/main.py        entry point
    momosnap-run            launcher, this is what F1 calls

## Notes for later

- The first run shows one GNOME permission window. Click Share. It is remembered
  in `~/.config/momosnap/restore_token.json` and never asked again. If capture
  ever fails, the tool deletes the token and asks once more; a real failure
  shows a desktop notification instead of dying silently.
- Pin-on-top is not built yet.
- Text as a drawing tool is not built yet.
- Line width is fixed at 3 px for now.
- Toolbar icons are borrowed stock icons; custom ones would read better.
