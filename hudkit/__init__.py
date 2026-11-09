"""hudkit - the shared look and window shell for the Simple* Windows utilities.

This is a copy. SimpleWinClean carries the same package, so a change worth
having in both has to be made in both.

Standard library only. An app gets the whole skin by subclassing
`shell.WizardApp` and writing its steps:

    palette       colour tokens (no tkinter, so build tools can import it)
    theme         fonts, ttk styling, Canvas widgets, display scaling
    logo          the mark, as pure geometry
    chrome        the frameless window
    shell         WizardApp / Step: header, footer, navigation, worker queue
    paths         path comparison and the directories nothing may ever touch
    platform_win  ctypes wrappers for the Windows APIs the apps need
    applog        per-app plain-text log and JSON reports

Colours live in palette.py and nowhere else. If a hex value appears outside
that file, it is a bug.
"""
