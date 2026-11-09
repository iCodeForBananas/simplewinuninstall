"""The wizard: Apps -> Uninstall -> Scan -> Review -> Remove -> Done.

The window, skin and worker plumbing are hudkit's; this file is only the steps.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from hudkit import applog, theme
from hudkit.paths import human_size
from hudkit.platform_win import disk_free
from hudkit.shell import Step, WizardApp, run_app
from hudkit.theme import (
    ACCENT, ACCENT_BRIGHT, BG, LINE, MUTED, OK, PANEL, TEXT,
    TEXT_BRIGHT, WARN, FramedPanel, NeoButton, SegmentBar,
)

from . import APP_NAME, __version__, artifacts, inventory, remover, uninstaller
from .artifacts import (
    FILE, FOLDER, REG_KEY, REG_VALUE, REVIEW, SERVICE, SHORTCUT, SURE, TASK,
    Artifact, ScanReport,
)
from .inventory import InstalledApp

TICKED = "◉"      # filled circle
UNTICKED = "○"    # hollow circle

GROUPS = (
    ("Folders", (FOLDER,)),
    ("Files and shortcuts", (FILE, SHORTCUT)),
    ("Registry", (REG_KEY, REG_VALUE)),
    ("Services and tasks", (SERVICE, TASK)),
)


class App(WizardApp):
    APP_NAME = APP_NAME
    VERSION = __version__
    MODULE = "winuninstall"
    ICON = os.path.join("assets", "simplewinuninstall.ico")
    ELEVATE_DECLINED = (
        "Continuing without Administrator rights.\n\n"
        "Leftovers in Program Files and machine-wide registry keys will be "
        "listed but may not be removable.")
    ORDER = ["apps", "uninstall", "scan", "review", "remove", "done"]

    def init_state(self) -> None:
        self.everyone: List[InstalledApp] = []
        self.target: Optional[InstalledApp] = None
        self.forced = False            # scanning an app that is still installed
        self.report: Optional[ScanReport] = None
        self.selected: set = set()
        self.remove_report: Optional[remover.RemoveReport] = None
        self.use_bin = tk.BooleanVar(value=True)

    def step_classes(self) -> list:
        return [("apps", AppsStep), ("uninstall", UninstallStep),
                ("scan", ScanStep), ("review", ReviewStep),
                ("remove", RemoveStep), ("done", DoneStep)]

    def status_text(self) -> str:
        free, total = disk_free()
        return f"C: {human_size(free)} free of {human_size(total)}" if total else ""

    def start_over(self) -> None:
        self.target = None
        self.forced = False
        self.report = None
        self.selected = set()
        self.remove_report = None
        self.cancel_event.clear()
        self.steps["apps"].request_reload()
        self.show("apps")


def _tree_in(parent, app: App, columns, **kw):
    """A Treeview in a hairline frame with a scrollbar. Returns (wrap, tree)."""
    wrap = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                    highlightbackground=LINE)
    wrap.columnconfigure(0, weight=1)
    wrap.rowconfigure(0, weight=1)
    tree = ttk.Treeview(wrap, columns=columns, **kw)
    tree.grid(row=0, column=0, sticky="nsew")
    bar = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    bar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=bar.set)
    return wrap, tree


# ===========================================================================
# 1. Choose an app
# ===========================================================================

class AppsStep(Step):
    next_text = "Uninstall"
    next_enabled = False
    back_enabled = False
    back_text = "Back"

    COLUMNS = ("name", "publisher", "version", "size", "installed")

    def build(self) -> None:
        pad = self.app.pad
        self.rowconfigure(1, weight=1)

        bar = tk.Frame(self, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, pad))
        bar.columnconfigure(1, weight=1)
        self.key(bar, "Search").grid(row=0, column=0, padx=(0, 10))
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *_a: self._fill())
        entry = ttk.Entry(bar, textvariable=self.query)
        entry.grid(row=0, column=1, sticky="ew", ipady=4)
        self.count = self.label(bar, "", fg=MUTED, font=self.fonts.small)
        self.count.grid(row=0, column=2, padx=(pad, 0))
        self.elevate = NeoButton(bar, "Restart elevated", self.fonts,
                                 self.app.try_elevate, height=34)
        self.elevate.grid(row=0, column=3, padx=(pad, 0))

        wrap, self.tree = _tree_in(self, self.app, self.COLUMNS, show="headings",
                                   selectmode="browse")
        wrap.grid(row=1, column=0, sticky="nsew")
        titles = {"name": "Name", "publisher": "Publisher", "version": "Version",
                  "size": "Size", "installed": "Installed"}
        widths = {"name": 380, "publisher": 220, "version": 120, "size": 90,
                  "installed": 100}
        for col in self.COLUMNS:
            self.tree.heading(col, text=titles[col].upper(),
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=theme.px(widths[col]), anchor="w",
                             stretch=col == "name")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)

        self.apps: List[InstalledApp] = []
        self.by_iid: Dict[str, InstalledApp] = {}
        self.sort_col = "name"
        self.sort_desc = False
        self.loaded = False

    def request_reload(self) -> None:
        self.loaded = False

    def on_show(self) -> None:
        if self.app.admin:
            self.elevate.grid_remove()
        if not self.loaded:
            self.loaded = True
            self._load()
        self.app.set_next_enabled(bool(self.tree.selection()))

    def _load(self) -> None:
        self.count.configure(text="Reading installed apps...")
        app = self.app

        def work() -> None:
            try:
                app.post("win32_ready", inventory.list_win32_apps())
                app.post("store_ready", inventory.list_store_apps())
            except Exception as exc:
                app.log.exception("Could not read installed apps")
                app.post("apps_failed", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def on_win32_ready(self, apps: List[InstalledApp]) -> None:
        self.apps = apps
        self._after_load()

    def on_store_ready(self, apps: List[InstalledApp]) -> None:
        self.apps = self.apps + apps
        self._after_load()

    def on_apps_failed(self, message: str) -> None:
        messagebox.showerror(APP_NAME, f"Could not read installed apps:\n\n{message}")

    def _after_load(self) -> None:
        self.app.everyone = self.apps
        self._fill()

    def _sort_key(self, a: InstalledApp):
        if self.sort_col == "size":
            return a.size_bytes
        if self.sort_col == "installed":
            return a.install_date
        return {"name": a.name, "publisher": a.publisher,
                "version": a.version}[self.sort_col].lower()

    def _sort_by(self, col: str) -> None:
        self.sort_desc = (not self.sort_desc) if self.sort_col == col else False
        self.sort_col = col
        self._fill()

    def _fill(self) -> None:
        wanted = self.query.get().strip().lower()
        chosen = self.tree.selection()
        keep = self.by_iid[chosen[0]].id if chosen else None
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        rows = [a for a in self.apps if wanted in a.name.lower()
                or wanted in a.publisher.lower()]
        rows.sort(key=self._sort_key, reverse=self.sort_desc)
        for index, item in enumerate(rows):
            iid = f"a{index}"
            self.by_iid[iid] = item
            self.tree.insert("", "end", iid=iid, values=(
                item.name, item.publisher, item.version,
                human_size(item.size_bytes) if item.size_bytes else "",
                item.install_date))
            if item.id == keep:
                self.tree.selection_set(iid)
        self.count.configure(text=f"{len(rows)} of {len(self.apps)} apps")
        if self.app.current == "apps":
            self.app.set_next_enabled(bool(self.tree.selection()))

    def _on_select(self, _event=None) -> None:
        if self.app.current == "apps":
            self.app.set_next_enabled(bool(self.tree.selection()))

    def _on_double(self, event) -> None:
        if self.tree.identify_row(event.y):
            self.on_next()

    def on_next(self) -> None:
        chosen = self.tree.selection()
        if not chosen:
            return
        self.app.target = self.by_iid[chosen[0]]
        self.app.forced = False
        self.app.show("uninstall")


# ===========================================================================
# 2. Run its uninstaller
# ===========================================================================

class UninstallStep(Step):
    next_text = "Run uninstaller"

    IDLE, RUNNING, REMOVED, STUCK = range(4)

    def build(self) -> None:
        pad = self.app.pad
        self.rowconfigure(2, weight=1)

        self.title_label = self.label(self, "", fg=TEXT_BRIGHT, font=self.fonts.h1)
        self.title_label.grid(row=0, column=0, sticky="w")

        panel = FramedPanel(self, self.fonts, "Registered with Windows")
        panel.grid(row=1, column=0, sticky="ew", pady=(pad, 0))
        panel.configure(height=theme.px(190))
        panel.body.columnconfigure(1, weight=1)
        self.fields: Dict[str, tk.Label] = {}
        for row, (key, title) in enumerate((
                ("publisher", "Publisher"), ("version", "Version"),
                ("location", "Location"), ("command", "Uninstaller"))):
            self.key(panel.body, title, bg=PANEL).grid(
                row=row, column=0, sticky="nw", padx=(0, pad), pady=3)
            field = tk.Label(panel.body, bg=PANEL, fg=TEXT, anchor="w",
                             justify="left", wraplength=theme.px(720),
                             font=self.fonts.mono if key in ("location", "command")
                             else self.fonts.body)
            field.grid(row=row, column=1, sticky="ew", pady=3)
            self.fields[key] = field
        panel.grid_propagate(True)
        panel._canvas.configure(height=theme.px(170))

        lower = tk.Frame(self, bg=BG)
        lower.grid(row=2, column=0, sticky="new", pady=(pad + 4, 0))
        lower.columnconfigure(0, weight=1)
        self.bar = SegmentBar(lower, bg=BG, segments=56)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.status_label = self.label(lower, "", fg=ACCENT, font=self.fonts.h2,
                                       wraplength=theme.px(880))
        self.status_label.grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.note_label = self.label(lower, "", fg=MUTED, wraplength=theme.px(880))
        self.note_label.grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.alt = NeoButton(lower, "Already gone? Skip to the scan", self.fonts,
                             self._alt_pressed, height=36)
        self.alt.grid(row=3, column=0, sticky="w", pady=(pad, 0))

        self.state = self.IDLE
        self.shown_for: Optional[str] = None
        self.stop_waiting = threading.Event()

    def on_show(self) -> None:
        target = self.app.target
        if target is None:
            return
        if self.shown_for != target.id:
            self.shown_for = target.id
            self._set_state(self.IDLE)
        self.title_label.configure(text=target.name)
        dirs = inventory.app_dirs(target)
        self.fields["publisher"].configure(text=target.publisher or "Unknown")
        self.fields["version"].configure(text=target.version or "Unknown")
        self.fields["location"].configure(
            text=dirs[0] if dirs else "Not recorded")
        self.fields["command"].configure(
            text=f"Remove-AppxPackage {target.package_full_name}"
            if target.source == inventory.STORE
            else uninstaller.command_for(target) or "None registered")

    def _set_state(self, state: int, status: str = "", note: str = "") -> None:
        self.state = state
        self.status_label.configure(text=status)
        self.note_label.configure(text=note)
        app = self.app
        if state == self.IDLE:
            self.bar.stop_pulse()
            self.bar.set(0, 1)
            self.status_label.configure(
                text="Windows will open this app's own uninstaller.", fg=ACCENT)
            self.note_label.configure(
                text="When it finishes, the leftover scan starts from here. "
                     "Nothing is deleted without your say-so.")
            self.alt.set_text("Already gone? Skip to the scan")
            self.alt.grid()
            app.set_next_text("Run uninstaller")
            app.set_next_enabled(True)
            app.btn_back.set_enabled(True)
        elif state == self.RUNNING:
            self.bar.start_pulse()
            self.status_label.configure(fg=ACCENT)
            self.alt.set_text("It finished. Stop waiting")
            self.alt.grid()
            app.set_next_enabled(False)
            app.btn_back.set_enabled(False)
        elif state == self.REMOVED:
            self.bar.stop_pulse()
            self.bar.set(1, 1)
            self.status_label.configure(fg=OK)
            self.alt.grid_remove()
            app.set_next_text("Scan for leftovers")
            app.set_next_enabled(True)
            app.btn_back.set_enabled(True)
        else:
            self.bar.stop_pulse()
            self.bar.set(0, 1)
            self.status_label.configure(fg=WARN)
            self.alt.grid_remove()
            app.set_next_text("Scan anyway")
            app.set_next_enabled(True)
            app.btn_back.set_enabled(True)

    def on_next(self) -> None:
        if self.state == self.IDLE:
            self._run()
        elif self.state == self.REMOVED:
            self.app.forced = False
            self._go_scan()
        elif self.state == self.STUCK:
            self._force_scan()

    def on_back(self) -> None:
        self.shown_for = None
        self.app.show("apps")

    def _alt_pressed(self) -> None:
        if self.state == self.RUNNING:
            self.stop_waiting.set()
        elif self.state == self.IDLE:
            self._force_scan()

    def _force_scan(self) -> None:
        target = self.app.target
        if inventory.still_installed(target) and not messagebox.askyesno(
                APP_NAME,
                f"{target.name} is still registered as installed.\n\n"
                "Scanning now will list its live files as leftovers, and "
                "removing them will break it. Only do this if its uninstaller "
                "is broken.\n\nScan anyway?", icon="warning"):
            return
        self.app.forced = inventory.still_installed(target)
        self._go_scan()

    def _go_scan(self) -> None:
        self.app.steps["scan"].started = False
        self.app.show("scan")

    def _run(self) -> None:
        self.stop_waiting.clear()
        self._set_state(self.RUNNING, "Running the uninstaller...")
        app, target, stop = self.app, self.app.target, self.stop_waiting

        def work() -> None:
            try:
                outcome = uninstaller.run_uninstaller(
                    target, stop, lambda t: app.post("uninstall_status", t))
            except Exception as exc:
                app.log.exception("Uninstaller failed")
                outcome = uninstaller.UninstallOutcome(uninstaller.FAILED, str(exc))
            app.post("uninstall_done", outcome)

        app.run_worker(work)

    def on_uninstall_status(self, text: str) -> None:
        self.note_label.configure(text=text)

    def on_uninstall_done(self, outcome) -> None:
        name = self.app.target.name
        if outcome.state == uninstaller.REMOVED:
            self.app.steps["apps"].request_reload()
            self._set_state(self.REMOVED, f"{name} is uninstalled.",
                            "Now to see what it left behind.")
        elif outcome.state == uninstaller.STILL_INSTALLED:
            self._set_state(self.STUCK, f"{name} is still installed.", outcome.detail)
        else:
            self._set_state(self.STUCK, "The uninstaller did not run.", outcome.detail)


# ===========================================================================
# 3. Scan for leftovers
# ===========================================================================

class ScanStep(Step):
    next_text = "Review findings"
    next_enabled = False
    back_text = "Cancel"

    def build(self) -> None:
        pad = self.app.pad
        self.rowconfigure(3, weight=1)

        head = tk.Frame(self, bg=BG)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.current_label = self.label(head, "", fg=ACCENT, font=self.fonts.h2)
        self.current_label.grid(row=0, column=0, sticky="w")
        self.skip = NeoButton(head, "Skip drive sweep", self.fonts,
                              self._skip_sweep, height=34)
        self.skip.grid(row=0, column=1, sticky="e")

        self.bar = SegmentBar(self, bg=BG, segments=56)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        self.where_label = self.label(self, "", fg=MUTED, font=self.fonts.small)
        self.where_label.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        panel = FramedPanel(self, self.fonts, "Found so far")
        panel.grid(row=3, column=0, sticky="nsew", pady=(pad, 0))
        panel.body.columnconfigure(0, weight=1)
        panel.body.rowconfigure(0, weight=1)
        self.listing = tk.Text(panel.body, height=14)
        theme.style_text_widget(self.listing, self.fonts, mono=True)
        self.listing.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel.body, orient="vertical", command=self.listing.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listing.configure(yscrollcommand=sb.set, state="disabled")

        self.started = False
        self.skip_event = threading.Event()

    def on_show(self) -> None:
        if self.started:
            self.app.set_next_enabled(self.app.report is not None)
            return
        self.started = True
        self.app.report = None
        self.app.selected = set()
        self.skip_event.clear()
        self.skip.set_enabled(True)
        self.listing.configure(state="normal")
        self.listing.delete("1.0", "end")
        self.listing.configure(state="disabled")
        self.where_label.configure(text="")
        self.current_label.configure(text="STARTING")
        self.bar.start_pulse()
        app, target, skip = self.app, self.app.target, self.skip_event

        def work() -> None:
            def progress(kind, *args) -> None:
                app.post("scan_" + kind, *args)
            try:
                everyone = app.everyone or inventory.list_win32_apps()
                report = artifacts.scan(
                    target, everyone, cancel=app.cancel_event, skip_sweep=skip,
                    progress=progress,
                    on_found=lambda a: app.post("scan_found", a))
                app.post("scan_done", report)
            except Exception as exc:
                app.log.exception("Leftover scan failed")
                app.post("scan_failed", str(exc))

        app.run_worker(work)

    def _skip_sweep(self) -> None:
        self.skip_event.set()
        self.skip.set_enabled(False)

    def on_scan_phase(self, name: str, index: int, total: int) -> None:
        self.current_label.configure(text=name.upper())
        if name.startswith("Sweeping"):
            self.bar.start_pulse()
        else:
            self.bar.stop_pulse()
            self.bar.set(index, total)

    def on_scan_sweep(self, where: str, visited: int) -> None:
        self.where_label.configure(text=f"{visited:,} folders   {where[:110]}")

    def on_scan_found(self, item: Artifact) -> None:
        size = human_size(item.size_bytes) if item.size_bytes else ""
        self.listing.configure(state="normal")
        self.listing.insert("end", f"{size:>12}   {item.location}\n")
        self.listing.see("end")
        self.listing.configure(state="disabled")

    def on_scan_done(self, report: ScanReport) -> None:
        log = self.app.log
        for path, why in report.excluded:
            log.info("[scan] left alone: %s (%s)", path, why)
        self.app.report = report
        self.app.selected = {a.id for a in report.artifacts if a.confidence == SURE}
        self.bar.stop_pulse()
        self.bar.set(1, 1)
        self.current_label.configure(text="SCAN COMPLETE")
        self.where_label.configure(
            text=f"{len(report.artifacts)} leftovers, "
                 f"{human_size(report.total_bytes)}, in {report.duration:.0f}s"
                 + ("" if report.sweep_completed else "   (drive sweep skipped)"))
        self.skip.set_enabled(False)
        self.app.btn_back.set_text("Back")
        self.back_text = "Back"
        self.app.set_next_enabled(True)

    def on_scan_failed(self, message: str) -> None:
        self.bar.stop_pulse()
        messagebox.showerror(APP_NAME, f"The scan could not finish:\n\n{message}")
        self.started = False
        self.app.show("apps")

    def on_next(self) -> None:
        self.app.show("review")

    def on_back(self) -> None:
        self.app.cancel_event.set()
        self.bar.stop_pulse()
        self.started = False
        self.back_text = "Cancel"
        self.app.report = None
        self.app.show("uninstall")


# ===========================================================================
# 4. Review
# ===========================================================================

class ReviewStep(Step):
    next_text = "Remove selected"

    def build(self) -> None:
        pad = self.app.pad
        self.rowconfigure(1, weight=3)
        self.rowconfigure(2, weight=1)

        bar = tk.Frame(self, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        bar.columnconfigure(3, weight=1)
        NeoButton(bar, "Recommended", self.fonts,
                  lambda: self._bulk("sure"), height=36
                  ).grid(row=0, column=0, padx=(0, 6))
        NeoButton(bar, "All", self.fonts, lambda: self._bulk("all"),
                  height=36, min_width=90).grid(row=0, column=1, padx=(0, 6))
        NeoButton(bar, "None", self.fonts, lambda: self._bulk("none"),
                  height=36, min_width=90).grid(row=0, column=2)
        self.total_label = tk.Label(bar, text="", bg=BG, fg=ACCENT_BRIGHT,
                                    font=self.fonts.h2)
        self.total_label.grid(row=0, column=3, sticky="e")

        wrap, self.tree = _tree_in(self, self.app, ("size", "note"),
                                   selectmode="browse")
        wrap.grid(row=1, column=0, sticky="nsew")
        self.tree.heading("#0", text="LEFTOVER")
        self.tree.heading("size", text="SIZE")
        self.tree.heading("note", text="NOTE")
        self.tree.column("#0", width=theme.px(680), anchor="w")
        self.tree.column("size", width=theme.px(100), anchor="e", stretch=False)
        self.tree.column("note", width=theme.px(130), anchor="w", stretch=False)
        self.tree.tag_configure("group", foreground=ACCENT, font=self.fonts.h2)
        self.tree.tag_configure("review", foreground=WARN)
        self.tree.tag_configure("on", foreground=TEXT_BRIGHT)
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<<TreeviewSelect>>", self._on_select, add="+")
        self.tree.bind("<space>", self._on_space, add="+")

        panel = FramedPanel(self, self.fonts, "Why it's listed")
        panel.grid(row=2, column=0, sticky="nsew", pady=(pad, 0))
        panel.body.columnconfigure(0, weight=1)
        panel.body.rowconfigure(0, weight=1)
        self.detail = tk.Text(panel.body, height=5)
        theme.style_text_widget(self.detail, self.fonts)
        self.detail.grid(row=0, column=0, sticky="nsew")
        self.detail.configure(state="disabled")

        options = tk.Frame(self, bg=BG)
        options.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(options, variable=self.app.use_bin,
                        text="Send files to the Recycle Bin so this can be undone"
                        ).pack(side="left")
        self.admin_note = tk.Label(options, text="", bg=BG, fg=WARN,
                                   font=self.fonts.small)
        self.admin_note.pack(side="right")

        self.by_iid: Dict[str, Artifact] = {}

    def on_show(self) -> None:
        self._fill()

    def _fill(self) -> None:
        report = self.app.report
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        if report is None:
            return
        counter = 0
        first = None
        for group, kinds in GROUPS:
            rows = [a for a in report.artifacts if a.kind in kinds]
            if not rows:
                continue
            rows.sort(key=lambda a: (a.confidence != SURE, -a.size_bytes,
                                     a.location.lower()))
            gid = f"g{counter}"
            counter += 1
            self.tree.insert("", "end", iid=gid,
                             text="  " + theme.spaced(group.upper()), open=True,
                             values=(human_size(sum(a.size_bytes for a in rows))
                                     if any(a.size_bytes for a in rows) else "", ""),
                             tags=("group",))
            for item in rows:
                iid = f"a{counter}"
                counter += 1
                self.by_iid[iid] = item
                self.tree.insert(gid, "end", iid=iid, text=self._text(item),
                                 values=self._values(item), tags=self._tags(item))
                first = first or iid
        self._update_total()
        if first:
            self.tree.selection_set(first)
        else:
            self._write_detail(None)

    def _text(self, item: Artifact) -> str:
        mark = TICKED if item.id in self.app.selected else UNTICKED
        return f"     {mark}   {item.location}"

    @staticmethod
    def _values(item: Artifact):
        size = human_size(item.size_bytes) if item.size_bytes else ""
        return (size, "CHECK FIRST" if item.confidence == REVIEW else "LEFTOVER")

    def _tags(self, item: Artifact):
        if item.confidence == REVIEW:
            return ("review",)
        return ("on",) if item.id in self.app.selected else ()

    def _toggle(self, iid: str) -> None:
        item = self.by_iid.get(iid)
        if item is None:
            return
        self.app.selected ^= {item.id}
        self.tree.item(iid, text=self._text(item), tags=self._tags(item))
        self._update_total()

    def _on_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) not in ("tree", "cell"):
            return
        if "indicator" in str(self.tree.identify_element(event.x, event.y)):
            return
        iid = self.tree.identify_row(event.y)
        if iid in self.by_iid:
            self._toggle(iid)

    def _on_space(self, _event) -> None:
        for iid in self.tree.selection():
            self._toggle(iid)

    def _on_select(self, _event=None) -> None:
        chosen = self.tree.selection()
        self._write_detail(self.by_iid.get(chosen[0]) if chosen else None)

    def _bulk(self, mode: str) -> None:
        report = self.app.report
        if report is None:
            return
        if mode == "all":
            self.app.selected = {a.id for a in report.artifacts}
        elif mode == "none":
            self.app.selected = set()
        else:
            self.app.selected = {a.id for a in report.artifacts
                                 if a.confidence == SURE}
        self._fill()

    def chosen(self) -> List[Artifact]:
        report = self.app.report
        if report is None:
            return []
        return [a for a in report.artifacts if a.id in self.app.selected]

    def _update_total(self) -> None:
        items = self.chosen()
        self.total_label.configure(
            text=f"{len(items)} selected   {human_size(sum(a.size_bytes for a in items))}")
        self.app.set_next_text(f"Remove {len(items)} item{'s' if len(items) != 1 else ''}"
                               if self.app.report and self.app.report.artifacts
                               else "Finish")
        report = self.app.report
        self.app.set_next_enabled(bool(items) or not (report and report.artifacts))
        needs = sum(1 for a in items if a.needs_admin
                    or "program files" in a.location.lower())
        self.admin_note.configure(
            text=f"{needs} selected need Administrator and may fail."
            if needs and not self.app.admin else "")

    def _write_detail(self, item: Optional[Artifact]) -> None:
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if item is None:
            report = self.app.report
            if report is not None and not report.artifacts:
                d.insert("end", "Nothing was left behind.", "good")
            else:
                d.insert("end", "Pick a row to see why it was listed.", "muted")
        else:
            if item.warning:
                d.insert("end", item.warning + "\n", "warn")
            d.insert("end", item.reason + "\n")
            if item.file_count:
                d.insert("end", f"{item.file_count:,} files. ", "muted")
            d.insert("end", item.kind.capitalize() + ".\n", "muted")
            d.insert("end", item.location, "path")
        d.configure(state="disabled")

    def on_next(self) -> None:
        items = self.chosen()
        if not items:
            self.app.show("done")
            return
        size = human_size(sum(a.size_bytes for a in items))
        how = "moved to the Recycle Bin" if self.app.use_bin.get() \
            else "permanently deleted"
        warn = ""
        if self.app.forced:
            warn = (f"\n\n{self.app.target.name} is STILL INSTALLED. This will "
                    "break it.")
        review = sum(1 for a in items if a.confidence == REVIEW)
        if review:
            warn += f"\n\n{review} of these are marked Check first."
        if messagebox.askyesno(
                APP_NAME,
                f"Remove {len(items)} items ({size})?\n\nFiles will be {how}. "
                "Registry entries are backed up first." + warn,
                icon="warning" if warn else "question"):
            self.app.steps["remove"].started = False
            self.app.show("remove")


# ===========================================================================
# 5. Remove
# ===========================================================================

class RemoveStep(Step):
    next_text = "Working"
    next_enabled = False
    back_enabled = False

    def build(self) -> None:
        self.rowconfigure(2, weight=1)
        self.current_label = self.label(self, "REMOVING", fg=ACCENT,
                                        font=self.fonts.h2)
        self.current_label.grid(row=0, column=0, sticky="w")
        self.bar = SegmentBar(self, bg=BG, segments=56)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.where_label = self.label(self, "", fg=MUTED, font=self.fonts.small,
                                      wraplength=theme.px(880))
        self.where_label.grid(row=2, column=0, sticky="new", pady=(10, 0))
        self.started = False

    def on_show(self) -> None:
        if self.started:
            return
        self.started = True
        app = self.app
        items = app.steps["review"].chosen()
        target = app.target
        recycle = bool(app.use_bin.get())
        forced = app.forced
        others = [o for o in app.everyone if o.id != target.id]
        kids = app.report.children
        other_dirs = [d for o in others for d in inventory.app_dirs(o)]
        child_dirs = [d for c in kids for d in inventory.app_dirs(c)]

        def work() -> None:
            try:
                result = remover.remove(
                    target, items, other_dirs, child_dirs, recycle=recycle,
                    allow_installed=forced, cancel=app.cancel_event,
                    progress=lambda a, i, n: app.post(
                        "remove_progress", a.location, i, n))
                app.post("remove_done", result)
            except Exception as exc:
                app.log.exception("Removal failed")
                app.post("remove_failed", str(exc))

        app.run_worker(work)

    def on_remove_progress(self, location: str, index: int, total: int) -> None:
        self.bar.set(index, total)
        self.where_label.configure(text=location)

    def on_remove_done(self, report) -> None:
        self.app.remove_report = report
        self.app.show("done")

    def on_remove_failed(self, message: str) -> None:
        messagebox.showerror(APP_NAME, f"Removal stopped:\n\n{message}")
        self.app.show("review")


# ===========================================================================
# 6. Done
# ===========================================================================

class DoneStep(Step):
    next_text = "Close"
    back_text = "Uninstall another"

    def build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.headline = self.label(self, "", fg=TEXT_BRIGHT, font=self.fonts.h1)
        self.headline.grid(row=0, column=0, sticky="w")
        panel = FramedPanel(self, self.fonts, "Summary")
        panel.grid(row=1, column=0, sticky="nsew", pady=(self.app.pad, 0))
        panel.body.columnconfigure(0, weight=1)
        panel.body.rowconfigure(0, weight=1)
        self.text = tk.Text(panel.body)
        theme.style_text_widget(self.text, self.fonts)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel.body, orient="vertical", command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=sb.set, state="disabled")

    def on_show(self) -> None:
        report = self.app.remove_report
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if report is None:
            self.headline.configure(text="All clean")
            t.insert("end", "Nothing was removed.\n", "muted")
        else:
            self.headline.configure(
                text=f"Removed {len(report.removed)} of {len(report.results)}"
                     f"   {human_size(report.freed_bytes)}")
            if report.recycled and report.freed_bytes:
                t.insert("end", "Files are in the Recycle Bin. The space comes "
                                "back when you empty it.\n", "muted")
            if report.failed:
                t.insert("end", "COULD NOT REMOVE\n", "h")
                for r in report.failed:
                    t.insert("end", f"{r.artifact.location}\n", "path")
                    t.insert("end", f"    {r.detail}\n", "warn")
            if report.removed:
                t.insert("end", "REMOVED\n", "h")
                for r in report.removed:
                    t.insert("end", f"{r.artifact.location}\n", "path")
            if report.backup_dir and os.path.isdir(report.backup_dir):
                t.insert("end", "REGISTRY BACKUP\n", "h")
                t.insert("end", report.backup_dir + "\n", "path")
        t.insert("end", "FULL LOG\n", "h")
        t.insert("end", applog.log_path() + "\n", "path")
        t.configure(state="disabled")
        self.app.status.configure(text=self.app.status_text())

    def on_next(self) -> None:
        self.app.destroy()

    def on_back(self) -> None:
        self.app.start_over()


def main(classic: bool = False, native_frame: bool = False) -> int:
    return run_app(App, classic=classic, native_frame=native_frame)
