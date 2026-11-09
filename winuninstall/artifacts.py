"""Find what an app left behind. Read-only: nothing here changes anything.

Where a candidate is found decides how much it is trusted:

  SURE    ticked by default. The registry entry named it, the catalog knows
          it, or its exact name sits in a place apps keep their own folders.
  REVIEW  listed unticked. Partial name matches, anything from the whole-drive
          sweep, anything in your personal folders, anything holding games.

Every folder then has to get past guards.vet_folder. See guards.py for what
can never be listed at all.
"""
from __future__ import annotations

import csv
import io
import os
import struct
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Dict, List, Optional, Set, Tuple

from hudkit.paths import key, sysdrive, userprofile, within
from hudkit.platform_win import IS_WINDOWS, run_command

from . import catalog, guards
from .inventory import (
    STORE, InstalledApp, app_dirs, children_of, hive_handle,
    icon_path, read_value, split_command, view_flag,
)
from .matching import (
    EXACT, PREFIX, clean_name, compact, exe_token, is_guid, match_name,
    name_tokens, publisher_folder, publisher_token, usable,
)

try:
    import winreg
except ImportError:  # pragma: no cover - not Windows
    winreg = None  # type: ignore[assignment]

SURE = "sure"
REVIEW = "review"

FOLDER = "folder"
FILE = "file"
SHORTCUT = "shortcut"
REG_KEY = "registry key"
REG_VALUE = "registry value"
SERVICE = "service"
TASK = "scheduled task"

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SWEEP_MIN_TOKEN = 5

# A direct subfolder with one of these names means the folder holds things a
# person would miss: browser profiles, game saves.
_USER_DATA_MARKERS = ("profiles", "user data", "saves", "savegames",
                      "saved games", "save games")

# Never descended into during the whole-drive sweep.
_SWEEP_SKIP_NAMES = {
    "windows", "$recycle.bin", "system volume information", "recovery",
    "perflogs", "config.msi", "$winreagent", "$windows.~bt", "$windows.~ws",
    "windows.old", "windowsapps", "node_modules", "__pycache__", ".git",
    ".svn", ".hg", ".venv", "venv", "site-packages", ".gradle", ".m2",
    ".nuget", ".cargo", ".rustup", "onedrive",
}


@dataclass(frozen=True)
class RegRoot:
    hive: str
    path: str
    view: int = 0


@dataclass
class ScanRoots:
    """Where to look. A parameter so the self-test can point it at a sandbox."""
    app_homes: List[str] = field(default_factory=list)
    temp: List[str] = field(default_factory=list)
    dot_homes: List[str] = field(default_factory=list)
    personal: List[str] = field(default_factory=list)
    shortcut_dirs: List[str] = field(default_factory=list)
    registry: List[RegRoot] = field(default_factory=list)
    sweep_root: str = ""
    sweep_skip: List[str] = field(default_factory=list)
    evidence: bool = True          # services, Run keys, classes, tasks

    @classmethod
    def for_this_machine(cls, sweep: bool = True) -> "ScanRoots":
        env = os.environ.get
        prof = userprofile()
        roaming = env("APPDATA", os.path.join(prof, "AppData", "Roaming"))
        local = env("LOCALAPPDATA", os.path.join(prof, "AppData", "Local"))
        data = env("ProgramData", r"C:\ProgramData")
        pf = env("ProgramW6432") or env("ProgramFiles", r"C:\Program Files")
        pf86 = env("ProgramFiles(x86)", r"C:\Program Files (x86)")
        public = env("PUBLIC", r"C:\Users\Public")
        common_start = os.path.join(data, r"Microsoft\Windows\Start Menu\Programs")
        user_start = os.path.join(roaming, r"Microsoft\Windows\Start Menu\Programs")
        users_root = os.path.dirname(prof)
        other_users = []
        try:
            for entry in os.scandir(users_root):
                if entry.is_dir(follow_symlinks=False) and key(entry.path) not in (
                        key(prof), key(public)):
                    other_users.append(entry.path)
        except OSError:
            pass
        return cls(
            app_homes=[
                pf, pf86, os.path.join(pf, "Common Files"),
                os.path.join(pf86, "Common Files"),
                data, roaming, local, os.path.join(local, "Programs"),
                os.path.join(prof, "AppData", "LocalLow"),
            ],
            temp=[env("TEMP", os.path.join(local, "Temp"))],
            dot_homes=[prof],
            personal=[
                os.path.join(prof, "Documents"),
                os.path.join(prof, "Documents", "My Games"),
                os.path.join(prof, "OneDrive", "Documents"),
                os.path.join(prof, "OneDrive", "Documents", "My Games"),
                os.path.join(prof, "Saved Games"),
            ],
            shortcut_dirs=[
                user_start, common_start,
                os.path.join(user_start, "Startup"),
                os.path.join(common_start, "Startup"),
                os.path.join(prof, "Desktop"),
                os.path.join(prof, "OneDrive", "Desktop"),
                os.path.join(public, "Desktop"),
                os.path.join(roaming, r"Microsoft\Internet Explorer\Quick Launch"),
                os.path.join(roaming, r"Microsoft\Internet Explorer\Quick Launch"
                                      r"\User Pinned\TaskBar"),
            ],
            registry=[RegRoot("HKCU", "Software", 0),
                      RegRoot("HKLM", "SOFTWARE", 64),
                      RegRoot("HKLM", "SOFTWARE", 32)],
            sweep_root=(sysdrive() + os.sep) if sweep else "",
            sweep_skip=[
                os.path.join(local, "Microsoft"),
                os.path.join(local, "Packages"),
                os.path.join(data, "Microsoft"),
                os.path.join(data, "Package Cache"),
                *other_users,
            ],
        )


