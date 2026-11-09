# PyInstaller spec for SimpleWinUninstall.
#
#   pip install pyinstaller
#   pyinstaller winuninstall.spec
#
# Produces dist\SimpleWinUninstall.exe. No admin manifest: the app offers to
# restart elevated, and an uninstaller that needs elevation triggers its own
# UAC prompt.

a = Analysis(
    ['run.py'],
    datas=[('assets/simplewinuninstall.ico', 'assets')],
    hiddenimports=['winuninstall.gui', 'winuninstall.selftest'],
    excludes=[
        'numpy', 'pandas', 'matplotlib', 'PIL', 'scipy', 'pytest',
        'setuptools', 'pip', 'unittest', 'pydoc', 'doctest',
        'xml', 'sqlite3', 'asyncio', 'multiprocessing',
    ],
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SimpleWinUninstall',
    upx=False,
    console=False,
    uac_admin=False,
    icon='assets/simplewinuninstall.ico',
)
