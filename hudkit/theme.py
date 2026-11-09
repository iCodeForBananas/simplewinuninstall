"""The look: an original sci-fi HUD skin, built from plain tkinter.

No images, no fonts to ship, no dependencies - everything here is drawn with
Canvas primitives and ttk style options, so the packaged exe stays small and
there is nothing to load from disk at startup.

Design notes, for anyone changing it:

* Colours are the iCodeForBananas Dark terminal scheme, defined in
  palette.py and nowhere else. See that file for what each one means.
* Corners are cut, not rounded. A 10px diagonal on two opposite corners of
  every button and panel is most of the aesthetic on its own.
* Bracket marks at panel corners instead of full borders - it suggests a
  frame without boxing everything in.
* Progress is segmented rather than smooth, which reads as instrumentation.

Everything degrades: `init_theme` is wrapped so a ttk option this Tk build
does not support cannot stop the app from starting, and `--classic` skips
the skin entirely.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, List, Optional

# Re-exported: apps import colours and widgets from one place.
from .palette import *  # noqa: F401,F403
from .palette import (
    ACCENT, ACCENT_BRIGHT, ACCENT_DEEP, ACCENT_DIM, BG, DANGER, DANGER_DEEP,
    GRID, LINE, MUTED, OK, PANEL, PANEL_HI, SELECT_BG, SIGNATURE, TEXT,
    TEXT_BRIGHT, WARN,
)

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_DISPLAY = ["Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold",
            "Segoe UI", "DejaVu Sans", "Arial"]
_BODY = ["Segoe UI", "Bahnschrift Light", "DejaVu Sans", "Arial"]
_MONO = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]

THIN_SPACE = " "


class Fonts:
    """Resolved font objects. Created once the root window exists."""

    def __init__(self, root: tk.Misc) -> None:
        try:
            available = {f.lower() for f in tkfont.families(root)}
        except tk.TclError:
            available = set()

        def pick(prefs: List[str], fallback: str) -> str:
            for name in prefs:
                if name.lower() in available:
                    return name
            return fallback

        self.display_family = pick(_DISPLAY, "Segoe UI")
        self.body_family = pick(_BODY, "Segoe UI")
        self.mono_family = pick(_MONO, "Courier New")

        self.title = tkfont.Font(family=self.display_family, size=17)
        self.h1 = tkfont.Font(family=self.display_family, size=14)
        self.h2 = tkfont.Font(family=self.display_family, size=11)
        self.body = tkfont.Font(family=self.body_family, size=10)
        self.small = tkfont.Font(family=self.body_family, size=9)
        self.mono = tkfont.Font(family=self.mono_family, size=9)
        self.label = tkfont.Font(family=self.display_family, size=9)
        self.button = tkfont.Font(family=self.display_family, size=10)


def spaced(text: str, gap: str = THIN_SPACE) -> str:
    """Fake letter-spacing; tkinter fonts have no tracking option."""
    return gap.join(text)


# ---------------------------------------------------------------------------
# Display scaling
# ---------------------------------------------------------------------------
# Every pixel measurement in this file goes through px(). Without it the
# Canvas-drawn parts stay at 96 DPI sizes while the text around them scales,
# which looks wrong on any high-DPI display.

SCALE = 1.0


def px(n: float) -> int:
    """Scale a design pixel to this display."""
    return max(1, int(round(n * SCALE)))


def _set_scale(root: tk.Misc) -> float:
    global SCALE
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except tk.TclError:
        dpi = 96.0
    SCALE = max(1.0, dpi / 96.0)
    try:
        # Tk sizes point-based fonts off this; without it text is the wrong
        # size once the process is DPI aware.
        root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass
    return SCALE


def alive(widget: tk.Misc) -> bool:
    """True if the widget's Tcl command still exists.

    Canvas callbacks can fire while a widget is being built or torn down, and
    touching it then raises TclError. Everything here checks first.
    """
    try:
        return bool(widget.winfo_exists())
    except (tk.TclError, RuntimeError):
        return False


# ---------------------------------------------------------------------------
# ttk styling
# ---------------------------------------------------------------------------

def _safe(fn: Callable, *args, **kwargs) -> None:
    """Apply a style option, ignoring ones this Tk build does not know."""
    try:
        fn(*args, **kwargs)
    except tk.TclError:
        pass


def init_classic(root: tk.Misc) -> Fonts:
    """Display scaling only, no skin. Used by --classic."""
    _set_scale(root)
    return Fonts(root)


def init_theme(root: tk.Misc) -> Fonts:
    _set_scale(root)
    fonts = Fonts(root)
    style = ttk.Style(root)

    # clam exposes the most colour options of the built-in themes.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    _safe(root.configure, background=BG)

    _safe(style.configure, ".", background=BG, foreground=TEXT,
          fieldbackground=PANEL, bordercolor=LINE, lightcolor=PANEL,
          darkcolor=PANEL, troughcolor=PANEL, focuscolor=ACCENT_DIM,
          font=fonts.body)

    _safe(style.configure, "TFrame", background=BG)
    _safe(style.configure, "Panel.TFrame", background=PANEL)
    _safe(style.configure, "TLabel", background=BG, foreground=TEXT,
          font=fonts.body)
    _safe(style.configure, "Panel.TLabel", background=PANEL, foreground=TEXT)
    _safe(style.configure, "Head.TLabel", background=BG, foreground=TEXT_BRIGHT,
          font=fonts.h1)
    _safe(style.configure, "Sub.TLabel", background=BG, foreground=MUTED,
          font=fonts.small)
    _safe(style.configure, "Key.TLabel", background=BG, foreground=ACCENT,
          font=fonts.label)
    _safe(style.configure, "Warn.TLabel", background=BG, foreground=WARN)
    _safe(style.configure, "Good.TLabel", background=BG, foreground=OK)

    _safe(style.configure, "TCheckbutton", background=BG, foreground=TEXT,
          focuscolor=BG, indicatorcolor=PANEL_HI, font=fonts.body)
    _safe(style.map, "TCheckbutton",
          background=[("active", BG)],
          indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_DIM)],
          foreground=[("disabled", MUTED)])

    _safe(style.configure, "Panel.TCheckbutton", background=PANEL,
          foreground=TEXT, focuscolor=PANEL, indicatorcolor=PANEL_HI,
          font=fonts.body)
    _safe(style.map, "Panel.TCheckbutton",
          background=[("active", PANEL)],
          indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_DIM)],
          foreground=[("disabled", MUTED)])

    _safe(style.configure, "Treeview", background=PANEL, fieldbackground=PANEL,
          foreground=TEXT, bordercolor=LINE, borderwidth=0, rowheight=28,
          font=fonts.body)
    _safe(style.map, "Treeview",
          background=[("selected", SELECT_BG)],
          foreground=[("selected", TEXT_BRIGHT)])
    _safe(style.configure, "Treeview.Heading", background=PANEL_HI,
          foreground=ACCENT, borderwidth=0, relief="flat",
          padding=(10, 7), font=fonts.label)
    _safe(style.map, "Treeview.Heading", background=[("active", LINE)])
    _safe(style.layout, "Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    _safe(style.configure, "TNotebook", background=BG, borderwidth=0,
          tabmargins=(0, 0, 0, 0))
    tab_pad = (px(24), px(12))
    _safe(style.configure, "TNotebook.Tab", background=BG, foreground=MUTED,
          padding=tab_pad, borderwidth=0, font=fonts.label)
    # clam maps a smaller padding onto the *selected* tab, which overrides
    # the configured one - that is why the active tab used to shrink. Pin
    # padding and expand to the same values in every state.
    _safe(style.map, "TNotebook.Tab",
          background=[("selected", PANEL), ("active", PANEL_HI)],
          foreground=[("selected", ACCENT), ("active", TEXT)],
          padding=[("selected", tab_pad), ("active", tab_pad),
                   ("!selected", tab_pad)],
          expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0))])

    for orient in ("Vertical", "Horizontal"):
        _safe(style.configure, f"{orient}.TScrollbar", background=PANEL_HI,
              troughcolor=BG, bordercolor=BG, arrowcolor=ACCENT_DIM,
              darkcolor=PANEL_HI, lightcolor=PANEL_HI, borderwidth=0)
        _safe(style.map, f"{orient}.TScrollbar",
              background=[("active", ACCENT_DIM)])

    _safe(style.configure, "TSpinbox", fieldbackground=PANEL_HI,
          background=PANEL_HI, foreground=TEXT_BRIGHT, arrowcolor=ACCENT,
          bordercolor=LINE, borderwidth=0, insertcolor=ACCENT)
    _safe(style.configure, "TEntry", fieldbackground=PANEL_HI,
          foreground=TEXT_BRIGHT, bordercolor=LINE, insertcolor=ACCENT)

    _safe(style.configure, "TLabelframe", background=BG, bordercolor=LINE,
          borderwidth=1, relief="solid")
    _safe(style.configure, "TLabelframe.Label", background=BG,
          foreground=ACCENT, font=fonts.label)

    _safe(style.configure, "TSeparator", background=LINE)
    _safe(style.configure, "TProgressbar", background=ACCENT,
          troughcolor=PANEL, bordercolor=LINE, lightcolor=ACCENT,
          darkcolor=ACCENT_DIM)

    return fonts


def style_text_widget(widget: tk.Text, fonts: Fonts,
                      mono: bool = False) -> None:
    """Dark-mode a tk.Text and give it the standard tag set."""
    _safe(widget.configure,
          background=PANEL, foreground=TEXT, insertbackground=ACCENT,
          selectbackground=SELECT_BG, selectforeground=TEXT_BRIGHT,
          relief="flat", borderwidth=0, highlightthickness=0,
          padx=14, pady=12, font=fonts.mono if mono else fonts.body,
          wrap="none" if mono else "word")
    widget.tag_configure("h", font=fonts.h2, foreground=ACCENT,
                         spacing1=8, spacing3=4)
    widget.tag_configure("warn", foreground=WARN)
    widget.tag_configure("good", foreground=OK)
    widget.tag_configure("muted", foreground=MUTED)
    widget.tag_configure("path", font=fonts.mono, foreground=MUTED)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def cut_rect(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int,
             cut: int = 10, **kw) -> int:
    """A rectangle with two opposite corners sliced off."""
    points = [
        x0 + cut, y0,
        x1, y0,
        x1, y1 - cut,
        x1 - cut, y1,
        x0, y1,
        x0, y0 + cut,
    ]
    return canvas.create_polygon(points, **kw)


def corner_brackets(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int,
                    size: int = 14, colour: str = ACCENT_DIM,
                    width: int = 1) -> None:
    """Four L-shaped marks suggesting a frame, without drawing one."""
    for (cx, cy, dx, dy) in (
        (x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1),
    ):
        canvas.create_line(cx, cy, cx + dx * size, cy,
                           fill=colour, width=width)
        canvas.create_line(cx, cy, cx, cy + dy * size,
                           fill=colour, width=width)


_LOGO_CACHE = None


def draw_logo(canvas: tk.Canvas, x: float, y: float, size: float,
              with_badge: bool = True) -> None:
    """Draw the banana mark (see logo.py) with Canvas primitives.

    Same geometry as the .ico, so the header and the taskbar icon always
    match. The layout is computed once and reused, because the header
    redraws every animation frame.
    """
    global _LOGO_CACHE
    from . import logo

    if _LOGO_CACHE is None:
        _LOGO_CACHE = logo.layout()
    mark = _LOGO_CACHE

    def flat(points, inset):
        out = []
        for px_, py_ in logo.to_pixels(points, size, x, y, inset):
            out.extend((px_, py_))
        return out

    inset = 0.12 if with_badge else 0.02
    if with_badge:
        canvas.create_polygon(flat(logo.badge(), 0.0), fill=logo.BADGE,
                              outline=logo.BADGE_EDGE,
                              width=max(1, int(size * 0.03)))

    for a, b in mark["lines"]:
        canvas.create_line(flat([a, b], inset), fill=logo.SWEEP,
                           width=max(1, size * 0.055), capstyle="round")
    canvas.create_polygon(flat(mark["body"], inset), fill=logo.BANANA,
                          outline="")
    canvas.create_line(flat(mark["shade"], inset), fill=logo.BANANA_SHADE,
                       width=max(1, size * 0.02), smooth=True)
    canvas.create_line(flat(list(mark["stem"]), inset), fill=logo.STEM,
                       width=max(1, size * 0.045), capstyle="round")
    fx, fy = flat([mark["flower"]], inset)
    fr = max(1, size * 0.022)
    canvas.create_oval(fx - fr, fy - fr, fx + fr, fy + fr, fill=logo.STEM,
                       outline="")
    canvas.create_polygon(flat(mark["glint"], inset), fill=logo.SPARKLE,
                          outline="")
    (dc, dr) = mark["glint_dot"]
    dx, dy = flat([dc], inset)
    rr = max(1, dr * size * (1 - 2 * inset))
    canvas.create_oval(dx - rr, dy - rr, dx + rr, dy + rr, fill=logo.SPARKLE,
                       outline="")


def grid_fill(canvas: tk.Canvas, width: int, height: int, step: int = 26,
              colour: str = GRID) -> None:
    """Faint technical grid, drawn behind everything else."""
    for x in range(0, width + step, step):
        canvas.create_line(x, 0, x, height, fill=colour)
    for y in range(0, height + step, step):
        canvas.create_line(0, y, width, y, fill=colour)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class NeoButton(tk.Canvas):
    """An angular button with a hover glow. Canvas-drawn, so it owes nothing
    to the platform widget set - which is the point."""

    def __init__(self, master: tk.Misc, text: str, fonts: Fonts,
                 command: Optional[Callable[[], None]] = None,
                 variant: str = "ghost", bg: str = BG,
                 min_width: int = 0, height: int = 40) -> None:
        self._fonts = fonts
        self._text = text
        self._variant = variant
        self._command = command
        self._enabled = True
        self._hover = False
        self._bg = bg
        self._pad = px(28)
        height = px(height)
        # Measure what is actually drawn - upper-cased and letter-spaced -
        # not the raw label. Measuring the raw text made every button too
        # narrow for its own caption.
        width = max(px(min_width),
                    fonts.button.measure(self._label()) + self._pad * 2)
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1)
        # NOTE: never name these _w or _h. tkinter stores the widget's own
        # Tcl path name in self._w, and overwriting it makes every later Tk
        # call pass an integer where a window name belongs.
        self._bw, self._bh = width, height
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())
        self._render()

    # -- appearance ------------------------------------------------------
    def _label(self) -> str:
        """The caption exactly as drawn. Width is measured from this."""
        return spaced(self._text.upper())

    def _colours(self):
        if not self._enabled:
            return PANEL, LINE, MUTED
        if self._variant == "primary":
            # The one place the signature amber appears on a control.
            if self._hover:
                return SIGNATURE, SIGNATURE, BG
            return ACCENT_DEEP, ACCENT, ACCENT_BRIGHT
        if self._variant == "danger":
            if self._hover:
                return DANGER_DEEP, DANGER, TEXT_BRIGHT
            return PANEL, DANGER, DANGER
        if self._hover:
            return PANEL_HI, ACCENT, TEXT_BRIGHT
        return PANEL, LINE, TEXT

    def _render(self) -> None:
        if not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        fill, outline, fg = self._colours()
        w, h = self._bw, self._bh
        cut = px(9)
        if self._hover and self._enabled:
            # Cheap glow: a slightly larger, darker shape behind the face.
            cut_rect(self, px(1), px(1), w - px(1), h - px(1), cut,
                     fill=outline, outline="")
        cut_rect(self, px(2), px(2), w - px(2), h - px(2), cut,
                 fill=fill, outline=outline)
        self.create_text(w // 2, h // 2 + 1, text=self._label(),
                         fill=fg, font=self._fonts.button)

    def _resize_to_text(self) -> None:
        self._bw = max(self._bw, self._fonts.button.measure(self._label())
                      + self._pad * 2)
        self.configure(width=self._bw)

    # -- events ----------------------------------------------------------
    def _on_enter(self, _e=None) -> None:
        self._hover = True
        self._render()

    def _on_leave(self, _e=None) -> None:
        self._hover = False
        self._render()

    def _on_press(self, _e=None) -> None:
        if self._enabled:
            self.configure(cursor="hand2")

    def _on_release(self, _e=None) -> None:
        self._fire()

    def _fire(self) -> None:
        if self._enabled and self._command is not None:
            self._command()

    # -- public ----------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._text = text
        self._resize_to_text()
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "")
        self._render()

    def set_command(self, command: Optional[Callable[[], None]]) -> None:
        self._command = command


class CaptionButton(tk.Canvas):
    """Minimize / maximize / close, drawn to match the skin.

    Glyphs are Canvas lines rather than font characters, so they look the
    same whatever fonts are installed. Sized like Windows' own caption
    buttons (46x32 at 100%) so they sit where muscle memory expects.
    """

    KINDS = ("min", "max", "close")

    def __init__(self, master: tk.Misc, kind: str,
                 command: Callable[[], None], bg: str = BG) -> None:
        if kind not in self.KINDS:
            raise ValueError(kind)
        # Never _w/_h: tkinter keeps the widget's path name in self._w.
        self._cw, self._ch = px(46), px(32)
        super().__init__(master, width=self._cw, height=self._ch, bg=bg,
                         highlightthickness=0, bd=0)
        self._kind = kind
        self._command = command
        self._bg = bg
        self._hover = False
        self._maximized = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_release)
        self._render()

    def set_maximized(self, value: bool) -> None:
        self._maximized = bool(value)
        self._render()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._render()

    def _on_release(self, event) -> None:
        # Only fire if the release is still over the button, like Windows.
        if 0 <= event.x < self._cw and 0 <= event.y < self._ch:
            self._command()

    def _render(self) -> None:
        if not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        w, h = self._cw, self._ch
        close = self._kind == "close"

        if self._hover:
            fill = DANGER if close else PANEL_HI
            if close:
                # The cut corner, faced into the window, ties the close
                # button to the rest of the skin's language.
                cut = px(8)
                self.create_polygon(0, 0, w, 0, w, h, cut, h, 0, h - cut,
                                    fill=fill, outline="")
            else:
                self.create_rectangle(0, 0, w, h, fill=fill, outline="")

        if close and self._hover:
            fg = BG
        elif self._hover:
            fg = ACCENT_BRIGHT
        else:
            fg = MUTED

        cx, cy = w / 2, h / 2
        s = px(5)                 # half the glyph size: a 10px glyph at 100%
        lw = max(1, px(1))

        if self._kind == "min":
            self.create_line(cx - s, cy, cx + s, cy, fill=fg, width=lw)
        elif self._kind == "max":
            if self._maximized:
                o = px(2)         # restore glyph: two overlapping squares
                self.create_line(cx - s + o, cy - s, cx + s, cy - s,
                                 cx + s, cy + s - o, fill=fg, width=lw)
                self.create_rectangle(cx - s, cy - s + o, cx + s - o, cy + s,
                                      outline=fg, width=lw)
            else:
                self.create_rectangle(cx - s, cy - s, cx + s, cy + s,
                                      outline=fg, width=lw)
        else:
            self.create_line(cx - s, cy - s, cx + s, cy + s, fill=fg, width=lw)
            self.create_line(cx - s, cy + s, cx + s, cy - s, fill=fg, width=lw)


class ResizeGrip(tk.Canvas):
    """Bottom-right resize handle: three diagonal hairlines."""

    def __init__(self, master: tk.Misc, bg: str = BG) -> None:
        self._gs = px(16)
        super().__init__(master, width=self._gs, height=self._gs, bg=bg,
                         highlightthickness=0, bd=0, cursor="size_nw_se")
        self._hover = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._render()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._render()

    def _render(self) -> None:
        if not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        s = self._gs
        colour = ACCENT if self._hover else ACCENT_DIM
        lw = max(1, px(1))
        for step in (4, 8, 12):
            d = px(step)
            self.create_line(s - d, s - px(2), s - px(2), s - d,
                             fill=colour, width=lw)


class SegmentBar(tk.Canvas):
    """Segmented progress. Reads as instrumentation rather than a download."""

    def __init__(self, master: tk.Misc, bg: str = BG, height: int = 16,
                 segments: int = 44) -> None:
        super().__init__(master, height=px(height), bg=bg,
                         highlightthickness=0, bd=0)
        self._segments = segments
        self._height = px(height)
        self._value = 0.0
        self._pulse_on = False
        self._pulse_pos = 0
        self.bind("<Configure>", lambda _e: self._render())

    def set(self, value: float, maximum: float) -> None:
        self._value = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
        self._render()

    def start_pulse(self) -> None:
        if not self._pulse_on:
            self._pulse_on = True
            self._tick()

    def stop_pulse(self) -> None:
        self._pulse_on = False
        self._render()

    def _tick(self) -> None:
        if not self._pulse_on:
            return
        self._pulse_pos = (self._pulse_pos + 1) % self._segments
        self._render()
        try:
            self.after(60, self._tick)
        except tk.TclError:
            self._pulse_on = False

    def _render(self) -> None:
        if not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width() or 600
        h = self._height
        gap = px(3)
        seg_w = max(2.0, (w - gap * (self._segments - 1)) / self._segments)
        filled = int(self._value * self._segments)
        for i in range(self._segments):
            x0 = i * (seg_w + gap)
            x1 = x0 + seg_w
            if i < filled:
                colour = ACCENT
            elif i == filled and self._value > 0:
                colour = ACCENT_DIM
            elif self._pulse_on and abs(i - self._pulse_pos) < 3:
                colour = ACCENT_DIM
            else:
                colour = PANEL_HI
            self.create_rectangle(x0, px(2), x1, h - px(2), fill=colour,
                                  outline="")


class Banner(tk.Canvas):
    """The header: grid, wordmark, step counter and a travelling scan line."""

    def __init__(self, master: tk.Misc, fonts: Fonts, title: str,
                 height: int = 64) -> None:
        super().__init__(master, height=px(height), bg=BG,
                         highlightthickness=0, bd=0)
        self._fonts = fonts
        self._title = title
        self._step = ""
        self._h = px(height)
        self._sweep = 0
        self._alive = True
        # Pixels kept clear on the right for the window's caption buttons,
        # which sit on top of the banner when the native frame is removed.
        self.reserve_right = 0
        self.bind("<Configure>", lambda _e: self._render())
        # Deferred: during __init__ the canvas is not realised yet, and
        # drawing into it raises TclError on some Tk builds.
        self.after_idle(self._animate)

    def set_step(self, text: str) -> None:
        self._step = text
        self._render()

    def destroy(self) -> None:  # stop the animation cleanly
        self._alive = False
        super().destroy()

    def _animate(self) -> None:
        if not self._alive or not alive(self):
            return
        w = self.winfo_width() or 900
        self._sweep = (self._sweep + px(11)) % (w + px(260))
        self._render()
        try:
            self.after(45, self._animate)
        except tk.TclError:
            self._alive = False

    def _render(self) -> None:
        if not self._alive or not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width() or 900
        h = self._h

        grid_fill(self, w, h, step=px(24))

        mid = (h - px(6)) // 2   # vertical centre, above the base rule

        # Logo mark and wordmark
        mark = px(38)
        draw_logo(self, px(14), mid - mark / 2, mark)
        self.create_text(px(14) + mark + px(14), mid, anchor="w",
                         text=spaced(self._title.upper()),
                         fill=TEXT_BRIGHT, font=self._fonts.title)
        if self._step:
            self.create_text(w - self.reserve_right - px(20), mid, anchor="e",
                             text=spaced(self._step.upper()),
                             fill=ACCENT_DIM, font=self._fonts.label)

        # Base rule plus the travelling highlight.
        y = h - px(6)
        self.create_line(0, y, w, y, fill=LINE)
        tail = px(130)
        x = self._sweep - tail
        if -tail < x < w:
            # Cyan tail with a hot head, so the motion reads directionally.
            self.create_line(max(0, x), y, min(w, x + tail), y,
                             fill=ACCENT_DIM, width=px(2))
            self.create_line(max(0, x + tail * 0.74), y, min(w, x + tail), y,
                             fill=SIGNATURE, width=px(2))


class Rule(tk.Canvas):
    """A hairline with a short bright lead-in, used to head a section."""

    def __init__(self, master: tk.Misc, bg: str = BG, lead: int = 40) -> None:
        super().__init__(master, height=px(3), bg=bg, highlightthickness=0,
                         bd=0)
        self._lead = px(lead)
        self.bind("<Configure>", lambda _e: self._render())

    def _render(self) -> None:
        if not alive(self):
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width() or 400
        y = px(1)
        self.create_line(0, y, w, y, fill=LINE)
        self.create_line(0, y, self._lead, y, fill=ACCENT, width=px(2))


class FramedPanel(tk.Frame):
    """A dark surface with corner brackets. Put normal widgets inside `body`."""

    def __init__(self, master: tk.Misc, fonts: Fonts, title: str = "",
                 pad: int = 16) -> None:
        super().__init__(master, bg=BG, highlightthickness=0, bd=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._fonts = fonts
        if title:
            head = tk.Frame(self, bg=BG)
            head.grid(row=0, column=0, sticky="ew")
            head.columnconfigure(1, weight=1)
            tk.Label(head, text=spaced(title.upper()), bg=BG, fg=ACCENT,
                     font=fonts.label).grid(row=0, column=0, sticky="w")
            Rule(head, bg=BG, lead=24).grid(row=1, column=0, columnspan=2,
                                            sticky="ew", pady=(4, 8))

        self._canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, bd=0)
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self.body = tk.Frame(self._canvas, bg=PANEL)
        self._pad = px(pad)
        self._window = self._canvas.create_window(
            (self._pad, self._pad), window=self.body, anchor="nw")
        self._canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, event) -> None:
        if not alive(self._canvas):
            return
        pad = self._pad
        try:
            self._canvas.coords(self._window, pad, pad)
            self._canvas.itemconfigure(self._window,
                                       width=max(1, event.width - pad * 2),
                                       height=max(1, event.height - pad * 2))
            # Redraw the brackets on top of the resized surface.
            self._canvas.delete("frame")
            corner_brackets(self._canvas, px(3), px(3),
                            event.width - px(3), event.height - px(3),
                            size=px(13), colour=ACCENT_DIM, width=px(1))
            for item in self._canvas.find_all():
                if item != self._window:
                    self._canvas.addtag_withtag("frame", item)
        except tk.TclError:
            return