@dataclass
class Artifact:
    kind: str
    location: str
    reason: str
    confidence: str = SURE
    warning: str = ""
    size_bytes: int = 0
    file_count: int = 0
    # Registry-backed kinds
    hive: str = ""
    reg_path: str = ""
    view: int = 0
    value: str = ""
    # Services and tasks
    name: str = ""
    needs_admin: bool = False

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.location}".lower()


@dataclass
class ScanReport:
    app: InstalledApp
    artifacts: List[Artifact] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)
    children: List[InstalledApp] = field(default_factory=list)
    sweep_completed: bool = False
    duration: float = 0.0

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)


def measure(path: str, cancel: Optional[Event] = None) -> Tuple[int, int]:
    """(bytes, files) under a folder, never crossing a junction or symlink."""
    total = count = 0
    stack = [path]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if getattr(info, "st_file_attributes", 0) \
                                & _FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += info.st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def shortcut_target(path: str) -> str:
    """Where a .lnk points, from the Shell Link binary format. "" if unknown.

    Only the local path in LinkInfo is read; no COM, no shell involvement.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(65536)
        if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", data, 20)[0]
        offset = 76
        if flags & 0x01:                    # HasLinkTargetIDList
            offset += 2 + struct.unpack_from("<H", data, offset)[0]
        if not flags & 0x02:                # HasLinkInfo
            return ""
        base = offset + struct.unpack_from("<I", data, offset + 16)[0]
        end = data.index(b"\0", base)
        return data[base:end].decode("mbcs" if os.name == "nt" else "latin-1",
                                     errors="replace")
    except (OSError, ValueError, struct.error):
        return ""


def _list_dir(path: str):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


class _Scan:
    def __init__(self, app: InstalledApp, everyone: List[InstalledApp],
                 roots: ScanRoots, cancel: Optional[Event],
                 skip_sweep: Optional[Event],
                 progress: Optional[Callable], on_found: Optional[Callable]):
        self.app = app
        self.roots = roots
        self.cancel = cancel or Event()
        self.skip_sweep = skip_sweep or Event()
        self.progress = progress or (lambda *_a: None)
        self.on_found = on_found or (lambda _a: None)
        self.report = ScanReport(app=app)
        self.family = catalog.family_for(clean_name(app.name))
        self.seen: Dict[str, Artifact] = {}

        others = [o for o in everyone if o.id != app.id and o.source != STORE]
        kids = children_of(app, everyone)
        self.report.children = kids
        self.child_dirs = [d for c in kids for d in app_dirs(c)]
        self.other_dirs = [d for o in others for d in app_dirs(o)]
        self.same_publisher = any(
            publisher_token(o.publisher) == publisher_token(app.publisher)
            for o in others if publisher_token(o.publisher))

        self.strong = self._strong_tokens()
        self.weak = self._weak_tokens() - self.strong
        self.publisher = publisher_token(app.publisher)
        self.vendor = publisher_folder(app.publisher)
        self.other_tokens: Dict[str, str] = {}
        for other in others:
            for token in name_tokens(other.name, other.publisher):
                self.other_tokens.setdefault(token, other.name)

        self.declared = list(app_dirs(app))
        self.markers = self.family.keep_markers if self.family else ()

    # -------------------------------------------------------------- tokens --
    def _strong_tokens(self) -> Set[str]:
        app = self.app
        tokens = set(name_tokens(app.name, app.publisher))
        if app.reg_key and not is_guid(app.reg_key):
            token = compact(clean_name(app.reg_key))
            if usable(token, whole_name=True):
                tokens.add(token)
        for d in app_dirs(app):
            token = compact(clean_name(os.path.basename(d)))
            if usable(token):
                tokens.add(token)
        if self.family:
            tokens |= {compact(a) for a in self.family.aliases}
        return tokens

    def _weak_tokens(self) -> Set[str]:
        out = set()
        app = self.app
        for exe in (split_command(os.path.expandvars(app.uninstall_string))[0],
                    icon_path(os.path.expandvars(app.display_icon))):
            if exe.lower().endswith(".exe"):
                token = exe_token(os.path.splitext(os.path.basename(exe))[0])
                if token:
                    out.add(token)
        return out

    # ---------------------------------------------------------------- adds --
    def _emit(self, artifact: Artifact) -> None:
        current = self.seen.get(artifact.id)
        if current is not None:
            # The same thing found twice keeps the stronger evidence.
            if current.confidence == REVIEW and artifact.confidence == SURE \
                    and not current.warning:
                current.confidence = SURE
                current.reason = artifact.reason
            return
        self.seen[artifact.id] = artifact
        self.report.artifacts.append(artifact)
        self.on_found(artifact)

    def covered(self, path: str) -> bool:
        """Already inside a folder that will be ticked for removal."""
        return any(a.kind == FOLDER and a.confidence == SURE
                   and within(path, a.location) and key(path) != key(a.location)
                   for a in self.report.artifacts)

    def collision(self, name: str) -> str:
        """The other installed app that also claims this name, if any."""
        own = compact(self.app.reg_key)
        for token in (compact(name), compact(clean_name(name))):
            if token and token != own and token in self.other_tokens:
                return self.other_tokens[token]
        return ""

    def add_folder(self, path: str, reason: str, confidence: str = SURE) -> None:
        verdict, why = guards.vet_folder(path, self.other_dirs, self.child_dirs)
        if verdict == guards.BLOCK:
            self.report.excluded.append((path, why))
            return
        if self.covered(path):
            return
        warning = why if verdict == guards.ASK else ""

        if self.markers and os.path.isdir(path):
            present = self.markers == ("*",) or any(
                os.path.isdir(os.path.join(path, m)) for m in self.markers)
            if present:
                warning = self.family.keep_note
        if not warning and os.path.isdir(path) and any(
                os.path.isdir(os.path.join(path, m)) for m in _USER_DATA_MARKERS):
            warning = "Looks like it holds your profile or saved data."
        rival = self.collision(os.path.basename(path))
        if rival:
            warning = f"Also matches {rival}, which is still installed."
        if warning:
            confidence = REVIEW

        size, files = measure(path, self.cancel)
        self._emit(Artifact(FOLDER, path, reason, confidence, warning, size, files))

    def add_file(self, path: str, kind: str, reason: str,
                 confidence: str = SURE) -> None:
        verdict, why = guards.vet_file(path, self.other_dirs)
        if verdict == guards.BLOCK:
            self.report.excluded.append((path, why))
            return
        if self.covered(path):
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        rival = self.collision(os.path.splitext(os.path.basename(path))[0])
        warning = f"Also matches {rival}, which is still installed." if rival else ""
        self._emit(Artifact(kind, path, reason, REVIEW if rival else confidence,
                            warning, size, 1))

    def add_reg_key(self, hive: str, path: str, view: int, reason: str,
                    confidence: str = SURE) -> None:
        verdict, why = guards.vet_registry(hive, path)
        display = _reg_display(hive, path, view)
        if verdict == guards.BLOCK:
            self.report.excluded.append((display, why))
            return
        rival = self.collision(path.rsplit("\\", 1)[-1])
        warning = f"Also matches {rival}, which is still installed." if rival else ""
        self._emit(Artifact(REG_KEY, display, reason,
                            REVIEW if rival else confidence, warning, hive=hive,
                            reg_path=path, view=view,
                            needs_admin=hive == "HKLM"))

    # -------------------------------------------------------------- phases --
    def phase_declared(self) -> None:
        name = self.app.name
        for path in self.declared:
            if os.path.isdir(path):
                self.add_folder(path, f"Where the registry says {name} was installed.")
        if self.family:
            for raw in self.family.paths:
                path = os.path.expandvars(raw)
                if "%" not in path and os.path.isdir(path):
                    self.add_folder(path, f"A known {name} data location.")

    def _match_children(self, base: str, label: str,
                        files_too: bool = True) -> None:
        """Compare everything directly inside `base` against the app's names."""
        for entry in _list_dir(base):
            if self.cancel.is_set():
                return
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                self._consider_dir(entry.path, entry.name, label)
            elif files_too and match_name(os.path.splitext(entry.name)[0],
                                          self.strong, allow_prefix=False):
                self.add_file(entry.path, FILE,
                              f"File named after the app in {label}.", REVIEW)

    def _consider_dir(self, path: str, name: str, label: str) -> None:
        how = match_name(name, self.strong)
        if how == EXACT:
            self.add_folder(path, f"Named exactly after the app, in {label}.")
            return
        if how == PREFIX:
            if compact(name) not in self.other_tokens:
                self.add_folder(path, f"Name starts with the app's name, in {label}.",
                                REVIEW)
            return
        if match_name(name, self.weak, allow_prefix=False):
            self.add_folder(path, f"Same name as the app's program file, in {label}.",
                            REVIEW)

    def phase_known_locations(self) -> None:
        for base in self.roots.app_homes:
            if self.cancel.is_set():
                return
            label = _label(base)
            self._match_children(base, label)
            # Vendor\Product layouts, e.g. %LOCALAPPDATA%\Valve\Steam
            if self.vendor:
                for entry in _list_dir(base):
                    if compact(entry.name) == self.vendor \
                            and entry.is_dir(follow_symlinks=False):
                        self._match_children(entry.path, f"{label}\\{entry.name}",
                                             files_too=False)
                        self._consider_publisher(entry.path, label)

        for base in self.roots.temp:
            self._match_children(base, "the temp folder")

        for base in self.roots.dot_homes:
            for entry in _list_dir(base):
                if entry.name.startswith(".") \
                        and entry.is_dir(follow_symlinks=False) \
                        and entry.name.lower() != ".git":
                    self._consider_dir(entry.path, entry.name, "your user folder")

        for base in self.roots.personal:
            for entry in _list_dir(base):
                if entry.is_dir(follow_symlinks=False) \
                        and match_name(entry.name, self.strong, allow_prefix=False):
                    self.add_folder(entry.path,
                                    f"Named exactly after the app, in {_label(base)}.",
                                    REVIEW)

    def _consider_publisher(self, path: str, label: str) -> None:
        if not self.publisher or self.same_publisher \
                or self.publisher in self.strong:
            return
        self.add_folder(path, f"The publisher's folder in {label}. No other "
                              "installed app comes from this publisher.", REVIEW)

    def phase_shortcuts(self) -> None:
        for base in self.roots.shortcut_dirs:
            label = _label(base)
            for entry in _list_dir(base):
                if self.cancel.is_set():
                    return
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if match_name(entry.name, self.strong, allow_prefix=False):
                        verdict, why = guards.vet_shell_folder(entry.path)
                        if verdict == guards.BLOCK:
                            self.report.excluded.append((entry.path, why))
                            continue
                        size, files = measure(entry.path, self.cancel)
                        rival = self.collision(entry.name)
                        self._emit(Artifact(
                            FOLDER, entry.path, f"Start menu folder in {label}.",
                            REVIEW if rival else SURE,
                            f"Also matches {rival}, which is still installed."
                            if rival else "", size, files))
                    continue
                if not entry.name.lower().endswith((".lnk", ".url")):
                    continue
                self._consider_shortcut(entry.path, entry.name, label)

    def _consider_shortcut(self, path: str, name: str, label: str) -> None:
        target = shortcut_target(path) if name.lower().endswith(".lnk") else ""
        if target and any(within(target, d) for d in self.declared):
            self.add_file(path, SHORTCUT, f"Shortcut into the app's folder, in {label}.")
        elif target and any(within(target, d) for d in self.other_dirs):
            return                       # points at something still installed
        elif match_name(os.path.splitext(name)[0], self.strong, allow_prefix=False):
            self.add_file(path, SHORTCUT, f"Shortcut named after the app, in {label}.")

    def phase_registry(self) -> None:
        if not IS_WINDOWS or winreg is None:
            return
        for root in self.roots.registry:
            self._match_reg_children(root.hive, root.path, root.view)
            if self.vendor:
                for sub in _reg_subkeys(root.hive, root.path, root.view):
                    if compact(sub) == self.vendor and sub.lower() != "microsoft":
                        self._match_reg_children(root.hive, f"{root.path}\\{sub}",
                                                 root.view)
        if self.family:
            for spec in self.family.registry:
                hive, view, path = _parse_reg_spec(spec)
                if _reg_exists(hive, path, view):
                    self.add_reg_key(hive, path, view,
                                     f"Known {self.app.name} settings location.")
        if self.app.source != STORE and self.app.reg_hive and _reg_exists(
                self.app.reg_hive, self.app.reg_path, self.app.reg_view):
            self.add_reg_key(self.app.reg_hive, self.app.reg_path, self.app.reg_view,
                             "The app's own entry in Installed Apps.")

    def _match_reg_children(self, hive: str, path: str, view: int) -> None:
        for sub in _reg_subkeys(hive, path, view):
            if match_name(sub, self.strong, allow_prefix=False):
                self.add_reg_key(hive, f"{path}\\{sub}", view,
                                 "Registry key named exactly after the app.")

    # Everything below this line needs proof: the entry must point into one
    # of the app's folders, not merely share a name with it.
    def _evidence_dirs(self) -> List[str]:
        dirs = list(self.declared)
        if self.family:
            dirs += [os.path.expandvars(p) for p in self.family.paths
                     if "common files" in p.lower()]
        return [d for d in dirs if "%" not in d]

    def phase_evidence(self) -> None:
        if not self.roots.evidence or not IS_WINDOWS or winreg is None:
            return
        dirs = self._evidence_dirs()
        if not dirs:
            return

        def points_in(text: str) -> bool:
            low = str(text or "").lower().replace("/", "\\")
            return any(d.lower().rstrip("\\") + "\\" in low for d in dirs)

        self._evidence_run_keys(points_in)
        self._evidence_classes(points_in)
        self._evidence_app_paths(points_in)
        self._evidence_services(points_in)
        self._evidence_tasks(points_in)

    def _evidence_run_keys(self, points_in) -> None:
        run = r"Software\Microsoft\Windows\CurrentVersion\Run"
        for hive, view in (("HKCU", 0), ("HKLM", 64), ("HKLM", 32)):
            for name, data in _reg_values(hive, run, view):
                if points_in(data):
                    verdict, why = guards.vet_registry(hive, run, name or "(default)")
                    display = _reg_display(hive, run, view) + "\\" + name
                    if verdict == guards.BLOCK:
                        self.report.excluded.append((display, why))
                        continue
                    self._emit(Artifact(
                        REG_VALUE, display, "Starts the app at sign-in.", SURE,
                        hive=hive, reg_path=run, view=view, value=name,
                        needs_admin=hive == "HKLM"))

    def _evidence_classes(self, points_in) -> None:
        for hive, base, view in (("HKCU", r"Software\Classes", 0),
                                 ("HKLM", r"SOFTWARE\Classes", 64)):
            for sub in _reg_subkeys(hive, base, view):
                if self.cancel.is_set():
                    return
                if sub.startswith((".", "*")) or is_guid(sub):
                    continue
                command = _reg_default(hive, f"{base}\\{sub}\\shell\\open\\command", view)
                if not command or not points_in(command):
                    continue
                if self._targets_child(command):
                    self.add_reg_key(hive, f"{base}\\{sub}", view,
                                     f'Opens "{sub}" links with an app installed '
                                     "through this one.", REVIEW)
                else:
                    self.add_reg_key(hive, f"{base}\\{sub}", view,
                                     f'Opens "{sub}" links or files with the app.')

    def _targets_child(self, text: str) -> bool:
        low = str(text or "").lower().replace("/", "\\")
        return any(d.lower().rstrip("\\") + "\\" in low
                   for d in self.child_dirs
                   if key(d) not in {key(x) for x in self.declared})

    def _evidence_app_paths(self, points_in) -> None:
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for view in (64, 32):
            for sub in _reg_subkeys("HKLM", base, view):
                if points_in(_reg_default("HKLM", f"{base}\\{sub}", view)):
                    self.add_reg_key("HKLM", f"{base}\\{sub}", view,
                                     "Lets Windows find the app's program by name.")

    def _evidence_services(self, points_in) -> None:
        base = r"SYSTEM\CurrentControlSet\Services"
        for sub in _reg_subkeys("HKLM", base, 64):
            if self.cancel.is_set():
                return
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", 0,
                                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as h:
                    image = read_value(h, "ImagePath")
                    title = read_value(h, "DisplayName") or sub
            except OSError:
                continue
            if points_in(image):
                self._emit(Artifact(
                    SERVICE, f"{title} ({sub})",
                    "Windows service that runs the app's program.", SURE,
                    name=sub, needs_admin=True))

    def _evidence_tasks(self, points_in) -> None:
        rc, out = run_command(["schtasks", "/query", "/v", "/fo", "csv", "/nh"],
                              timeout=60)
        if rc != 0:
            return
        done = set()
        for row in csv.reader(io.StringIO(out)):
            # HostName, TaskName, ..., Task To Run is the ninth column.
            if len(row) < 9 or row[1] in done or not points_in(row[8]):
                continue
            done.add(row[1])
            self._emit(Artifact(TASK, row[1],
                                "Scheduled task that runs the app's program.",
                                SURE, name=row[1], needs_admin=True))

    def phase_sweep(self) -> None:
        """Walk the whole drive for folders with exactly the app's name.

        This is the step that finds C:\\SteamLibrary, and also the step most
        likely to find something that only shares a name, so nothing it turns
        up is ever ticked by default.
        """
        root = self.roots.sweep_root
        if not root:
            return
        tokens = {t for t in self.strong if len(t) >= _SWEEP_MIN_TOKEN}
        if not tokens:
            self.report.sweep_completed = True
            return

        skip = {key(p) for p in self.roots.sweep_skip + self.other_dirs}
        skip |= {key(a.location) for a in self.report.artifacts if a.kind == FOLDER}
        stack = [root]
        visited = 0
        while stack:
            if self.cancel.is_set() or self.skip_sweep.is_set():
                return
            current = stack.pop()
            visited += 1
            if visited % 400 == 0:
                self.progress("sweep", current, visited)
            for entry in _list_dir(current):
                try:
                    info = entry.stat(follow_symlinks=False)
                    if getattr(info, "st_file_attributes", 0) \
                            & _FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if entry.name.lower() in _SWEEP_SKIP_NAMES \
                            or key(entry.path) in skip:
                        continue
                    if match_name(entry.name, tokens, allow_prefix=False):
                        if not os.path.isdir(os.path.join(entry.path, ".git")):
                            self.add_folder(entry.path,
                                            "Exact name match found by the drive "
                                            "sweep, outside the usual places.",
                                            REVIEW)
                        continue
                    stack.append(entry.path)
                elif entry.name.lower().endswith((".lnk", ".url")) and match_name(
                        os.path.splitext(entry.name)[0], tokens, allow_prefix=False):
                    self.add_file(entry.path, SHORTCUT,
                                  "Shortcut found by the drive sweep.", REVIEW)
        self.report.sweep_completed = True

    def run(self) -> ScanReport:
        started = time.monotonic()
        phases = [
            ("Install folders", self.phase_declared),
            ("App data folders", self.phase_known_locations),
            ("Shortcuts", self.phase_shortcuts),
            ("Registry", self.phase_registry),
            ("Services and tasks", self.phase_evidence),
            ("Sweeping the drive", self.phase_sweep),
        ]
        for index, (name, phase) in enumerate(phases):
            if self.cancel.is_set():
                break
            self.progress("phase", name, index, len(phases))
            phase()
        self._collapse_nested()
        self.report.duration = time.monotonic() - started
        return self.report

    def _collapse_nested(self) -> None:
        """Drop anything already inside a ticked parent, so that removing the
        parent cannot make the child look like a failure."""
        everything = self.report.artifacts
        folders = [a.location for a in everything
                   if a.kind == FOLDER and a.confidence == SURE]
        keys = [(a.hive, a.view, a.reg_path.lower() + "\\") for a in everything
                if a.kind == REG_KEY and a.confidence == SURE]
        keep = []
        for item in everything:
            if item.kind == FOLDER and any(
                    within(item.location, f) and key(item.location) != key(f)
                    for f in folders):
                continue
            if item.kind == REG_KEY and any(
                    h == item.hive and v == item.view
                    and item.reg_path.lower().startswith(p) for h, v, p in keys):
                continue
            keep.append(item)
        self.report.artifacts = keep


