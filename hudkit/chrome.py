"""Frameless window: no Windows title bar or border, themed caption buttons.

tkinter's overrideredirect(True) removes the native frame, but on its own it
also breaks four things a window needs. Each is put back deliberately:

  taskbar button   Win32 extended style swapped from tool-window to app-window
  minimize         ShowWindow(SW_MINIMIZE) - Tk refuses to iconify a frameless
                   window, and its usual workaround flickers the frame back in
  moving           pure Tk: the window follows the pointer (never Windows'
                   own move loop - see the note on the drag handlers)
  resizing         a themed grip in the bottom-right corner

Maximize fills the monitor's work area (so the taskbar stays visible) rather
than the whole screen. Double-clicking the header toggles it, and dragging a
maximized window restores it under the cursor, as Windows does.

If any of this fails, the window keeps working: Win32 calls fall back to pure
Tk equivalents, and if setup itself fails the native frame is restored.
--native-frame (and --classic) skip it entirely.
"""
from __future__ import annotations

import re
import tkinter as tk
from typing import Callable, Optional, Tuple

from .platform_win import (
    IS_WINDOWS, make_taskbar_window, minimize_window, toplevel_hwnd,
    work_area,
)
from .theme import (
    ACCENT_DIM, BG, LINE, Banner, CaptionButton, ResizeGrip, px,
)

DRAG_THRESHOLD = 4   # pixels of movement before a press becomes a drag

_GEOM = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")


def _parse_geometry(geom: str) -> Optional[Tuple[int, int, int, int]]:
    m = _GEOM.match(geom or "")
    if not m:
        return None
    w, h, x, y = m.groups()
    # Tk writes "+-1920" for a window on a monitor left of the primary.
    return int(w), int(h), int(x.replace("+", "")), int(y.replace("+", ""))


