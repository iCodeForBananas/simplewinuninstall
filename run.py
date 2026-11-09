#!/usr/bin/env python3
"""Entry point for running SimpleWinUninstall from source and for its build.

    python run.py                 # open the wizard
    python run.py --list          # print installed apps
    python run.py --scan Steam    # print leftovers, delete nothing
    python run.py --selftest      # run the safety-guard tests
"""
import sys

from winuninstall.cli import main

if __name__ == "__main__":
    sys.exit(main())
