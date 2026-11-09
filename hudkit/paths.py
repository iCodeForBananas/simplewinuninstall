"""Path comparison and the directories no Simple* tool may ever touch.

Pure functions over strings, shared so every app agrees on what "the same
folder" means and on which folders are off limits. Each app layers its own
policy on top: see winuninstall/guards.py.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Optional

_DRIVE_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def looks_absolute(path: str) -> bool:
    """Absolute by this platform's rules, or a Windows drive path.

    The second case matters because the self-tests exercise Windows-shaped
    paths on any platform, and because `os.path.isabs` on POSIX would happily
    call ``C:\\Windows`` a relative path.
    """
    return bool(path) and (os.path.isabs(path) or bool(_DRIVE_ABS.match(path)))


def norm(path: str) -> str:
    """Normalise for comparison: absolute, no trailing slash, one separator.

    A drive-absolute path has its backslashes converted to the platform
    separator first, so a rule written with either slash compares equal to the
    same location. On Windows `normpath` already does this; doing it explicitly
    also means the guards can be tested faithfully off Windows.
    """
    p = path
    if _DRIVE_ABS.match(p):
        p = p.replace("\\", os.sep)
    p = os.path.normpath(os.path.abspath(p))
    if len(p) > 3 and p.endswith(os.sep):
        p = p[:-1]
    return p


def key(path: str) -> str:
    return norm(path).lower()


def parts(path: str) -> list:
    p = norm(path)
    drive, rest = os.path.splitdrive(p)
    bits = [b for b in rest.split(os.sep) if b]
    return ([drive + os.sep] if drive else []) + bits


def within(candidate: str, base: str) -> bool:
    """True if `candidate` is `base` or sits underneath it."""
    c, b = key(candidate), key(base)
    if c == b:
        return True
    return c.startswith(b + os.sep.lower())


def sysdrive() -> str:
    return os.environ.get("SystemDrive", "C:")


def windir() -> str:
    return os.environ.get("WINDIR", os.environ.get("SystemRoot", r"C:\Windows"))


def userprofile() -> str:
    return os.environ.get("USERPROFILE", os.path.expanduser("~"))


def critical_directories() -> set:
    """Directories that must never be used as a cleanup base."""
    win = windir()
    prof = userprofile()
    drive = sysdrive() + os.sep
    out = {
        drive,
        win,
        os.path.join(win, "System32"),
        os.path.join(win, "SysWOW64"),
        os.path.join(win, "WinSxS"),
        os.path.join(win, "Installer"),
        os.path.join(win, "assembly"),
        os.path.join(win, "Fonts"),
        os.path.join(win, "Boot"),
        os.path.join(win, "System32", "config"),
        os.path.join(win, "System32", "drivers"),
        os.path.join(win, "System32", "DriverStore"),
        os.path.join(win, "servicing"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        os.environ.get("PUBLIC", r"C:\Users\Public"),
        os.path.dirname(prof) or r"C:\Users",
        prof,
        os.environ.get("APPDATA", os.path.join(prof, "AppData", "Roaming")),
        os.environ.get("LOCALAPPDATA", os.path.join(prof, "AppData", "Local")),
        os.path.join(prof, "AppData"),
    }
    return {key(p) for p in out if p}


def self_paths() -> set:
    """Paths belonging to this program, which it must never delete."""
    out = set()
    try:
        out.add(key(os.path.dirname(os.path.abspath(sys.argv[0]))))
        out.add(key(os.path.dirname(os.path.abspath(__file__))))
        if getattr(sys, "frozen", False):
            out.add(key(os.path.dirname(sys.executable)))
            # PyInstaller onefile extraction dir
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                out.add(key(meipass))
    except Exception:
        pass
    return out


def is_self(path: str) -> bool:
    k = key(path)
    return any(k == s or k.startswith(s + os.sep.lower()) for s in self_paths())


def human_size(num: Optional[float]) -> str:
    if not num:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"
