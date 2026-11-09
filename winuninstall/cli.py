"""Command-line entry point. With no arguments it opens the wizard.

--list and --scan never change anything, so you can read exactly what would be
offered before trusting the app with a real uninstall.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import APP_NAME, __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="winuninstall",
        description=f"{APP_NAME} {__version__} - uninstall an app and clear "
                    "what it left behind.")
    p.add_argument("--version", action="version",
                   version=f"{APP_NAME} {__version__}")
    p.add_argument("--list", action="store_true", help="print installed apps")
    p.add_argument("--scan", metavar="NAME",
                   help="print what would be offered for this app; "
                        "changes nothing, and does not run its uninstaller")
    p.add_argument("--no-sweep", action="store_true",
                   help="with --scan, skip the whole-drive sweep")
    p.add_argument("--json", action="store_true",
                   help="with --scan, print JSON instead of a table")
    p.add_argument("--selftest", action="store_true",
                   help="run the safety-guard tests and exit")
    p.add_argument("--classic", action="store_true",
                   help="use the platform's default widget styling")
    p.add_argument("--native-frame", action="store_true",
                   help="keep the skin but use the normal Windows title bar")
    return p


def do_list() -> int:
    from hudkit.paths import human_size

    from .inventory import list_store_apps, list_win32_apps

    apps = list_win32_apps() + list_store_apps()
    for app in apps:
        size = human_size(app.size_bytes) if app.size_bytes else ""
        print(f"{app.name[:50]:<50}  {app.publisher[:28]:<28}  {size:>10}")
    print(f"\n{len(apps)} apps.")
    return 0


def do_scan(args) -> int:
    from hudkit import applog
    from hudkit.paths import human_size

    from .artifacts import SURE, ScanRoots, scan
    from .inventory import list_win32_apps

    applog.setup(console=False)
    apps = list_win32_apps()
    wanted = args.scan.lower()
    matches = [a for a in apps if a.name.lower() == wanted] \
        or [a for a in apps if wanted in a.name.lower()]
    if len(matches) != 1:
        names = ", ".join(a.name for a in matches[:10]) or "none"
        print(f"Need exactly one app matching {args.scan!r}. Found: {names}")
        return 2

    report = scan(matches[0], apps,
                  roots=ScanRoots.for_this_machine(sweep=not args.no_sweep))
    if args.json:
        print(json.dumps({
            "app": report.app.name,
            "artifacts": [vars(a) for a in report.artifacts],
            "excluded": report.excluded,
            "children": [c.name for c in report.children],
            "duration_seconds": round(report.duration, 1),
        }, indent=2))
        return 0

    print(f"\n{report.app.name}: {len(report.artifacts)} candidates, "
          f"{human_size(report.total_bytes)}, in {report.duration:.0f}s")
    print("[x] ticked by default   [ ] check first\n")
    for a in sorted(report.artifacts, key=lambda a: (a.kind, a.confidence != SURE)):
        size = human_size(a.size_bytes) if a.size_bytes else ""
        print(f"[{'x' if a.confidence == SURE else ' '}] {a.kind:<14} "
              f"{size:>10}  {a.location}")
        if a.warning:
            print(f"{'':30}! {a.warning}")
    if report.children:
        print(f"\nInstalled through this app: {len(report.children)}")
    if report.excluded:
        print(f"\nLeft alone by the guards ({len(report.excluded)}):")
        for path, why in report.excluded:
            print(f"    {path}  <- {why}")
    print("\nNothing was changed.")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        from .selftest import run
        return run()
    if args.list:
        return do_list()
    if args.scan:
        return do_scan(args)
    from .gui import main as gui_main
    return gui_main(classic=args.classic, native_frame=args.native_frame)


if __name__ == "__main__":
    sys.exit(main())
