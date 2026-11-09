"""Run the app's own uninstaller and work out whether it actually finished.

"The process exited" is not "the app is gone". NSIS and Inno Setup uninstallers
copy themselves to %TEMP%, start the copy and exit straight away, while the
user is still reading the first page. So this follows the whole process tree
and then checks the registry, and a cancelled uninstaller is reported as still
installed - which is what stops the leftover scan from treating a working app
as debris.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from dataclasses import dataclass
from threading import Event
from typing import Callable, Dict, Optional, Set, Tuple

from hudkit import applog
from hudkit.platform_win import IS_WINDOWS, run_command

from .inventory import STORE, InstalledApp, split_command, still_installed
from .matching import is_guid

REMOVED = "removed"
STILL_INSTALLED = "still_installed"
FAILED = "failed"

ERROR_ELEVATION_REQUIRED = 740
_GRACE_SECONDS = 6.0
_POLL_SECONDS = 0.25


@dataclass
class UninstallOutcome:
    state: str
    detail: str = ""
    exit_code: Optional[int] = None


def command_for(app: InstalledApp) -> str:
    """The command to run. Empty if there is none."""
    if app.is_msi and is_guid(app.reg_key):
        # Plenty of MSI entries say /I{GUID}, which opens "modify", not remove.
        return f"msiexec.exe /X{app.reg_key}"
    return os.path.expandvars(app.uninstall_string).strip()


# ------------------------------------------------------------------- Win32 --
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32), ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32), ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_WAIT_OBJECT_0 = 0
_api = None


def _win32():
    """Private DLL handles with every prototype declared.

    Declared so handles stay 64-bit: ctypes assumes int otherwise.
    """
    global _api
    if _api is not None or not IS_WINDOWS:
        return _api
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    s = ctypes.WinDLL("shell32", use_last_error=True)
    vp, u32 = ctypes.c_void_p, ctypes.c_uint32
    k.CreateToolhelp32Snapshot.argtypes = [u32, u32]
    k.CreateToolhelp32Snapshot.restype = vp
    entry = ctypes.POINTER(_PROCESSENTRY32W)
    k.Process32FirstW.argtypes = [vp, entry]
    k.Process32FirstW.restype = ctypes.c_int
    k.Process32NextW.argtypes = [vp, entry]
    k.Process32NextW.restype = ctypes.c_int
    k.CloseHandle.argtypes = [vp]
    k.CloseHandle.restype = ctypes.c_int
    k.GetProcessId.argtypes = [vp]
    k.GetProcessId.restype = u32
    k.WaitForSingleObject.argtypes = [vp, u32]
    k.WaitForSingleObject.restype = u32
    k.GetExitCodeProcess.argtypes = [vp, ctypes.POINTER(u32)]
    k.GetExitCodeProcess.restype = ctypes.c_int
    s.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    s.ShellExecuteExW.restype = ctypes.c_int
    _api = (k, s)
    return _api


def _process_table() -> Dict[int, int]:
    """{pid: parent pid} for everything running."""
    api = _win32()
    if api is None:
        return {}
    k = api[0]
    snap = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap in (None, _INVALID_HANDLE):
        return {}
    table: Dict[int, int] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    return table


class ProcessTree:
    """Follows a process and every descendant, even after a parent exits."""

    def __init__(self, root_pid: int) -> None:
        self.tracked: Set[int] = {root_pid}

    def update(self, table: Dict[int, int]) -> bool:
        """Feed a fresh process table. True while any of the tree is alive."""
        grew = True
        while grew:
            grew = False
            for pid, parent in table.items():
                if pid not in self.tracked and parent in self.tracked:
                    self.tracked.add(pid)
                    grew = True
        # Forget the dead, so a recycled PID cannot adopt unrelated processes.
        self.tracked &= set(table)
        return bool(self.tracked)


# ----------------------------------------------------------------- launching --
def _start_elevated(command: str) -> Tuple[Optional[int], Optional[int], str]:
    """UAC prompt, then run. (pid, process handle, error text)."""
    api = _win32()
    if api is None:
        return None, None, "Only available on Windows."
    program, args = split_command(command)
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = program
    info.lpParameters = args or None
    info.nShow = 1
    if not api[1].ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None, None, "Windows would not start it (UAC was declined?)."
    return int(api[0].GetProcessId(info.hProcess)), info.hProcess, ""


def _start(command: str):
    """Returns (pid, poll function, error text)."""
    try:
        proc = subprocess.Popen(command, shell=False)
        return proc.pid, proc.poll, ""
    except OSError as exc:
        if getattr(exc, "winerror", None) != ERROR_ELEVATION_REQUIRED:
            return None, None, f"Could not start the uninstaller: {exc}"

    pid, handle, error = _start_elevated(command)
    if pid is None:
        return None, None, error
    k = _win32()[0]

    def poll() -> Optional[int]:
        if k.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
            return None
        code = ctypes.c_uint32()
        k.GetExitCodeProcess(handle, ctypes.byref(code))
        return int(code.value)

    return pid, poll, ""


def run_uninstaller(app: InstalledApp, stop_waiting: Optional[Event] = None,
                    on_status: Optional[Callable[[str], None]] = None
                    ) -> UninstallOutcome:
    """Blocks until the uninstaller is done. Call from a worker thread."""
    log = applog.get()
    stop_waiting = stop_waiting or Event()
    say = on_status or (lambda _text: None)

    if app.source == STORE:
        return _remove_store_app(app, say)

    command = command_for(app)
    if not command:
        return UninstallOutcome(FAILED, "This app does not register an uninstaller.")

    log.info("[uninstall] %s: %s", app.name, command)
    pid, poll, error = _start(command)
    if pid is None:
        log.error("[uninstall] %s", error)
        return UninstallOutcome(FAILED, error)

    say("Follow the uninstaller's window. This page will carry on when it closes.")
    tree = ProcessTree(pid)
    exit_code: Optional[int] = None
    idle_since: Optional[float] = None
    while not stop_waiting.is_set():
        code = poll()
        if code is not None:
            exit_code = code
        alive = tree.update(_process_table())
        if not alive and code is not None:
            if not still_installed(app):
                break
            # A short grace period: the registry entry is often the last thing
            # an uninstaller removes, sometimes from a process we cannot see.
            idle_since = idle_since if idle_since is not None else time.monotonic()
            if time.monotonic() - idle_since >= _GRACE_SECONDS:
                break
        else:
            idle_since = None
        time.sleep(_POLL_SECONDS)

    if still_installed(app):
        log.info("[uninstall] %s still registered (exit code %s)", app.name, exit_code)
        return UninstallOutcome(
            STILL_INSTALLED,
            "It is still registered as installed. The uninstaller may have been "
            "cancelled, or it may need a restart to finish.", exit_code)
    log.info("[uninstall] %s removed (exit code %s)", app.name, exit_code)
    return UninstallOutcome(REMOVED, "The uninstaller finished.", exit_code)


def _remove_store_app(app: InstalledApp, say) -> UninstallOutcome:
    name = app.package_full_name
    if not re.match(r"^[\w.\-~]+$", name):
        return UninstallOutcome(FAILED, "Unexpected package name.")
    say("Removing the Store package...")
    rc, out = run_command(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           f"Remove-AppxPackage -Package '{name}'"], timeout=300)
    applog.get().info("[uninstall] Store package %s: rc=%s %s", name, rc, out.strip())
    if rc != 0:
        return UninstallOutcome(FAILED, out.strip() or "Windows refused.", rc)
    return UninstallOutcome(REMOVED, "The package was removed.", rc)
