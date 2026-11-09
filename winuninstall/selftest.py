"""Safety tests. Everything happens in a throwaway folder and a throwaway
registry key; no real app is touched and no uninstaller is run.

    python run.py --selftest
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import sys
import tempfile
from typing import List

from hudkit import applog
from hudkit.platform_win import IS_WINDOWS

from . import artifacts, catalog, guards, inventory, matching, remover, uninstaller
from .artifacts import REVIEW, SURE, Artifact, RegRoot, ScanRoots
from .inventory import InstalledApp
from .matching import EXACT, PREFIX, match_name, name_tokens

_failures: List[str] = []
_checks = {"n": 0}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST_REG = r"Software\ZZSimpleWinUninstallSelfTest"
TEST_UNINSTALL = "ZZSimpleWinUninstallSelfTest"


def check(condition: bool, message: str) -> None:
    _checks["n"] += 1
    print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        _failures.append(message)


def test_matching() -> None:
    print("\n[1] whole names only, never substrings")
    tokens = {"steam", "steamlibrary"}
    check(match_name("Steam", tokens) == EXACT, "Steam matches")
    check(match_name("STEAM", tokens) == EXACT, "case does not matter")
    check(match_name("SteamLibrary", tokens) == EXACT, "alias matches")
    check(match_name("Steam Backup", tokens) == PREFIX, "whole-word prefix is partial")
    check(match_name("steam-cache", tokens) == PREFIX, "hyphen is a word break")
    for name in ("upstream", "mainstream", "Steamworks", "steam_api64.dll",
                 "Upstream Steam", "steamy"):
        check(match_name(name, tokens) != EXACT, f"{name} is not an exact match")
    for name in ("upstream", "mainstream", "Steamworks", "steamy"):
        check(match_name(name, tokens) is None, f"{name} does not match at all")
    check(match_name("Foo 1.2.3", {"foo"}) == EXACT, "version suffix ignored")
    check(match_name("Git LFS", {"git"}) is None, "3-letter tokens get no prefix match")

    print("\n[2] tokens from display names")
    check(name_tokens("Steam") == {"steam"}, "plain name")
    check("firefox" in name_tokens("Mozilla Firefox (x64 en-US)", "Mozilla"),
          "publisher word stripped, locale dropped")
    check("7zip" in name_tokens("7-Zip 24.08 (x64)", "Igor Pavlov"), "version dropped")
    check("git" in name_tokens("Git", "The Git Development Community"),
          "three letters allowed when it is the whole name")
    check(not name_tokens("Launcher"), "stop word alone yields nothing")
    check("launcher" not in name_tokens("Epic Games Launcher", "Epic Games, Inc."),
          "publisher stripping cannot leave a generic word")
    check(matching.exe_token("unins000") == "", "uninstaller exe names ignored")
    check(matching.exe_token("Code") == "code", "real exe names kept")
    fam = catalog.family_for("Steam")
    check(fam is not None and "steamapps" in fam.keep_markers,
          "Steam is in the catalog and flags steamapps")
    check(catalog.family_for("Steam Deck Tools") is None,
          "catalog does not match on a partial name")


def test_guards() -> None:
    print("\n[3] folder guard")
    env = os.environ
    pf = env.get("ProgramFiles", r"C:\Program Files")
    data = env.get("ProgramData", r"C:\ProgramData")
    win = env.get("WINDIR", r"C:\Windows")
    prof = env.get("USERPROFILE", r"C:\Users\me")
    local = env.get("LOCALAPPDATA", os.path.join(prof, "AppData", "Local"))
    roaming = env.get("APPDATA", os.path.join(prof, "AppData", "Roaming"))
    blocked = [
        "C:\\", r"C:\Windows", win, os.path.join(win, "System32", "foo"),
        pf, data, prof, local, roaming, os.path.dirname(prof),
        os.path.join(pf, "Common Files"), os.path.join(pf, "WindowsApps", "x"),
        os.path.join(local, "Programs"), os.path.join(local, "Microsoft"),
        os.path.join(roaming, "Microsoft", "Windows", "Foo"),
        os.path.join(prof, "Documents"), os.path.join(prof, "Desktop"),
        os.path.join(prof, "AppData"), r"C:\$Recycle.Bin\x",
        "%LOCALAPPDATA%\\Foo", "relative\\path", "",
        os.path.join(data, "Package Cache", "{abc}"),
    ]
    for path in blocked:
        verdict, _ = guards.vet_folder(path, check_reparse=False)
        check(verdict == guards.BLOCK, f"blocks {path or '(empty)'}")

    ok = os.path.join(pf, "Zebrafy")
    check(guards.vet_folder(ok, check_reparse=False)[0] == guards.OK,
          "an ordinary app folder is fine")
    check(guards.vet_folder(os.path.join(prof, "Documents", "Zebrafy"),
                            check_reparse=False)[0] == guards.ASK,
          "anything in Documents needs asking")
    check(guards.vet_folder(r"C:\SteamLibrary", check_reparse=False)[0] == guards.ASK,
          "top level of the drive needs asking")

    other = os.path.join(pf, "Zebrafy", "Bundled")
    check(guards.vet_folder(ok, [other], check_reparse=False)[0] == guards.BLOCK,
          "cannot remove a folder holding an unrelated installed app")
    check(guards.vet_folder(ok, [other], [other], check_reparse=False)[0] == guards.ASK,
          "a folder holding its own child apps needs asking")
    check(guards.vet_folder(other, [ok], check_reparse=False)[0] == guards.BLOCK,
          "cannot remove part of another installed app")
    check(guards.vet_folder(ok, [ok], [ok], check_reparse=False)[0] == guards.ASK,
          "child apps registered in the parent's own folder need asking")
    check(guards.vet_folder(HERE, check_reparse=False)[0] == guards.BLOCK,
          "cannot remove this program")

    print("\n[4] registry guard")
    good = [
        ("HKCU", r"Software\Valve"),
        ("HKLM", r"SOFTWARE\Valve\Steam"),
        ("HKCU", r"Software\Classes\steam"),
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam"),
    ]
    for hive, path in good:
        check(guards.vet_registry(hive, path)[0] == guards.OK, f"allows {hive}\\{path}")
    bad = [
        ("HKCU", r"Software"), ("HKLM", r"SOFTWARE\Microsoft"),
        ("HKLM", r"SOFTWARE\Microsoft\Windows"),
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        ("HKLM", r"SOFTWARE\Classes"), ("HKLM", r"SOFTWARE\Classes\.txt"),
        ("HKLM", r"SOFTWARE\Classes\CLSID"), ("HKCU", r"Software\Classes\http"),
        ("HKLM", r"SOFTWARE\Policies"), ("HKLM", r"SYSTEM\CurrentControlSet"),
        ("HKCR", r"steam"),
    ]
    for hive, path in bad:
        check(guards.vet_registry(hive, path)[0] == guards.BLOCK, f"blocks {hive}\\{path}")
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    check(guards.vet_registry("HKCU", run, "Steam")[0] == guards.OK,
          "a single Run value is removable")
    check(guards.vet_registry("HKCU", run)[0] == guards.BLOCK,
          "the Run key itself is not")


def test_commands() -> None:
    print("\n[5] uninstall command handling")
    s = inventory.split_command
    check(s(r"C:\Program Files (x86)\Steam\uninstall.exe")[0]
          == r"C:\Program Files (x86)\Steam\uninstall.exe", "unquoted path with spaces")
    check(s(r'"C:\A B\u.exe" /S') == (r"C:\A B\u.exe", "/S"), "quoted with arguments")
    check(s(r"C:\A B\u.exe /S --x") == (r"C:\A B\u.exe", "/S --x"), "unquoted with arguments")
    check(inventory.icon_path(r'"C:\A\b.exe",0') == r"C:\A\b.exe", "icon index removed")
    check(inventory._pretty_date("20261109") == "2026-11-09", "install date formatted")
    check(inventory._pretty_date("20224510") == "", "impossible install date dropped")
    check(inventory.store_display_name("Clipchamp.Clipchamp") == "Clipchamp",
          "Store vendor prefix dropped")
    check(inventory.store_publisher("CN=33F0F141-36F3-4EC2-A77D-51B53D57C2A0") == "",
          "Store certificate GUID is not shown as a publisher")

    guid = "{11111111-2222-3333-4444-555555555555}"
    msi = InstalledApp(id="x", name="X", is_msi=True, reg_key=guid,
                       uninstall_string=f"MsiExec.exe /I{guid}")
    check(uninstaller.command_for(msi) == f"msiexec.exe /X{guid}",
          "MSI /I (modify) is turned into /X (remove)")

    def dirs(**kw):
        return inventory.app_dirs(InstalledApp(id="x", name="X", **kw))

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    win = os.environ.get("WINDIR", r"C:\Windows")
    check(dirs(uninstall_string=os.path.join(pf, "Foo", "unins000.exe"))
          == [os.path.join(pf, "Foo")], "folder inferred from the uninstaller")
    check(dirs(uninstall_string=os.path.join(pf, "Foo", "bin", "un.exe"))
          == [os.path.join(pf, "Foo")], "generic bin folder climbed out of")
    check(dirs(uninstall_string=os.path.join(win, "System32", "msiexec.exe") + " /X{a}")
          == [], "msiexec does not make System32 the app's folder")
    check(dirs(install_location=pf) == [], "InstallLocation of Program Files is refused")
    check(dirs(uninstall_string=os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"), "Package Cache", "{g}", "s.exe"))
        == [], "cached installers are not the app's folder")

    print("\n[6] following a self-relaunching uninstaller")
    tree = uninstaller.ProcessTree(100)
    check(tree.update({100: 1, 200: 100}), "parent and child alive")
    check(tree.update({200: 100}), "still alive after the parent exits")
    check(not tree.update({300: 1}), "done once the child exits")
    tree = uninstaller.ProcessTree(100)
    tree.update({100: 1})
    tree.update({})
    check(not tree.update({500: 100}), "a recycled PID cannot adopt strangers")


def _mk(path: str, data: bytes = b"x" * 10) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _lnk(path: str, target: str) -> None:
    """A minimal Shell Link pointing at `target`."""
    base = target.encode("mbcs" if os.name == "nt" else "latin-1") + b"\0"
    # size, header size, flags, volume id, local base path, network, suffix
    info = struct.pack("<IIIIIII", 28 + len(base), 28, 1, 0, 28, 0, 0) + base
    header = struct.pack("<I", 0x4C) + b"\0" * 16 + struct.pack("<I", 0x02) \
        + b"\0" * 52
    _mk(path, header + info)


def test_scan() -> None:
    print("\n[7] leftover scan, in a sandbox")
    sandbox = tempfile.mkdtemp(prefix="swu-selftest-")
    try:
        pf = os.path.join(sandbox, "PF")
        roaming = os.path.join(sandbox, "Roaming")
        local = os.path.join(sandbox, "Local")
        docs = os.path.join(sandbox, "Documents")
        start = os.path.join(sandbox, "Start Menu", "Programs")
        drive = os.path.join(sandbox, "Drive")

        install = os.path.join(pf, "Zebrafy")
        other_install = os.path.join(pf, "OtherApp")
        _mk(os.path.join(install, "leftover.dat"), b"y" * 1000)
        _mk(os.path.join(other_install, "Zebrafy", "plugin.dll"))
        _mk(os.path.join(roaming, "Zebrafy", "settings.json"))
        _mk(os.path.join(local, "Acme", "Zebrafy", "cache.bin"))
        _mk(os.path.join(local, "ZebrafyCache", "a"))
        _mk(os.path.join(local, "Zebrafyer", "a"))
        _mk(os.path.join(local, "Upstream Zebrafy", "a"))
        _mk(os.path.join(local, "OtherApp Zebrafy", "a"))
        _mk(os.path.join(docs, "Zebrafy", "my-save.dat"))
        _mk(os.path.join(start, "Zebrafy.lnk"))
        _mk(os.path.join(start, "Unrelated.lnk"))
        _lnk(os.path.join(start, "Renamed.lnk"), os.path.join(install, "a.exe"))
        _lnk(os.path.join(start, "Zebrafy Other.lnk"),
             os.path.join(other_install, "a.exe"))
        _mk(os.path.join(drive, "Stuff", "Zebrafy", "a"))
        _mk(os.path.join(drive, "Code", "Zebrafy", ".git", "HEAD"))
        _mk(os.path.join(drive, "Lib", "zebrafy_api64.dll"))
        _mk(os.path.join(drive, "Lib", "upstreamzebrafy", "a"))

        target = InstalledApp(id="t", name="Zebrafy 2.1 (x64)", publisher="Acme Corp",
                              install_location=install, reg_key="Zebrafy")
        other = InstalledApp(id="o", name="OtherApp", publisher="Others Inc",
                             install_location=other_install)
        roots = ScanRoots(app_homes=[pf, roaming, local], personal=[docs],
                          shortcut_dirs=[start], sweep_root=drive, evidence=False)
        report = artifacts.scan(target, [target, other], roots)
        by_path = {a.location: a for a in report.artifacts}

        def conf(*bits):
            found = by_path.get(os.path.join(*bits))
            return found.confidence if found else None

        check(conf(install) == SURE, "install folder ticked")
        check(by_path[install].size_bytes == 1000, "folder size measured")
        check(conf(roaming, "Zebrafy") == SURE, "exact name in AppData ticked")
        check(conf(local, "Acme", "Zebrafy") == SURE, "Vendor\\Product layout found")
        check(conf(local, "Acme") == REVIEW, "publisher folder listed unticked")
        check(conf(local, "ZebrafyCache") == REVIEW, "partial name unticked")
        check(conf(docs, "Zebrafy") == REVIEW, "folder in Documents unticked")
        check(by_path[os.path.join(docs, "Zebrafy")].warning != "",
              "folder in Documents carries a warning")
        check(conf(drive, "Stuff", "Zebrafy") == REVIEW, "drive sweep result unticked")
        check(conf(start, "Zebrafy.lnk") == SURE, "shortcut by name found")
        check(conf(start, "Renamed.lnk") == SURE, "renamed shortcut found by its target")
        check(report.sweep_completed, "sweep ran to the end")

        for bits, why in (
                ((local, "Zebrafyer"), "a longer word"),
                ((local, "Upstream Zebrafy"), "name merely contains it"),
                ((local, "OtherApp Zebrafy"), "name merely contains it"),
                ((other_install, "Zebrafy"), "inside another installed app"),
                ((drive, "Code", "Zebrafy"), "it is a source repository"),
                ((drive, "Lib", "zebrafy_api64.dll"), "a DLL sharing the prefix"),
                ((drive, "Lib", "upstreamzebrafy"), "name merely contains it"),
                ((start, "Unrelated.lnk"), "unrelated shortcut"),
                ((start, "Zebrafy Other.lnk"), "points at a different app"),
        ):
            check(os.path.join(*bits) not in by_path, f"not listed: {bits[-1]} ({why})")

        _mk(os.path.join(roaming, "Sharedname", "a"))
        _mk(os.path.join(roaming, "Sharedname 3.1", "a"))
        _mk(os.path.join(start, "Sharedname 3.1", "a.lnk"))
        _mk(os.path.join(roaming, "Mailer", "Profiles", "x", "logins.json"))
        shared = InstalledApp(id="s1", name="Sharedname 3.2", reg_key="s1",
                              install_location=os.path.join(pf, "Sharedname32"))
        twin = InstalledApp(id="s2", name="Sharedname 3.1", reg_key="s2",
                            install_location=os.path.join(pf, "Sharedname31"))
        rows = {a.location: a for a in artifacts.scan(shared, [shared, twin], roots).artifacts}
        for path in (os.path.join(roaming, "Sharedname"),
                     os.path.join(roaming, "Sharedname 3.1"),
                     os.path.join(start, "Sharedname 3.1")):
            check(rows[path].confidence == REVIEW and "still installed" in rows[path].warning,
                  f"name shared with another installed version stays unticked: "
                  f"{os.path.basename(path)}")
        mailer = InstalledApp(id="m", name="Mailer", reg_key="Mailer",
                              install_location=os.path.join(pf, "Mailer"))
        row = next(a for a in artifacts.scan(mailer, [mailer], roots).artifacts
                   if a.location == os.path.join(roaming, "Mailer"))
        check(row.confidence == REVIEW and "profile" in row.warning,
              "folder holding Profiles stays unticked")
        # A vendor name too generic to be a token can still be looked inside.
        _mk(os.path.join(local, "Google", "Mapper", "a"))
        mapper = InstalledApp(id="p", name="Google Mapper", publisher="Google LLC",
                              reg_key="p", install_location=os.path.join(pf, "GMapper"))
        rows = {a.location: a for a in artifacts.scan(mapper, [mapper], roots).artifacts}
        check(os.path.join(local, "Google", "Mapper") in rows,
              "found inside a generically named vendor folder")
        check(os.path.join(local, "Google") not in rows,
              "the generically named vendor folder itself is never offered")

        print("\n[8] games installed through a launcher stay unticked")
        game = InstalledApp(id="g", name="A Game",
                            install_location=os.path.join(install, "steamapps", "A"),
                            uninstall_string=os.path.join(install, "z.exe") + " uninstall/1")
        _mk(os.path.join(install, "steamapps", "A", "a.pak"))
        report = artifacts.scan(target, [target, other, game], roots)
        row = next(a for a in report.artifacts if a.location == install)
        check(row.confidence == REVIEW and "deletes them" in row.warning,
              "launcher folder holding games warns and is unticked")
        check([c.name for c in report.children] == ["A Game"], "child app detected")
        shutil.rmtree(os.path.join(install, "steamapps"))

        print("\n[9] removal")
        item = next(a for a in artifacts.scan(target, [target, other], roots).artifacts
                    if a.location == os.path.join(roaming, "Zebrafy"))
        blocked = Artifact(artifacts.FOLDER, other_install, "test")
        result = remover.remove(target, [item, blocked], [other_install],
                                recycle=False, allow_installed=True)
        check(not os.path.exists(item.location), "ticked folder removed")
        check(os.path.exists(other_install), "guard stops a stale list at removal time")
        check(len(result.failed) == 1 and "blocked" in result.failed[0].detail,
              "the blocked item is reported, and did not stop the rest")
        missing = Artifact(artifacts.FOLDER, os.path.join(pf, "AlreadyGone"), "test")
        check(remover.remove(target, [missing], recycle=False,
                             allow_installed=True).results[0].ok,
              "something already gone counts as done")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_registry() -> None:
    if not IS_WINDOWS:
        return
    import winreg

    print("\n[10] registry scan and removal, in a throwaway key")
    uninstall_path = inventory.UNINSTALL_KEY + "\\" + TEST_UNINSTALL
    backup = ""
    try:
        for sub in ("Zebrafy\\Inner", "Upstream Zebrafy", "Zebrafyer"):
            winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{TEST_REG}\\{sub}")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, uninstall_path) as h:
            winreg.SetValueEx(h, "DisplayName", 0, winreg.REG_SZ, "Zebrafy")
            winreg.SetValueEx(h, "UninstallString", 0, winreg.REG_SZ, "nothing.exe")

        target = InstalledApp(id="t", name="Zebrafy", reg_hive="HKCU", reg_view=0,
                              reg_key=TEST_UNINSTALL)
        check(inventory.still_installed(target), "registration detected")
        check(any(a.name == "Zebrafy" and a.reg_key == TEST_UNINSTALL
                  for a in inventory.list_win32_apps()), "appears in the installed list")

        try:
            remover.remove(target, [], recycle=False)
            check(False, "removal refused while the app is installed")
        except remover.StillInstalled:
            check(True, "removal refused while the app is installed")

        roots = ScanRoots(registry=[RegRoot("HKCU", TEST_REG, 0)], evidence=False)
        report = artifacts.scan(target, [target], roots)
        keys = {a.reg_path for a in report.artifacts if a.kind == artifacts.REG_KEY}
        check(f"{TEST_REG}\\Zebrafy" in keys, "exact registry key found")
        check(f"{TEST_REG}\\Upstream Zebrafy" not in keys, "lookalike key ignored")
        check(f"{TEST_REG}\\Zebrafyer" not in keys, "longer name ignored")

        item = next(a for a in report.artifacts if a.reg_path == f"{TEST_REG}\\Zebrafy")
        result = remover.remove(target, [item], recycle=False, allow_installed=True)
        backup = result.backup_dir
        check(result.results[0].ok, "registry key removed")
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{TEST_REG}\\Zebrafy").Close()
            check(False, "key and subkeys are gone")
        except OSError:
            check(True, "key and subkeys are gone")
        exported = os.path.join(backup, "000.reg")
        check(os.path.isfile(exported) and os.path.getsize(exported) > 0,
              "exported to a .reg backup first")
    finally:
        for path in (f"{TEST_REG}\\Zebrafy\\Inner", f"{TEST_REG}\\Zebrafy",
                     f"{TEST_REG}\\Upstream Zebrafy", f"{TEST_REG}\\Zebrafyer",
                     TEST_REG, uninstall_path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass
        if backup:
            shutil.rmtree(backup, ignore_errors=True)


def test_theme_is_standardized() -> None:
    if getattr(sys, "frozen", False):
        return                 # reads the source tree, which a build lacks
    print("\n[11] one theme, one source")
    hex_colour = re.compile(r"#[0-9a-fA-F]{6}\b")
    offenders = []
    for folder in ("hudkit", "winuninstall"):
        base = os.path.join(ROOT, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith(".py") or name == "palette.py" \
                    or (name == "selftest.py"):
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if hex_colour.search(line):
                        offenders.append(f"{folder}/{name}:{lineno}")
    check(not offenders, "colours are only defined in hudkit/palette.py"
          + ("".join("\n        " + o for o in offenders) if offenders else ""))

    with open(os.path.join(HERE, "gui.py"), encoding="utf-8") as fh:
        source = fh.read()
    check("from hudkit.shell import" in source, "the wizard is built on the shared shell")
    check(not re.search(r"self\._[wh]\s*(?:,[^=\n]*)?=(?!=)", source),
          "gui.py never assigns tkinter's internal self._w")

    banned = ("SendMessage", "WM_NCLBUTTONDOWN", "ReleaseCapture", "HTCAPTION")
    found = []
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py") and name != "selftest.py":
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    code = re.sub(r"#.*$", "", line)
                    found += [f"{name}:{lineno}" for w in banned if w in code]
    check(not found, "no module starts a modal move loop")


def run() -> int:
    applog.setup(console=False)
    for test in (test_matching, test_guards, test_commands, test_scan,
                 test_registry, test_theme_is_standardized):
        test()
    print("\n" + "=" * 72)
    if _failures:
        print(f"{len(_failures)} of {_checks['n']} checks FAILED:")
        for failure in _failures:
            print(f"  -> {failure}")
        return 1
    print(f"All {_checks['n']} checks passed.")
    return 0
