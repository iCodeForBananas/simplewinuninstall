"""Colour tokens - iCodeForBananas Dark. The only file with hex values in it.

Taken from https://github.com/iCodeForBananas/itermcolors (MIT). Values marked
"derived" are shades mixed toward the background for surfaces and hairlines,
which a terminal scheme has no equivalent for; everything else is used exactly
as published.

Colour carries meaning, so pick a token by what it says, not how it looks:
cyan means you can click it, yellow means stop and read, and the one hot amber
accent (SIGNATURE, the scheme's cursor colour) is reserved for the live element
on screen - the sweep line and the primary action.

Imports nothing, so tools/make_icon.py can use it without a display.
"""

# Surfaces
BG = "#0F1114"           # Background
PANEL = "#16181C"        # derived: raised surface
PANEL_HI = "#1B1E23"     # Cursor guide - hover / active surface
LINE = "#252A31"         # derived: hairlines
GRID = "#131519"         # derived: background grid

# Interactive
ACCENT = "#56D4DD"       # ANSI cyan - anything interactive
ACCENT_BRIGHT = "#76E3EA"  # ANSI bright cyan
ACCENT_DIM = "#0D7478"   # the light scheme's cyan, used here as the dim state
ACCENT_DEEP = "#102A2E"  # derived: cyan mixed to the background, for fills

SIGNATURE = "#FFCC00"    # Cursor - the one hot accent, used sparingly

# Text
TEXT = "#E6E8EB"         # Foreground
TEXT_BRIGHT = "#FFFFFF"  # Bold
MUTED = "#98A2B0"        # ANSI black, which in this scheme is the grey
LINK = "#A5D6FF"         # Link

# Status
WARN = "#E3B341"         # ANSI yellow - anything with a tradeoff
DANGER = "#FF8A82"       # Badge / ANSI red
DANGER_DEEP = "#3A1512"  # derived: red mixed to the background, for fills
OK = "#6FD28A"           # ANSI green

SELECT_BG = "#27456B"    # Selection
SELECT_FG = "#E6E8EB"    # Selected text

# Logo-only
BANANA_TIP = "#8A5A00"   # the light scheme's yellow; dark enough for the tips