class FramelessChrome:
    def __init__(self, root: tk.Tk, banner: Banner,
                 on_close: Callable[[], None], log=None) -> None:
        self.root = root
        self.banner = banner
        self.on_close = on_close
        self.log = log
        self.maximized = False
        self._normal_geometry: Optional[str] = None
        self._hwnd: Optional[int] = None
        self._press: Optional[Tuple[int, int]] = None
        self._manual_offset: Optional[Tuple[int, int]] = None
        self._resize_start: Optional[Tuple[int, int, int, int]] = None

        root.overrideredirect(True)

        # A 1px themed outline replaces the Windows border. It brightens when
        # the window has focus, which is the only cue a frameless window has.
        border = max(1, px(1))
        root.configure(highlightthickness=border, highlightbackground=LINE,
                       highlightcolor=ACCENT_DIM)

        # Caption buttons, laid over the banner's top-right corner.
        self.controls = tk.Frame(root, bg=BG)
        self.btn_min = CaptionButton(self.controls, "min", self.minimize)
        self.btn_max = CaptionButton(self.controls, "max", self.toggle_maximize)
        self.btn_close = CaptionButton(self.controls, "close", on_close)
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.pack(side="left")
        self.controls.place(relx=1.0, y=0, anchor="ne")
        banner.reserve_right = px(46) * 3

        self.grip = ResizeGrip(root)
        self.grip.place(relx=1.0, rely=1.0, anchor="se")
        self.grip.bind("<ButtonPress-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_drag)

        banner.bind("<ButtonPress-1>", self._on_press, add="+")
        banner.bind("<B1-Motion>", self._on_motion, add="+")
        banner.bind("<ButtonRelease-1>", self._on_release, add="+")
        banner.bind("<Double-Button-1>", lambda _e: self.toggle_maximize(),
                    add="+")

        # The HWND only exists once the window is mapped.
        root.after(30, self._attach_native)

    # ------------------------------------------------------------------ #
    def _note(self, message: str, *args) -> None:
        if self.log is not None:
            self.log.info("[chrome] " + message, *args)

    def _attach_native(self) -> None:
        if not IS_WINDOWS:
            return
        try:
            self.root.update_idletasks()
            self._hwnd = toplevel_hwnd(self.root)
            if self._hwnd and make_taskbar_window(self._hwnd):
                # Windows only re-reads the taskbar style when the window is
                # shown again.
                self.root.withdraw()
                self.root.after(10, self._reshow)
                self._note("frameless window attached (hwnd=%s)", self._hwnd)
            else:
                self._note("could not set taskbar style; continuing without")
        except Exception as exc:
            self._note("native attach failed: %s", exc)

    def _reshow(self) -> None:
        try:
            self.root.deiconify()
            self.root.after(20, self.root.focus_force)
        except tk.TclError:
            pass

    # ------------------------------------------------------------- min --
    def minimize(self) -> None:
        if self._hwnd and minimize_window(self._hwnd):
            return
        # Fallback: Tk will only iconify a window that has a frame, so put it
        # back for the minimize and remove it again on restore.
        try:
            self.root.overrideredirect(False)
            self.root.iconify()
            self.root.bind("<Map>", self._restore_frameless)
        except tk.TclError as exc:
            self._note("minimize fallback failed: %s", exc)

    def _restore_frameless(self, event) -> None:
        if event.widget is not self.root:
            return
        try:
            if self.root.state() == "normal":
                self.root.unbind("<Map>")
                self.root.overrideredirect(True)
                if self._hwnd:
                    make_taskbar_window(self._hwnd)
        except tk.TclError:
            pass

    # ------------------------------------------------------------- max --
    def _area(self) -> Tuple[int, int, int, int]:
        area = work_area(self._hwnd) if self._hwnd else None
        if area:
            return area
        return (0, 0, self.root.winfo_screenwidth(),
                self.root.winfo_screenheight())

    def toggle_maximize(self) -> None:
        if self.maximized:
            self._restore()
        else:
            self._normal_geometry = self.root.geometry()
            x, y, w, h = self._area()
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self._set_maximized(True)

    def _restore(self) -> None:
        if self._normal_geometry:
            self.root.geometry(self._normal_geometry)
        self._set_maximized(False)

    def _set_maximized(self, value: bool) -> None:
        self.maximized = value
        self.btn_max.set_maximized(value)
        if value:
            self.grip.place_forget()
        else:
            self.grip.place(relx=1.0, rely=1.0, anchor="se")

    # ------------------------------------------------------------ drag --
    # Dragging is done entirely in Tk, by moving the window to follow the
    # pointer. It deliberately does NOT hand the drag to Windows' own move
    # loop (ReleaseCapture + WM_NCLBUTTONDOWN/HTCAPTION), even though that
    # is the usual borderless-window trick: that loop pumps messages,
    # including the timers behind Tk's after() callbacks, so Tcl calls back
    # into Python from inside a ctypes call. _tkinter's thread-state
    # bookkeeping cannot survive that and the process dies with a fatal
    # error - "works great, then crashes". It only appears to work in apps
    # with nothing animating. See selftest: this must not come back.

    def _on_press(self, event) -> None:
        self._press = (event.x_root, event.y_root)
        self._manual_offset = None

    def _on_motion(self, event) -> None:
        if self._press is None:
            return

        if self._manual_offset is not None:
            ox, oy = self._manual_offset
            self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")
            return

        px0, py0 = self._press
        if (abs(event.x_root - px0) < DRAG_THRESHOLD
                and abs(event.y_root - py0) < DRAG_THRESHOLD):
            return

        if self.maximized:
            # Restore under the cursor; offsets come from the new position.
            nx, ny = self._restore_under_cursor(event, px0, py0)
            self._manual_offset = (event.x_root - nx, event.y_root - ny)
        else:
            # Offset from the original press point, so the window does not
            # lag the cursor by the threshold distance.
            self._manual_offset = (px0 - self.root.winfo_x(),
                                   py0 - self.root.winfo_y())

        ox, oy = self._manual_offset
        self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def _on_release(self, _event) -> None:
        self._press = None
        self._manual_offset = None

    def _restore_under_cursor(self, event, px0: int,
                              py0: int) -> Tuple[int, int]:
        """Un-maximize so the cursor keeps its relative spot on the header.
        Returns the window's new top-left."""
        parsed = _parse_geometry(self._normal_geometry or "")
        if not parsed:
            self._restore()
            return self.root.winfo_x(), self.root.winfo_y()
        w, h, _x, _y = parsed
        frac = (px0 - self.root.winfo_rootx()) / max(1, self.root.winfo_width())
        offset_y = py0 - self.root.winfo_rooty()
        nx = int(event.x_root - frac * w)
        ny = int(event.y_root - offset_y)
        self.root.geometry(f"{w}x{h}+{nx}+{ny}")
        self._set_maximized(False)
        return nx, ny

    # ---------------------------------------------------------- resize --
    def _grip_press(self, event) -> None:
        self._resize_start = (event.x_root, event.y_root,
                              self.root.winfo_width(), self.root.winfo_height())

    def _grip_drag(self, event) -> None:
        if self._resize_start is None:
            return
        x0, y0, w0, h0 = self._resize_start
        min_w, min_h = self.root.minsize()
        w = max(int(min_w), w0 + event.x_root - x0)
        h = max(int(min_h), h0 + event.y_root - y0)
        self.root.geometry(f"{w}x{h}")


def centre_on_screen(root: tk.Tk, width: int, height: int,
                     hwnd: Optional[int] = None) -> str:
    """Geometry string placing the window in the middle of the work area."""
    area = work_area(hwnd) if hwnd else None
    if area:
        ax, ay, aw, ah = area
    else:
        ax, ay = 0, 0
        aw, ah = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = min(width, aw), min(height, ah)
    x = ax + max(0, (aw - width) // 2)
    y = ay + max(0, (ah - height) // 2)
    return f"{width}x{height}+{x}+{y}"
