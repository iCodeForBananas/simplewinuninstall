"""Remove a few items with Administrator rights, without restarting the window.

The wizard runs unelevated. When some leftovers fail because they need
Administrator - a kernel driver's service, a machine-wide registry key - this
writes a job file listing just those items, starts this same program elevated
with `--remove-job <file>` (Windows shows its UAC prompt), waits for it, and
reads the results back.

The elevated process goes through remover.remove like any other removal, so
every item is vetted by the guards again, and backed up first, over there.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from threading import Event
from typing import List, Optional, Sequence, Tuple

from hudkit import applog
from hudkit.platform_win import IS_WINDOWS

from . import remover
from .artifacts import Artifact
from .held import _build
from .inventory import InstalledApp
from .uninstaller import _SEE_MASK_NOCLOSEPROCESS, _SHELLEXECUTEINFOW, _win32

FLAG = "--remove-job"
_JOB_NAME = re.compile(r"^job-[0-9a-f]{32}\.json$")
_SW_HIDE = 0
_WAIT_TIMEOUT = 0x102
_GIVE_UP_SECONDS = 15 * 60

DECLINED = "Administrator was not granted"


def _job_dir() -> str:
    path = os.path.join(applog.data_dir(), "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def _command(job: str) -> Tuple[str, str, Optional[str]]:
    """(program, parameters, working folder) that re-runs this program."""
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline([FLAG, job]), None
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return (sys.executable,
            subprocess.list2cmdline(["-m", "winuninstall", FLAG, job]), root)


def remove_elevated(app: InstalledApp, items: Sequence[Artifact],
                    other_dirs: Sequence[str], child_dirs: Sequence[str],
                    recycle: bool, allow_installed: bool,
                    cancel: Optional[Event] = None
                    ) -> Tuple[Optional[List[remover.ItemResult]], str]:
    """(results, error). Results is None if it never ran (UAC declined)."""
    log = applog.get()
    api = _win32()
    if api is None:
        return None, "Only available on Windows."
    k, s = api

    job_id = uuid.uuid4().hex
    folder = _job_dir()
    job = os.path.join(folder, f"job-{job_id}.json")
    out = os.path.join(folder, f"result-{job_id}.json")
    with open(job, "w", encoding="utf-8") as fh:
        json.dump({"app": asdict(app), "items": [asdict(a) for a in items],
                   "other_dirs": list(other_dirs), "child_dirs": list(child_dirs),
                   "recycle": recycle, "allow_installed": allow_installed,
                   "out": out}, fh)

    try:
        program, params, cwd = _command(job)
        info = _SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
        info.fMask = _SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = program
        info.lpParameters = params
        info.lpDirectory = cwd
        info.nShow = _SW_HIDE
        log.info("[elevated] asking for Administrator to remove %d item(s)", len(items))
        if not s.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
            log.info("[elevated] declined or could not start")
            return None, DECLINED

        started = time.monotonic()
        try:
            while k.WaitForSingleObject(info.hProcess, 250) == _WAIT_TIMEOUT:
                if cancel is not None and cancel.is_set():
                    break
                if time.monotonic() - started > _GIVE_UP_SECONDS:
                    log.error("[elevated] gave up waiting")
                    break
        finally:
            k.CloseHandle(info.hProcess)

        try:
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.error("[elevated] no result: %s", exc)
            return None, "The elevated helper did not report back."
        by_id = {a.id: a for a in items}
        results = [remover.ItemResult(by_id[r["id"]], bool(r["ok"]), r.get("detail", ""))
                   for r in data.get("results", []) if r.get("id") in by_id]
        return results, ""
    finally:
        for path in (job, out):
            try:
                os.remove(path)
            except OSError:
                pass


def run_job(job: str) -> int:
    """The elevated side. Called from the command line with FLAG."""
    applog.setup(console=False)
    log = applog.get()
    if not _JOB_NAME.match(os.path.basename(job)):
        log.error("[elevated] refusing an unexpected job file: %s", job)
        return 2
    try:
        with open(job, encoding="utf-8") as fh:
            data = json.load(fh)
        app = _build(InstalledApp, data["app"])
        items = [_build(Artifact, a) for a in data["items"]]
        out = data["out"]
    except (OSError, ValueError, KeyError, TypeError):
        log.exception("[elevated] unreadable job %s", job)
        return 2

    log.info("[elevated] job for %s: %d item(s)", app.name, len(items))
    try:
        report = remover.remove(
            app, items, data.get("other_dirs", []), data.get("child_dirs", []),
            recycle=bool(data.get("recycle", True)),
            allow_installed=bool(data.get("allow_installed", False)))
        results = [{"id": r.artifact.id, "ok": r.ok, "detail": r.detail}
                   for r in report.results]
    except remover.StillInstalled:
        results = [{"id": a.id, "ok": False,
                    "detail": f"{app.name} is installed again"} for a in items]
    except Exception as exc:  # pragma: no cover - report, never crash silently
        log.exception("[elevated] job failed")
        results = [{"id": a.id, "ok": False, "detail": str(exc)} for a in items]

    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"results": results}, fh)
    return 0
