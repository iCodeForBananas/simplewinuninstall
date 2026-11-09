"""SimpleWinUninstall - remove an app, then find what it left behind.

Runs the app's own uninstaller, then searches the known locations, the
registry and the whole C: drive for leftovers, and shows you every one before
anything is removed. No telemetry, no network code, standard library only.
"""
from hudkit import applog as _applog

__version__ = "0.1.0"
APP_NAME = "SimpleWinUninstall"

_applog.configure(APP_NAME)
