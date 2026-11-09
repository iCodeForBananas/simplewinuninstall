"""Remove the artifacts the user ticked.

Every item is re-vetted right here, not just when it was found: the guards are
the last thing standing between a stale list and a deleted folder. Registry
keys, services and tasks are exported to a backup first, and if the backup
fails the item is left alone.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from hudkit import applog
from hudkit.platform_win import (
    clear_readonly, is_admin, run_command, send_to_recycle_bin, stop_service,
)

from . import guards
from .artifacts import (
    FILE, FOLDER, REG_KEY, REG_VALUE, SERVICE, SHORTCUT, TASK, Artifact,
)
from .inventory import InstalledApp, still_installed


class StillInstalled(Exception):
    """Refused: this would delete files out from under a working app."""


@dataclass
class ItemResult:
    artifact: Artifact
    ok: bool
    detail: str = ""


@dataclass
class RemoveReport:
    results: List[ItemResult] = field(default_factory=list)
    backup_dir: str = ""
    recycled: bool = False

    @property
    def removed(self) -> List[ItemResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> List[ItemResult]:
        return [r for r in self.results if not r.ok]

    @property
    def freed_bytes(self) -> int:
        return sum(r.artifact.size_bytes for r in self.removed)


def backup_dir_for(app: InstalledApp) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]+", "_", app.name)[:40] or "app"
    return os.path.join(applog.data_dir(), "backups", f"{safe}-{stamp}")


def _reg_flag(view: int) -> List[str]:
    return [f"/reg:{view}"] if view in (32, 64) else []


def _delete_tree(path: str) -> Tuple[bool, str]:
    def retry(func, target, _exc):
        clear_readonly(target)
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(path, onerror=retry)
    except OSError as exc:
        return False, str(exc)
    return not os.path.exists(path), ""


def _remove_path(artifact: Artifact, recycle: bool,
                 other_dirs: Sequence[str], child_dirs: Sequence[str]
                 ) -> Tuple[bool, str]:
    path = artifact.location
    if not os.path.lexists(path):
        return True, "already gone"

    if artifact.kind == FOLDER:
        if "\\start menu\\programs\\" in path.lower():
            verdict, why = guards.vet_shell_folder(path)
        else:
            verdict, why = guards.vet_folder(path, other_dirs, child_dirs)
    else:
        verdict, why = guards.vet_file(path, other_dirs)
    if verdict == guards.BLOCK:
        return False, f"blocked at removal: {why}"

    if recycle:
        if send_to_recycle_bin(path, warn_if_permanent=True):
            return (True, "") if not os.path.lexists(path) \
                else (False, "still there after recycling")
        return False, "could not move it to the Recycle Bin (in use or declined)"

    if artifact.kind == FOLDER:
        ok, error = _delete_tree(path)
        return ok, error or ("" if ok else "some files are in use")
    try:
        clear_readonly(path)
        os.remove(path)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def _remove_reg(artifact: Artifact, backup: str, serial: int) -> Tuple[bool, str]:
    full = f"{artifact.hive}\\{artifact.reg_path}"
    verdict, why = guards.vet_registry(artifact.hive, artifact.reg_path,
                                       artifact.value)
    if verdict == guards.BLOCK:
        return False, f"blocked at removal: {why}"

    rc, _ = run_command(["reg", "query", full, *_reg_flag(artifact.view)], timeout=60)
    if rc != 0:
        return True, "already gone"

    target = os.path.join(backup, f"{serial:03d}.reg")
    os.makedirs(backup, exist_ok=True)
    rc, out = run_command(["reg", "export", full, target, "/y",
                           *_reg_flag(artifact.view)], timeout=60)
    if rc != 0:
        return False, f"not removed, backup failed: {out.strip()}"

    args = ["reg", "delete", full, "/f", *_reg_flag(artifact.view)]
    if artifact.kind == REG_VALUE:
        args[3:3] = ["/v", artifact.value]
    rc, out = run_command(args, timeout=60)
    return rc == 0, "" if rc == 0 else out.strip()


def _remove_service(artifact: Artifact, backup: str, serial: int) -> Tuple[bool, str]:
    name = artifact.name
    if not re.match(r"^[\w .\-]+$", name):
        return False, "unexpected service name"
    if not is_admin():
        return False, "needs Administrator"
    target = os.path.join(backup, f"{serial:03d}-service.reg")
    os.makedirs(backup, exist_ok=True)
    rc, out = run_command(["reg", "export",
                           f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{name}",
                           target, "/y"], timeout=60)
    if rc != 0:
        return False, f"not removed, backup failed: {out.strip()}"
    stop_service(name)
    rc, out = run_command(["sc", "delete", name], timeout=60)
    return rc == 0, "" if rc == 0 else out.strip()


def _remove_task(artifact: Artifact, backup: str, serial: int) -> Tuple[bool, str]:
    name = artifact.name
    rc, xml = run_command(["schtasks", "/query", "/tn", name, "/xml"], timeout=60)
    if rc != 0:
        return False, f"not removed, backup failed: {xml.strip()}"
    os.makedirs(backup, exist_ok=True)
    with open(os.path.join(backup, f"{serial:03d}-task.xml"), "w",
              encoding="utf-16") as fh:
        fh.write(xml)
    rc, out = run_command(["schtasks", "/delete", "/tn", name, "/f"], timeout=60)
    return rc == 0, "" if rc == 0 else out.strip()


def remove(app: InstalledApp, artifacts: Iterable[Artifact],
           other_dirs: Sequence[str] = (), child_dirs: Sequence[str] = (),
           recycle: bool = True, allow_installed: bool = False,
           cancel: Optional[Event] = None,
           progress: Optional[Callable[[Artifact, int, int], None]] = None
           ) -> RemoveReport:
    """Remove each artifact. One failure never stops the rest.

    allow_installed must be set explicitly to work on an app that is still
    registered; the GUI only sets it after warning the user and being told yes.
    """
    log = applog.get()
    if not allow_installed and still_installed(app):
        raise StillInstalled(app.name)

    items = list(artifacts)
    report = RemoveReport(backup_dir=backup_dir_for(app), recycled=recycle)
    log.info("[remove] %s: %d items, recycle=%s", app.name, len(items), recycle)

    # Registry and services first, while the files they name still exist.
    order = {SERVICE: 0, TASK: 1, REG_VALUE: 2, REG_KEY: 3, SHORTCUT: 4, FILE: 5,
             FOLDER: 6}
    for index, artifact in enumerate(sorted(items, key=lambda a: order[a.kind])):
        if cancel is not None and cancel.is_set():
            break
        if progress is not None:
            progress(artifact, index, len(items))
        try:
            if artifact.kind in (FOLDER, FILE, SHORTCUT):
                ok, detail = _remove_path(artifact, recycle, other_dirs, child_dirs)
            elif artifact.kind in (REG_KEY, REG_VALUE):
                ok, detail = _remove_reg(artifact, report.backup_dir, index)
            elif artifact.kind == SERVICE:
                ok, detail = _remove_service(artifact, report.backup_dir, index)
            else:
                ok, detail = _remove_task(artifact, report.backup_dir, index)
        except Exception as exc:  # pragma: no cover - one bad item must not stop the rest
            log.exception("[remove] %s", artifact.location)
            ok, detail = False, str(exc)
        log.info("[remove] %s %s %s %s", "ok  " if ok else "FAIL", artifact.kind,
                 artifact.location, detail)
        report.results.append(ItemResult(artifact, ok, detail))

    applog.write_report({
        "app": app.name, "version": app.version, "recycled": recycle,
        "backup_dir": report.backup_dir,
        "items": [{"kind": r.artifact.kind, "location": r.artifact.location,
                   "bytes": r.artifact.size_bytes, "ok": r.ok, "detail": r.detail}
                  for r in report.results],
    })
    return report
