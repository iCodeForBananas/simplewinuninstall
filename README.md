<p align="center"><img src="assets/logo.png" width="128" alt="Banana logo"></p>

# SimpleWinUninstall

Removes a Windows app, then finds what it left behind. Standard library only,
no telemetry, no network code.

```
Pick an app  ->  Its own uninstaller runs  ->  Scan for leftovers  ->  Review  ->  Remove
```

```bash
python run.py
```

To see what would be offered for an app without changing anything, and without
running its uninstaller:

```bash
python run.py --scan Steam
```

## What it looks for

| Where | How it decides |
|---|---|
| Install folder | The app's own registry entry |
| AppData, ProgramData, Program Files, Temp | Folder named after the app, including `Vendor\Product` layouts |
| Start menu, Desktop, taskbar pins | Shortcut named after the app, or pointing into its folder |
| Registry | `Software\<App>`, `Software\<Vendor>\<App>` |
| `steam://` style links, Run keys, App Paths, services, scheduled tasks | Only if the entry points into the app's folder |
| The whole C: drive | Folders with exactly the app's name, skipping Windows and other apps |

A few apps scatter things under other names. Those are listed in
[`catalog.py`](winuninstall/catalog.py): Steam, Epic, Battle.net, EA, Ubisoft and GOG.

## What gets ticked

**Ticked by default:** the install folder, known locations, exact names where
apps keep their folders, and anything proven to point into the app's folder.

**Listed but unticked ("Check first"):**

- anything found only by the drive sweep
- partial name matches
- anything in Documents or on the root of the drive
- folders holding browser profiles, game saves or games (Steam's `steamapps`)
- any name another installed app also claims (Python 3.10 and 3.13)

**Never listed, whatever you tick:** Windows, shared folders like
`Program Files` itself, anything inside another installed app, links and
junctions, this program, `Software\Microsoft`, COM and file-type registrations.
Matching is by whole name, so `steam` never matches `upstream` or
`steam_api64.dll`. The guards are in [`guards.py`](winuninstall/guards.py) and run again
immediately before each removal.

## Undoing it

Files go to the Recycle Bin by default. If something is too big for the bin,
Windows asks before deleting it for good. Registry keys, services and tasks are
exported to `%LOCALAPPDATA%\SimpleWinUninstall\backups` first, and if the backup
fails the item is left alone. To restore a key, double-click its `.reg` file.

If an uninstaller is cancelled the app is still installed, so the scan stops
there and warns you. "Scan anyway" exists for broken uninstallers.

## Building

```bash
pyinstaller winuninstall.spec
```

## Tests

```bash
python run.py --selftest
```

Works in a throwaway folder and registry key. No real app is touched.

## The shared theme

The look is shared with SimpleWinClean and lives in [`hudkit`](hudkit/__init__.py). A new tool subclasses
`WizardApp`, lists its steps and gets the window, header, buttons and worker
thread for free. Colours are defined in `hudkit/palette.py` and nowhere else,
and the self-test fails if a hex value appears anywhere else.
