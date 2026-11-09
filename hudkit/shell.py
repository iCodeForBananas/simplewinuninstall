"""The wizard shell every Simple* app shares.

Header, footer, Back/Next, the frameless window, the worker thread and the
queue that carries its results back to the UI thread. An app subclasses
`WizardApp`, lists its steps, and writes `Step` subclasses:

    class MyApp(WizardApp):
        APP_NAME, VERSION, MODULE = "MyApp", "1.0", "myapp"
        ORDER = ["first", "second"]
        def step_classes(self):
            return [("first", FirstStep), ("second", SecondStep)]

All slow work happens on a worker thread; the UI thread only drains a queue,
so the window never freezes and Cancel always responds. A worker calls
`app.post("thing_done", value)` and the visible step's `on_thing_done(value)`
runs on the UI thread.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

from . import applog, theme
from .chrome import FramelessChrome, centre_on_screen
from .platform_win import (
    IS_WINDOWS, enable_dpi_awareness, is_admin, open_in_shell,
    relaunch_as_admin,
)
from .theme import ACCENT, BG, LINK, MUTED, TEXT, Banner, NeoButton, Rule


def resource(relative: str) -> str:
    """Path to a bundled file, from source or from the PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


class WizardApp(tk.Tk):
    APP_NAME = "App"
    VERSION = "0"
    MODULE = ""                # package name, for `python -m` when elevating
    ICON = os.path.join("assets", "simplewinuninstall.ico")
    ORDER: List[str] = []
    SIZE: Tuple[int, int] = (1020, 760)
    MIN_SIZE: Tuple[int, int] = (900, 660)
    ELEVATE_DECLINED = "Continuing without Administrator rights."

    def __init__(self, classic: bool = False,
                 native_frame: bool = False) -> None:
        super().__init__()
        self.title(f"{self.APP_NAME} {self.VERSION}")
        self._set_icon()

        self.log = applog.setup()
        self.admin = is_admin()
        self.classic = classic
        self.chrome: Optional[FramelessChrome] = None
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None

        self.init_state()

        # The skin must never be the reason the app fails to start.
        try:
            self.fonts = theme.init_theme(self) if not classic \
                else theme.init_classic(self)
        except Exception:
            self.log.exception("Theme failed to apply; falling back to default")
            self.classic = True
            self.fonts = theme.init_classic(self)

        # Size the window in design pixels, scaled for this display, centred
        # on screen. Done after the theme so theme.SCALE is known.
        self.geometry(centre_on_screen(self, theme.px(self.SIZE[0]),
                                       theme.px(self.SIZE[1])))
        self.minsize(theme.px(self.MIN_SIZE[0]), theme.px(self.MIN_SIZE[1]))
        self.log.info("Display scale: %.2fx", theme.SCALE)

        self.pad = theme.px(16)
        self.on_scale_known()
        self._build_chrome()

        # Replace the Windows title bar and border with the themed ones.
        # Windows only, skinned only, and never fatal: if it fails, the
        # native frame comes back and the app carries on.
        if IS_WINDOWS and not self.classic and not native_frame:
            try:
                self.chrome = FramelessChrome(self, self.banner,
                                              self._on_close, log=self.log)
            except Exception:
                self.log.exception("Frameless window failed; using native frame")
                self.chrome = None
                try:
                    self.overrideredirect(False)
                except tk.TclError:
                    pass

        self.steps: Dict[str, ttk.Frame] = {}
        for name, cls in self.step_classes():
            frame = cls(self.body, self)
            self.steps[name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.current = self.ORDER[0]
        self.show(self.current)
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------- for subclasses to fill --
    def init_state(self) -> None:
        """Create the app's own variables. Runs before anything is built."""

    def on_scale_known(self) -> None:
        """theme.SCALE and self.pad are now set; steps are not built yet."""

    def step_classes(self) -> list:
        """[(name, StepClass), ...] in the same order as ORDER."""
        raise NotImplementedError

    def status_text(self) -> str:
        """Left-hand footer text, refreshed on every step change."""
        return ""

    # ------------------------------------------------------------- chrome --
    def report_callback_exception(self, exc, val, tb) -> None:
        """Errors inside UI callbacks go to the log, not to stderr.

        Tkinter's default prints to sys.stderr, which is None in a windowed
        exe - so the error report itself raised, and a recoverable bug could
        take the app down. Log it and keep running.
        """
        try:
            self.log.error("Unhandled error in a UI callback",
                           exc_info=(exc, val, tb))
        except Exception:
            pass

    def _set_icon(self) -> None:
        """Window, taskbar and dialog icon. Optional: missing is fine."""
        ico = resource(self.ICON)
        if os.path.exists(ico):
            try:
                self.iconbitmap(default=ico)
            except tk.TclError:
                pass

    def _build_chrome(self) -> None:
        pad = self.pad
        self.configure(background=BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.banner = Banner(self, self.fonts, self.APP_NAME)
        self.banner.grid(row=0, column=0, sticky="ew")

        self.body = tk.Frame(self, bg=BG, padx=pad + 4, pady=pad)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        footer = tk.Frame(self, bg=BG, padx=pad + 4, pady=pad)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)

        Rule(footer, bg=BG, lead=60).grid(row=0, column=0, columnspan=4,
                                          sticky="ew", pady=(0, pad))

        self.status = tk.Label(footer, text=self.status_text(), bg=BG,
                               fg=MUTED, font=self.fonts.small)
        self.status.grid(row=1, column=0, sticky="w")

        self.log_link = tk.Label(footer, text="open log", bg=BG, fg=LINK,
                                 cursor="hand2", font=self.fonts.small)
        self.log_link.grid(row=1, column=1, sticky="e", padx=(0, pad))
        self.log_link.bind("<Button-1>",
                           lambda _e: open_in_shell(applog.log_path()))

        self.btn_back = NeoButton(footer, "Back", self.fonts, self._back,
                                  variant="ghost", min_width=120)
        self.btn_back.grid(row=1, column=2, padx=(0, 8))
        self.btn_next = NeoButton(footer, "Next", self.fonts, self._next,
                                  variant="primary", min_width=180)
        self.btn_next.grid(row=1, column=3)

    # -------------------------------------------------------- navigation --
    def show(self, name: str) -> None:
        self.current = name
        frame = self.steps[name]
        frame.tkraise()
        idx = self.ORDER.index(name) + 1
        self.banner.set_step(f"{idx:02d} / {len(self.ORDER):02d}  {name}")
        self.btn_back.set_text(frame.back_text)
        self.btn_back.set_enabled(frame.back_enabled)
        self.btn_next.set_text(frame.next_text)
        self.btn_next.set_enabled(frame.next_enabled)
        self.status.configure(text=self.status_text())
        frame.on_show()

    def _next(self) -> None:
        self.steps[self.current].on_next()

    def _back(self) -> None:
        self.steps[self.current].on_back()

    def set_next_enabled(self, enabled: bool) -> None:
        self.btn_next.set_enabled(enabled)

    def set_next_text(self, text: str) -> None:
        self.btn_next.set_text(text)

    # ------------------------------------------------------------ workers --
    def run_worker(self, fn) -> None:
        self.cancel_event.clear()
        self.worker = threading.Thread(target=fn, daemon=True)
        self.worker.start()

    def post(self, kind: str, *payload) -> None:
        self.queue.put((kind, payload))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                handler = getattr(self.steps[self.current], f"on_{kind}", None)
                if handler is None:
                    for frame in self.steps.values():
                        handler = getattr(frame, f"on_{kind}", None)
                        if handler:
                            break
                if handler:
                    handler(*payload)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    self.APP_NAME, "Work is still in progress. Stop it and quit?"):
                return
            self.cancel_event.set()
        self.log.info("%s closing", self.APP_NAME)
        self.destroy()

    # --------------------------------------------------------- elevation --
    def try_elevate(self) -> None:
        if self.admin:
            return
        if not IS_WINDOWS:
            messagebox.showinfo(self.APP_NAME,
                                "Elevation is only available on Windows.")
            return
        if relaunch_as_admin(module=self.MODULE):
            self.log.info("Relaunching elevated; this instance is exiting")
            self.destroy()
        else:
            messagebox.showinfo(self.APP_NAME, self.ELEVATE_DECLINED)


