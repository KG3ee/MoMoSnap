"""Where to put the floating toolbar.

Kept separate from the UI so the awkward cases can be tested on their own.

The order of preference is:
  1. Just BELOW the selection      - the normal case, toolbar never covers your work.
  2. Just ABOVE the selection      - when the selection reaches the bottom edge.
  3. INSIDE the selection, bottom  - when the selection is so tall that neither
                                     outside position fits. This is the full-screen
                                     capture case.
Horizontally the toolbar follows the selection's left edge, then is clamped so it
can never hang off either side of the screen.
"""

GAP = 8          # breathing room between toolbar and selection
MARGIN = 8       # smallest distance the toolbar may sit from a screen edge

BELOW = "below"
ABOVE = "above"
INSIDE = "inside"


def place_toolbar(sel, toolbar, screen):
    """Return (x, y, mode).

    sel, toolbar, screen are (x, y, w, h), (w, h) and (w, h) respectively.
    All values are in the same coordinate space, origin top-left.
    """
    sx, sy, sw, sh = sel
    tw, th = toolbar
    screen_w, screen_h = screen

    # --- vertical ---------------------------------------------------------
    below_y = sy + sh + GAP
    above_y = sy - GAP - th

    if below_y + th + MARGIN <= screen_h:
        y, mode = below_y, BELOW
    elif above_y >= MARGIN:
        y, mode = above_y, ABOVE
    else:
        # Neither side fits: sit inside the selection, near its bottom edge.
        # This is what happens when you grab the entire screen.
        y, mode = sy + sh - th - GAP, INSIDE
        # A selection can be shorter than the toolbar itself, so clamp to the
        # screen rather than trusting the selection's own bounds.
        y = max(MARGIN, min(y, screen_h - th - MARGIN))

    # --- horizontal -------------------------------------------------------
    x = sx
    x = min(x, screen_w - tw - MARGIN)   # never past the right edge
    x = max(MARGIN, x)                   # never past the left edge

    # If the toolbar is wider than the screen there is nothing sensible to do,
    # so pin it to the left margin and let it clip.
    if tw + 2 * MARGIN > screen_w:
        x = MARGIN

    return int(x), int(y), mode
