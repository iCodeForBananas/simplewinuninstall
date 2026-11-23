"""Leftovers that could not be removed, kept until you deal with them.

Once an app's own uninstaller has run, the app is gone from Windows' installed
list, so a leftover that fails to go - a service that needs Administrator, a
folder that is in use - would have nowhere to be found again. Every failure is
written to %LOCALAPPDATA%\\SimpleWinUninstall\\held.json, and the first screen
lists it as "<App> (held leftovers)" until it is removed or found gone.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Dict, List, Optional

from hudkit import applog

from .artifacts import FILE, FOLDER, SERVICE, SHORTCUT, Artifact
from .inventory import InstalledApp

FILE_NAME = "held.json"
_PATH_KINDS = (FOLDER, FILE, SHORTCUT)


@dataclass
class HeldApp:
    app: InstalledApp
    artifacts: List[Artifact] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)   # artifact id -> why
    held_at: Dict[str, str] = field(default_factory=dict)   # artifact id -> when

    @property
    def id(self) -> str:
        return "held:" + self.app.id


def default_path() -> str:
    return os.path.join(applog.data_dir(), FILE_NAME)


def _build(cls, data: dict):
    """A dataclass from a dict, ignoring keys a newer or older version wrote."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _read(path: str) -> List[HeldApp]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        applog.get().exception("[held] could not read %s", path)
        return []
    out: List[HeldApp] = []
    for group in data.get("apps", []):
        try:
            held = HeldApp(app=_build(InstalledApp, group["app"]))
            for entry in group.get("items", []):
                item = _build(Artifact, entry["artifact"])
                held.artifacts.append(item)
                held.details[item.id] = entry.get("detail", "")
                held.held_at[item.id] = entry.get("held_at", "")
        except (KeyError, TypeError):
            applog.get().exception("[held] skipping an unreadable entry")
            continue
        if held.artifacts:
            out.append(held)
    return out


def _write(path: str, groups: List[HeldApp]) -> None:
    data = {"version": 1, "apps": [
        {"app": asdict(g.app),
         "items": [{"artifact": asdict(a), "detail": g.details.get(a.id, ""),
                    "held_at": g.held_at.get(a.id, "")} for a in g.artifacts]}
        for g in groups if g.artifacts]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _still_there(item: Artifact) -> bool:
    if item.kind in _PATH_KINDS:
        return os.path.lexists(item.location)
    if item.kind == SERVICE and item.name:
        # A driver marked for deletion goes at the next restart.
        from .remover import service_exists
        return service_exists(item.name)
    return True


def load(path: Optional[str] = None) -> List[HeldApp]:
    """Everything still held. Anything already gone is dropped."""
    path = path or default_path()
    groups = _read(path)
    changed = False
    for g in groups:
        keep = [a for a in g.artifacts if _still_there(a)]
        if len(keep) != len(g.artifacts):
            g.artifacts = keep
            changed = True
    groups = [g for g in groups if g.artifacts]
    if changed:
        try:
            _write(path, groups)
        except OSError:
            applog.get().exception("[held] could not prune %s", path)
    return groups


def record(app: InstalledApp, results, path: Optional[str] = None) -> int:
    """Fold one removal's results in: drop what went, hold what did not.

    `results` is a sequence of remover.ItemResult. Returns how many items are
    now held for this app.
    """
    path = path or default_path()
    groups = _read(path)
    group = next((g for g in groups if g.app.id == app.id), None)
    if group is None:
        group = HeldApp(app=app)
        groups.append(group)
    by_id = {a.id: a for a in group.artifacts}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in results:
        aid = r.artifact.id
        if r.ok:
            by_id.pop(aid, None)
            group.details.pop(aid, None)
            group.held_at.pop(aid, None)
        else:
            by_id[aid] = r.artifact
            group.details[aid] = r.detail
            group.held_at.setdefault(aid, stamp)
    group.artifacts = list(by_id.values())
    _write(path, groups)
    if group.artifacts:
        applog.get().info("[held] %s: %d item(s) held for later", app.name,
                          len(group.artifacts))
    return len(group.artifacts)