class Step(ttk.Frame):
    """One page of the wizard. Override build() and the on_* hooks."""

    next_text = "Next"
    back_text = "Back"
    next_enabled = True
    back_enabled = True

    def __init__(self, parent: tk.Frame, app: WizardApp) -> None:
        super().__init__(parent)
        self.app = app
        self.fonts = app.fonts
        self.configure(style="TFrame")
        self.columnconfigure(0, weight=1)
        self.build()

    # Convenience wrappers so steps read cleanly.
    def label(self, master, text, fg=TEXT, font=None, bg=BG, **kw):
        return tk.Label(master, text=text, bg=bg, fg=fg,
                        font=font or self.fonts.body, justify="left",
                        anchor="w", **kw)

    def key(self, master, text, bg=BG):
        return tk.Label(master, text=theme.spaced(text.upper()), bg=bg,
                        fg=ACCENT, font=self.fonts.label, anchor="w")

    def build(self) -> None: ...
    def on_show(self) -> None: ...
    def on_next(self) -> None: ...

    def on_back(self) -> None:
        idx = self.app.ORDER.index(self.app.current)
        if idx > 0:
            self.app.show(self.app.ORDER[idx - 1])


_FATAL_LOG = None


def _install_crash_recorder() -> None:
    """Make crashes leave evidence.

    A windowed exe has no console, so a hard crash (a fatal error inside
    Python or Tcl) otherwise vanishes without a trace. faulthandler writes
    the Python stack of every thread to fatal.log when that happens. The
    file stays open for the life of the process, as faulthandler requires.
    """
    global _FATAL_LOG
    import faulthandler

    try:
        path = os.path.join(applog.log_dir(), "fatal.log")
        _FATAL_LOG = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=_FATAL_LOG, all_threads=True)
    except Exception:
        pass


def run_app(app_class, classic: bool = False,
            native_frame: bool = False) -> int:
    _install_crash_recorder()
    # Before any Tk window exists, or Windows renders us at 96 DPI and
    # stretches the result, which looks blurry on a scaled display.
    enable_dpi_awareness()
    if not IS_WINDOWS:
        print(f"{app_class.APP_NAME} is a Windows utility. "
              "On other platforms you can still run the self-test.")
    app = app_class(classic=classic, native_frame=native_frame)
    app.mainloop()
    return 0
