"""What is installed: the same registry entries Settings > Apps reads."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from hudkit.paths import key, within
from hudkit.platform_win import IS_WINDOWS, run_command

from . import guards
from .matching import is_guid

try:
    import winreg
except ImportError:  # pragma: no cover - not Windows
    winreg = None  # type: ignore[assignment]

UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
WIN32 = "win32"
STORE = "store"

_EXE_CMD = re.compile(r"^\s*(.+?\.exe)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_GENERIC_LEAF = {"bin", "uninstall", "uninst", "_uninst", "_uninstall",
                 "uninstaller", "x64", "x86", "app"}
_SKIP_RELEASE = {"update", "hotfix", "security update", "service pack"}


@dataclass
class InstalledApp:
    id: str
    name: str
    publisher: str = ""
    version: str = ""
    install_location: str = ""
    uninstall_string: str = ""
    display_icon: str = ""
    size_bytes: int = 0
    install_date: str = ""
    source: str = WIN32
    is_msi: bool = False
    reg_hive: str = ""
    reg_view: int = 0
    reg_key: str = ""         # subkey name under the Uninstall key
    package_full_name: str = ""
    no_remove: bool = False

    @property
    def reg_path(self) -> str:
        return UNINSTALL_KEY + "\\" + self.reg_key


def view_flag(view: int) -> int:
    if view == 64:
        return winreg.KEY_WOW64_64KEY
    if view == 32:
        return winreg.KEY_WOW64_32KEY
    return 0


def hive_handle(hive: str):
    return {"HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]


def read_value(handle, name: str, default=""):
    try:
        return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return default


def split_command(command: str) -> Tuple[str, str]:
    """(program, arguments) from a registry command line.

    Handles the unquoted-with-spaces form that plenty of installers write:
    C:\\Program Files (x86)\\Steam\\uninstall.exe
    """
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
    match = _EXE_CMD.match(text)
    if match:
        return match.group(1).strip(), (match.group(2) or "").strip()
    bits = text.split(None, 1)
    return (bits[0] if bits else ""), (bits[1] if len(bits) > 1 else "")


def icon_path(display_icon: str) -> str:
    """DisplayIcon is often "C:\\app\\a.exe,0"."""
    text = (display_icon or "").strip().strip('"')
    return re.sub(r",\s*-?\d+$", "", text).strip().strip('"')


def _climb_generic(path: str) -> str:
    """...\\Foo\\bin -> ...\\Foo: the leaf says nothing about the app."""
    while os.path.basename(path.rstrip("\\/")).lower() in _GENERIC_LEAF:
        path = os.path.dirname(path.rstrip("\\/"))
    return path


def app_dirs(app: InstalledApp) -> List[str]:
    """Folders the registry entry says the app lives in.

    Only InstallLocation if there is one. Otherwise inferred from the
    uninstaller and icon, with cached-installer and Windows locations thrown
    out by the guard: msiexec does not mean the app lives in System32.
    """
    if app.source == STORE:
        return []
    raw = []
    location = (app.install_location or "").strip().strip('"')
    if location:
        raw.append(location)
    else:
        for exe in (split_command(os.path.expandvars(app.uninstall_string))[0],
                    icon_path(os.path.expandvars(app.display_icon))):
            if exe.lower().endswith(".exe"):
                raw.append(_climb_generic(os.path.dirname(exe)))

    out: List[str] = []
    seen: Set[str] = set()
    for path in raw:
        path = os.path.expandvars(path).rstrip("\\/")
        if not path or key(path) in seen or is_guid(os.path.basename(path)):
            continue
        verdict, _ = guards.vet_folder(path, check_reparse=False)
        if verdict != guards.BLOCK:
            seen.add(key(path))
            out.append(path)
    return out


def _read_entry(hive: str, view: int, subkey: str) -> Optional[InstalledApp]:
    path = UNINSTALL_KEY + "\\" + subkey
    try:
        handle = winreg.OpenKey(hive_handle(hive), path, 0,
                                winreg.KEY_READ | view_flag(view))
    except OSError:
        return None
    with handle:
        name = str(read_value(handle, "DisplayName") or "").strip()
        uninstall = str(read_value(handle, "UninstallString") or "").strip()
        is_msi = read_value(handle, "WindowsInstaller", 0) == 1
        if not name or not (uninstall or is_msi):
            return None
        if read_value(handle, "SystemComponent", 0) == 1:
            return None
        if read_value(handle, "ParentKeyName") or read_value(handle, "ParentDisplayName"):
            return None
        release = str(read_value(handle, "ReleaseType") or "").strip().lower()
        if release in _SKIP_RELEASE or re.match(r"^KB\d+$", subkey, re.IGNORECASE):
            return None
        try:
            size_kb = int(read_value(handle, "EstimatedSize", 0) or 0)
        except (TypeError, ValueError):
            size_kb = 0
        return InstalledApp(
            id=f"{hive}{view}:{subkey}",
            name=name,
            publisher=str(read_value(handle, "Publisher") or "").strip(),
            version=str(read_value(handle, "DisplayVersion") or "").strip(),
            install_location=str(read_value(handle, "InstallLocation") or "").strip(),
            uninstall_string=uninstall,
            display_icon=str(read_value(handle, "DisplayIcon") or "").strip(),
            size_bytes=max(0, size_kb) * 1024,
            install_date=_pretty_date(str(read_value(handle, "InstallDate") or "")),
            is_msi=is_msi,
            reg_hive=hive,
            reg_view=view,
            reg_key=subkey,
            no_remove=read_value(handle, "NoRemove", 0) == 1,
        )


def _pretty_date(raw: str) -> str:
    """InstallDate is free text in practice. Only show it if it is a date."""
    if not re.match(r"^\d{8}$", raw):
        return ""
    if not (1 <= int(raw[4:6]) <= 12 and 1 <= int(raw[6:8]) <= 31):
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def store_display_name(package_name: str) -> str:
    """Microsoft.WindowsCalculator -> WindowsCalculator; the vendor prefix
    is noise and the real title needs a slow manifest read per app."""
    head, _, rest = package_name.partition(".")
    return rest if rest and not head.isdigit() else package_name


def store_publisher(publisher: str) -> str:
    """Store publishers are often a certificate GUID, which tells you nothing."""
    name = re.sub(r"^CN=([^,]+).*$", r"\1", publisher)
    return "" if is_guid(name) else name


def list_win32_apps() -> List[InstalledApp]:
    if not IS_WINDOWS or winreg is None:
        return []
    found = {}
    for hive, view in (("HKLM", 64), ("HKLM", 32), ("HKCU", 0)):
        try:
            root = winreg.OpenKey(hive_handle(hive), UNINSTALL_KEY, 0,
                                  winreg.KEY_READ | view_flag(view))
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                app = _read_entry(hive, view, subkey)
                if app is not None:
                    found.setdefault((app.name.lower(), app.version), app)
    return sorted(found.values(), key=lambda a: a.name.lower())


_STORE_SCRIPT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Get-AppxPackage | Where-Object {"
    "$_.IsFramework -eq $false -and $_.IsResourcePackage -eq $false -and "
    "$_.NonRemovable -eq $false -and $_.SignatureKind -eq 'Store'} | "
    "Select-Object Name,Publisher,Version,PackageFullName,InstallLocation | "
    "ConvertTo-Json -Compress"
)


def list_store_apps() -> List[InstalledApp]:
    """Microsoft Store apps. Slow (PowerShell), so loaded separately."""
    if not IS_WINDOWS:
        return []
    rc, out = run_command(["powershell", "-NoProfile", "-NonInteractive",
                           "-Command", _STORE_SCRIPT], timeout=60)
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    apps = []
    for row in data:
        full = str(row.get("PackageFullName") or "")
        if not full:
            continue
        apps.append(InstalledApp(
            id=f"store:{full}",
            name=store_display_name(str(row.get("Name") or full)),
            publisher=store_publisher(str(row.get("Publisher") or "")),
            version=str(row.get("Version") or ""), source=STORE,
            package_full_name=full,
        ))
    return sorted(apps, key=lambda a: a.name.lower())


def still_installed(app: InstalledApp) -> bool:
    """Is this app's registration still there?"""
    if not IS_WINDOWS:
        return False
    if app.source == STORE:
        name = app.package_full_name
        if not re.match(r"^[\w.\-~]+$", name):
            return False
        rc, out = run_command(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-AppxPackage | Where-Object PackageFullName -eq '{name}')"
             ".PackageFullName"], timeout=60)
        return rc == 0 and name in out
    try:
        handle = winreg.OpenKey(hive_handle(app.reg_hive), app.reg_path, 0,
                                winreg.KEY_READ | view_flag(app.reg_view))
    except OSError:
        return False
    handle.Close()
    return True


def children_of(app: InstalledApp, everyone: List[InstalledApp]) -> List[InstalledApp]:
    """Apps installed through this one - Steam's games, for example."""
    dirs = app_dirs(app)
    out = []
    for other in everyone:
        if other.id == app.id or other.source == STORE:
            continue
        exe = split_command(os.path.expandvars(other.uninstall_string))[0]
        location = other.install_location.strip().strip('"')
        for d in dirs:
            if (exe and within(exe, d)) or (location and within(location, d)):
                out.append(other)
                break
    return out
