"""Thin, dependency-free wrappers around the Windows APIs we need.

Every function here degrades gracefully on non-Windows platforms (and when a
call is blocked by permissions) so the app never crashes just because an API
is unavailable.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Iterable, Optional, Tuple

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """True if the current process has an elevated (Administrator) token."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: Optional[Iterable[str]] = None,
                      module: str = "winclean") -> bool:
    """Ask Windows to restart this program elevated (triggers the UAC prompt).

    Returns True if the elevated process was launched (the caller should then
    exit). Returns False if the user declined UAC or the call failed - the
    caller should keep running unelevated and degrade.
    """
    if not IS_WINDOWS:
        return False

    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = _join_args(args)
    else:
        exe = sys.executable
        # Re-run the same module entry point.
        params = _join_args(["-m", module, *args])

    try:
        # SW_SHOWNORMAL = 1; return value > 32 means success.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, os.getcwd(), 1
        )
        return int(rc) > 32
    except Exception:
        return False


def _join_args(args: Iterable[str]) -> str:
    out = []
    for a in args:
        if " " in a or '"' in a:
            out.append('"' + a.replace('"', r"\"") + '"')
        else:
            out.append(a)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Recycle Bin
# ---------------------------------------------------------------------------

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def query_recycle_bin() -> Tuple[int, int]:
    """Return (total_bytes, item_count) across all drives. (0, 0) on failure."""
    if not IS_WINDOWS:
        return (0, 0)
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        # None = all drives for the current user.
        rc = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if rc != 0:
            return (0, 0)
        return (int(info.i64Size), int(info.i64NumItems))
    except Exception:
        return (0, 0)


SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


def empty_recycle_bin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        rc = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        # S_OK (0) or "already empty" (0x8000FFFF on some builds) both fine.
        return rc in (0, -2147418113)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Send a file to the Recycle Bin instead of deleting it permanently
# ---------------------------------------------------------------------------

FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400
FOF_NOCONFIRMMKDIR = 0x0200
FOF_WANTNUKEWARNING = 0x4000


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def send_to_recycle_bin(path: str, warn_if_permanent: bool = False) -> bool:
    """Move a single file/folder to the Recycle Bin. Returns True on success.

    Windows silently deletes for good anything too big for the bin. With
    warn_if_permanent it asks first instead, and a "no" returns False.
    Blocks until answered, so only use that from a worker thread.
    """
    if not IS_WINDOWS:
        return False
    try:
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        # pFrom must be double-null terminated.
        op.pFrom = path + "\0\0"
        op.pTo = None
        op.fFlags = (
            FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            | FOF_NOCONFIRMMKDIR
            | (FOF_WANTNUKEWARNING if warn_if_permanent else 0)
        )
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return rc == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Attributes / reparse points
# ---------------------------------------------------------------------------

FILE_ATTRIBUTE_READONLY = 0x01
FILE_ATTRIBUTE_HIDDEN = 0x02
FILE_ATTRIBUTE_SYSTEM = 0x04
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# GetFileAttributesW returns a DWORD, but ctypes defaults the return type to
# a *signed* int. Without this, a missing file's 0xFFFFFFFF arrives as -1,
# the INVALID_FILE_ATTRIBUTES comparison fails, and -1 & FILE_ATTRIBUTE_*
# is true for every flag - so every non-existent path looked like a junction.
if IS_WINDOWS:  # pragma: no cover - Windows only
    try:
        _k32 = ctypes.windll.kernel32
        _k32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        _k32.GetFileAttributesW.restype = ctypes.c_uint32
        _k32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        _k32.SetFileAttributesW.restype = ctypes.c_int
    except Exception:
        pass


def get_attributes(path: str) -> int:
    """Raw file attributes, or INVALID_FILE_ATTRIBUTES if unavailable."""
    if not IS_WINDOWS:
        return INVALID_FILE_ATTRIBUTES
    try:
        # Masked as well as typed, so a signed value can never slip through.
        return int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
    except Exception:
        return INVALID_FILE_ATTRIBUTES


