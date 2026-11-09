"""What a name match is allowed to turn into.

A folder that merely *looks* like it belongs to an app gets judged here, once
when it is found and again immediately before removal. Three outcomes:

    OK     may be listed and ticked by default
    ASK    may be listed, but starts unticked and carries a warning
    BLOCK  is never listed and can never be removed, whatever the user ticks

Everything is a pure function over strings plus a reparse-point check, which
is what makes it testable - see selftest.py.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Tuple

from hudkit.paths import (
    critical_directories, is_self, key, looks_absolute, parts, userprofile,
    windir, within,
)
from hudkit.platform_win import is_reparse_point

OK = "ok"
ASK = "ask"
BLOCK = "block"

Verdict = Tuple[str, str]

PERSONAL_COMPONENTS = {
    "documents", "my documents", "desktop", "downloads", "pictures", "videos",
    "music", "saved games", "favorites", "contacts", "onedrive", "dropbox",
    "google drive", "icloud drive", "creative cloud files",
}
SYSTEM_COMPONENTS = {
    "$recycle.bin", "system volume information", "recovery", "windows.old",
    "winsxs", "windowsapps", "driverstore", "boot", "efi", "package cache",
    "installshield installation information", "$windows.~bt", "$windows.~ws",
}


def shared_directories() -> set:
    """Folders every app lives in, so none of them can be "an app's folder"."""
    prof = userprofile()
    local = os.environ.get("LOCALAPPDATA", os.path.join(prof, "AppData", "Local"))
    roaming = os.environ.get("APPDATA", os.path.join(prof, "AppData", "Roaming"))
    data = os.environ.get("ProgramData", r"C:\ProgramData")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    extra = [
        os.path.join(pf, "Common Files"),
        os.path.join(pf86, "Common Files"),
        os.path.join(pf, "WindowsApps"),
        os.path.join(prof, "AppData", "LocalLow"),
        os.path.join(local, "Programs"),
        os.path.join(local, "Temp"),
        os.path.join(local, "Packages"),
        os.path.join(local, "Microsoft"),
        os.path.join(roaming, "Microsoft"),
        os.path.join(data, "Microsoft"),
        os.path.join(data, "Package Cache"),
        os.path.join(local, "Package Cache"),
        os.path.join(prof, "Documents"),
        os.path.join(prof, "Documents", "My Games"),
        os.path.join(prof, "Desktop"),
        os.path.join(prof, "Downloads"),
        os.path.join(prof, "Saved Games"),
        os.path.join(prof, "OneDrive"),
        os.path.join(prof, "OneDrive", "Documents"),
        os.path.join(prof, "OneDrive", "Desktop"),
    ]
    return critical_directories() | {key(p) for p in extra}


def _is_ms_windows_area(path: str) -> bool:
    bits = [b.lower() for b in parts(path)[1:]]
    return any(a == "microsoft" and b.startswith("windows")
               for a, b in zip(bits, bits[1:]))


def vet_folder(path: str, other_dirs: Iterable[str] = (),
               child_dirs: Iterable[str] = (),
               check_reparse: bool = True) -> Verdict:
    """May this folder be offered for removal?

    other_dirs: where every other installed app lives.
    child_dirs: the subset installed *through* this app (Steam's games).
    """
    if not path:
        return BLOCK, "empty path"
    if "%" in path:
        return BLOCK, "unexpanded environment variable"
    if not looks_absolute(path):
        return BLOCK, "not an absolute path"

    bits = parts(path)
    if len(bits) < 2:
        return BLOCK, "drive root"

    k = key(path)
    shared = shared_directories()
    if k in shared:
        return BLOCK, "shared folder, not one app's"
    if any(s == k or s.startswith(k + os.sep.lower()) for s in shared):
        return BLOCK, "contains a protected folder"
    if within(path, windir()):
        return BLOCK, "inside Windows"
    lowered = {b.lower() for b in bits[1:]}
    if lowered & SYSTEM_COMPONENTS:
        return BLOCK, "protected system area"
    if _is_ms_windows_area(path):
        return BLOCK, "inside Windows' per-user data"
    if is_self(path):
        return BLOCK, "belongs to this program"
    if check_reparse and is_reparse_point(path):
        return BLOCK, "link or junction (not followed)"

    children = {key(c) for c in child_dirs}
    holds_children: List[str] = []
    for other in other_dirs:
        is_child = key(other) in children
        if within(path, other) and not (is_child and key(other) == k):
            return BLOCK, f"inside another installed app ({other})"
        if within(other, path):
            if not is_child:
                return BLOCK, f"contains another installed app ({other})"
            holds_children.append(other)

    if holds_children:
        return ASK, (f"Holds {len(holds_children)} app(s) installed through "
                     "this one. Removing it deletes them too.")
    personal = lowered & PERSONAL_COMPONENTS
    if personal:
        return ASK, (f"Inside your {sorted(personal)[0].title()} folder. "
                     "May be something you made.")
    if len(bits) == 2:
        return ASK, "Directly on the root of the drive."
    return OK, ""


def vet_file(path: str, other_dirs: Iterable[str] = (),
             check_reparse: bool = False) -> Verdict:
    if not path or "%" in path or not looks_absolute(path):
        return BLOCK, "not a usable path"
    if len(parts(path)) < 3:
        return BLOCK, "too shallow"
    if within(path, windir()):
        return BLOCK, "inside Windows"
    if {b.lower() for b in parts(path)[1:]} & SYSTEM_COMPONENTS:
        return BLOCK, "protected system area"
    if _is_ms_windows_area(path) and not _is_shell_shortcut(path):
        return BLOCK, "inside Windows' per-user data"
    if is_self(path):
        return BLOCK, "belongs to this program"
    if check_reparse and is_reparse_point(path):
        return BLOCK, "link or junction (not followed)"
    for other in other_dirs:
        if within(path, other):
            return BLOCK, f"inside another installed app ({other})"
    return OK, ""


def _is_shell_shortcut(path: str) -> bool:
    """Shortcuts in the Start menu are in Windows' area, and fair game."""
    return path.lower().endswith((".lnk", ".url")) or "start menu" in path.lower()


def vet_shell_folder(path: str, other_dirs: Iterable[str] = ()) -> Verdict:
    """A folder inside the Start menu. Only ever holds shortcuts."""
    lowered = path.lower().replace("/", "\\")
    if not re.search(r"\\start menu\\programs\\.+", lowered):
        return BLOCK, "not a Start menu folder"
    leaf = os.path.basename(lowered.rstrip("\\"))
    if leaf in ("startup", "administrative tools", "accessories", "windows tools",
                "system tools", "accessibility", "maintenance"):
        return BLOCK, "Windows' own Start menu folder"
    return OK, ""


# ---------------------------------------------------------------- registry --

_PROTECTED_SOFTWARE = {
    "microsoft", "policies", "wow6432node", "clients",
    "registeredapplications", "odbc", "classes",
}
_NOT_A_CLASS = re.compile(
    r"^(\.|\*|clsid$|interface$|typelib$|appid$|applications$|directory$|"
    r"drive$|folder$|allfilesystemobjects$|systemfileassociations$|"
    r"http$|https$|ftp$|mailto$|exefile$|lnkfile$|batfile$|cmdfile$|"
    r"windows\.|microsoft\.|ms-)", re.IGNORECASE)
_ALLOWED_MS_PREFIXES = (
    "microsoft\\windows\\currentversion\\uninstall\\",
    "microsoft\\windows\\currentversion\\app paths\\",
)
_RUN_KEYS = (
    "microsoft\\windows\\currentversion\\run",
    "microsoft\\windows\\currentversion\\runonce",
)


def vet_registry(hive: str, path: str, value: str = "") -> Verdict:
    """May this registry key or value be removed?"""
    if hive not in ("HKCU", "HKLM"):
        return BLOCK, "unknown hive"
    low = path.replace("/", "\\").strip("\\").lower()
    if ".." in low or not low.startswith("software\\"):
        return BLOCK, "outside the Software branch"
    rest = low[len("software\\"):]
    if not rest:
        return BLOCK, "too shallow"
    head = rest.split("\\")[0]

    if head == "classes":
        bits = rest.split("\\")
        if len(bits) != 2 or _NOT_A_CLASS.match(bits[1]) or value:
            return BLOCK, "protected file-type or COM registration"
        return OK, ""
    if value:
        return (OK, "") if rest in _RUN_KEYS else (BLOCK, "value outside a Run key")
    if head == "microsoft":
        if any(rest.startswith(p) and len(rest) > len(p)
               and "\\" not in rest[len(p):] for p in _ALLOWED_MS_PREFIXES):
            return OK, ""
        return BLOCK, "inside Microsoft's registry branch"
    if head in _PROTECTED_SOFTWARE:
        return BLOCK, "protected registry branch"
    return OK, ""