def scan(app: InstalledApp, everyone: List[InstalledApp],
         roots: Optional[ScanRoots] = None, cancel: Optional[Event] = None,
         skip_sweep: Optional[Event] = None, progress: Optional[Callable] = None,
         on_found: Optional[Callable] = None) -> ScanReport:
    return _Scan(app, everyone, roots or ScanRoots.for_this_machine(),
                 cancel, skip_sweep, progress, on_found).run()


# ------------------------------------------------------------ small helpers --

def _label(path: str) -> str:
    return os.path.basename(path.rstrip("\\/")) or path


def _reg_display(hive: str, path: str, view: int) -> str:
    if view == 32 and path.lower().startswith("software\\"):
        return f"{hive}\\{path[:9]}WOW6432Node\\{path[9:]}"
    return f"{hive}\\{path}"


def _parse_reg_spec(spec: str) -> Tuple[str, int, str]:
    head, path = spec.split("\\", 1)
    return head[:4], int(head[4:] or 0), path


def _open(hive: str, path: str, view: int):
    return winreg.OpenKey(hive_handle(hive), path, 0,
                          winreg.KEY_READ | view_flag(view))


def _reg_exists(hive: str, path: str, view: int) -> bool:
    try:
        _open(hive, path, view).Close()
        return True
    except OSError:
        return False


def _reg_subkeys(hive: str, path: str, view: int) -> List[str]:
    out = []
    try:
        with _open(hive, path, view) as handle:
            index = 0
            while True:
                try:
                    out.append(winreg.EnumKey(handle, index))
                except OSError:
                    break
                index += 1
    except OSError:
        pass
    return out


def _reg_values(hive: str, path: str, view: int) -> List[Tuple[str, object]]:
    out = []
    try:
        with _open(hive, path, view) as handle:
            index = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(handle, index)
                except OSError:
                    break
                out.append((name, data))
                index += 1
    except OSError:
        pass
    return out


def _reg_default(hive: str, path: str, view: int) -> str:
    try:
        with _open(hive, path, view) as handle:
            return str(read_value(handle, "") or "")
    except OSError:
        return ""