def is_reparse_point(path: str) -> bool:
    """True for junctions / symlinks / OneDrive-style placeholders."""
    attrs = get_attributes(path)
    if attrs == INVALID_FILE_ATTRIBUTES:
        # Fall back to the portable check so this works in tests off-Windows.
        try:
            return os.path.islink(path)
        except OSError:
            return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def clear_readonly(path: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        attrs = get_attributes(path)
        if attrs != INVALID_FILE_ATTRIBUTES and attrs & FILE_ATTRIBUTE_READONLY:
            ctypes.windll.kernel32.SetFileAttributesW(
                str(path), attrs & ~FILE_ATTRIBUTE_READONLY
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Services / external commands
# ---------------------------------------------------------------------------

_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def run_command(args, timeout: int = 120) -> Tuple[int, str]:
    """Run a command with no console window. Returns (returncode, output)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
            shell=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, str(exc)


def service_state(name: str) -> str:
    rc, out = run_command(["sc", "query", name], timeout=20)
    if rc != 0:
        return "unknown"
    low = out.lower()
    if "running" in low:
        return "running"
    if "stopped" in low:
        return "stopped"
    return "unknown"


def stop_service(name: str) -> bool:
    rc, _ = run_command(["net", "stop", name, "/y"], timeout=90)
    return rc == 0


def start_service(name: str) -> bool:
    rc, _ = run_command(["net", "start", name], timeout=90)
    return rc == 0


def restart_explorer() -> bool:
    """Restart Explorer so thumbnail/icon caches are released and rebuilt."""
    run_command(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    try:
        subprocess.Popen(
            [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")],
            creationflags=_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def running_processes() -> set:
    """Lower-cased image names of running processes (empty set on failure)."""
    rc, out = run_command(["tasklist", "/fo", "csv", "/nh"], timeout=30)
    if rc != 0:
        return set()
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"'):
            names.add(line.split('","')[0].strip('"').lower())
    return names


def process_is_running(image_name: str) -> bool:
    return image_name.lower() in running_processes()


def open_in_shell(target: str) -> None:
    """Open a path, URL or ms-settings: URI with the default handler."""
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Disk space
# ---------------------------------------------------------------------------

def enable_dpi_awareness() -> bool:
    """Tell Windows we scale our own UI. Must run BEFORE the Tk root exists.

    Without this, Windows renders the window at 96 DPI and then bitmap-
    stretches it to the display's scaling factor, which is why an unaware
    tkinter app looks soft or blurry on a high-DPI screen. Declaring
    awareness means we get real pixels; theme.px() then scales the drawing
    to match.
    """
    if not IS_WINDOWS:
        return False
    # Per-monitor v2 is the best option and handles moving between displays
    # with different scaling. Fall back through the older APIs.
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        ctx = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctx):
            return True
    except Exception:
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE == 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Frameless window support
# ---------------------------------------------------------------------------
# tkinter's overrideredirect(True) removes the title bar and border, but it
# also makes the window a tool-style popup: no taskbar button, and Tk refuses
# to iconify it. These helpers put back what is lost, using the real Win32
# window rather than emulating it.
#
# Every function here declares argtypes AND restype. ctypes otherwise
# assumes int, which truncates 64-bit handles and sign-mangles DWORDs - the
# same class of bug that once made every missing file look like a junction.

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SW_MINIMIZE = 6
MONITOR_DEFAULTTONEAREST = 2


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


_user32_handle = None


def _u32():
    """A private user32 handle with every prototype declared.

    Private (WinDLL rather than windll.user32) so declaring prototypes here
    cannot change how any other module's calls behave.
    """
    global _user32_handle
    if _user32_handle is not None or not IS_WINDOWS:
        return _user32_handle
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        vp, i32, u32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        u.GetParent.argtypes = [vp]
        u.GetParent.restype = vp
        u.GetWindowLongW.argtypes = [vp, i32]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [vp, i32, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowPos.argtypes = [vp, vp, i32, i32, i32, i32, u32]
        u.SetWindowPos.restype = ctypes.c_int
        u.ShowWindow.argtypes = [vp, i32]
        u.ShowWindow.restype = ctypes.c_int
        u.MonitorFromWindow.argtypes = [vp, ctypes.c_ulong]
        u.MonitorFromWindow.restype = vp
        u.GetMonitorInfoW.argtypes = [vp, ctypes.POINTER(_MONITORINFO)]
        u.GetMonitorInfoW.restype = ctypes.c_int
        _user32_handle = u
    except Exception:
        _user32_handle = None
    return _user32_handle


def _signed32(value: int) -> int:
    """Window styles are DWORDs, but SetWindowLongW takes a signed LONG."""
    return ctypes.c_int32(value & 0xFFFFFFFF).value


def toplevel_hwnd(widget) -> Optional[int]:
    """The real top-level HWND for a Tk root (Tk's own id is a child of it)."""
    u = _u32()
    if u is None:
        return None
    try:
        hwnd = u.GetParent(ctypes.c_void_p(int(widget.winfo_id())))
        return int(hwnd) if hwnd else None
    except Exception:
        return None


def make_taskbar_window(hwnd: int) -> bool:
    """Give a frameless window back its taskbar button and minimize ability.

    Swaps the tool-window extended style for the app-window one, and adds
    the minimize-box and system-menu styles that the taskbar relies on to
    minimize and restore a window. No caption style is added, so nothing is
    drawn - these only change behaviour.
    """
    u = _u32()
    if u is None or not hwnd:
        return False
    try:
        h = ctypes.c_void_p(hwnd)
        ex = u.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
        ex = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        u.SetWindowLongW(h, GWL_EXSTYLE, _signed32(ex))

        st = u.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
        st |= WS_MINIMIZEBOX | WS_SYSMENU
        u.SetWindowLongW(h, GWL_STYLE, _signed32(st))

        u.SetWindowPos(h, None, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                       | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        return True
    except Exception:
        return False


def minimize_window(hwnd: int) -> bool:
    u = _u32()
    if u is None or not hwnd:
        return False
    try:
        u.ShowWindow(ctypes.c_void_p(hwnd), SW_MINIMIZE)
        return True
    except Exception:
        return False


# There is intentionally no "begin native drag" helper here. The usual
# borderless trick - ReleaseCapture() then SendMessage(WM_NCLBUTTONDOWN,
# HTCAPTION) - runs Windows' modal move loop inside a ctypes call made from
# a Tk callback. That loop pumps Tcl's timers, Tcl calls back into Python
# re-entrantly, and _tkinter dies with a fatal thread-state error. It was
# tried, it crashed on every drag, and selftest now guards against it.


def work_area(hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """(x, y, width, height) of the usable area - minus the taskbar - of the
    monitor the window is on."""
    u = _u32()
    if u is None or not hwnd:
        return None
    try:
        mon = u.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)
        if not mon:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(info)):
            return None
        r = info.rcWork
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


def disk_free(path: str = None) -> Tuple[int, int]:
    """Return (free_bytes, total_bytes) for the volume holding `path`."""
    import shutil

    target = path or os.environ.get("SystemDrive", "C:") + os.sep
    try:
        usage = shutil.disk_usage(target)
        return usage.free, usage.total
    except Exception:
        return (0, 0)
