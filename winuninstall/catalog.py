"""What the generic matcher cannot guess about a few notorious apps.

Most apps need nothing here: their leftovers are named after them. These are
the ones that scatter data under another name (Steam keeps registry settings
under "Valve") or that keep things you might want inside their own folder.

Entries only add candidates. Everything found through here still passes the
same guards as any other match, and a listed path is only reported if it
exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Family:
    pattern: str                          # matched against the display name
    aliases: Tuple[str, ...] = ()         # other names its folders go by
    paths: Tuple[str, ...] = ()           # may use %ENV% variables
    registry: Tuple[str, ...] = ()        # "HKCU\\Software\\X" / "HKLM32\\..."
    keep_markers: Tuple[str, ...] = ()    # subfolders that mean "your stuff"
    keep_note: str = ""


FAMILIES = (
    Family(
        pattern=r"^steam$",
        aliases=("SteamLibrary",),
        paths=(
            r"%ProgramFiles(x86)%\Common Files\Steam",
            r"%ProgramFiles(x86)%\Steam",
            r"%ProgramData%\Steam",
            r"%LOCALAPPDATA%\Steam",
            r"%APPDATA%\Steam",
            r"%SystemDrive%\SteamLibrary",
        ),
        registry=(
            r"HKCU\Software\Valve",
            r"HKLM32\SOFTWARE\Valve",
            r"HKLM64\SOFTWARE\Valve",
        ),
        keep_markers=("steamapps", "userdata"),
        keep_note="Holds your installed games, screenshots or local saves.",
    ),
    Family(
        pattern=r"^epic games launcher$",
        aliases=("EpicGamesLauncher", "Epic Games", "UnrealEngineLauncher"),
        paths=(
            r"%LOCALAPPDATA%\EpicGamesLauncher",
            r"%LOCALAPPDATA%\UnrealEngineLauncher",
            r"%ProgramData%\Epic",
            r"%ProgramFiles%\Epic Games",
        ),
        registry=(r"HKCU\Software\Epic Games", r"HKLM32\SOFTWARE\EpicGames"),
        keep_markers=("*",),
        keep_note="May hold your installed games.",
    ),
    Family(
        pattern=r"^battle\.net$",
        aliases=("Battle.net", "Blizzard Entertainment"),
        paths=(
            r"%ProgramData%\Battle.net",
            r"%ProgramData%\Blizzard Entertainment",
            r"%APPDATA%\Battle.net",
            r"%LOCALAPPDATA%\Battle.net",
            r"%LOCALAPPDATA%\Blizzard Entertainment",
        ),
        registry=(r"HKCU\Software\Blizzard Entertainment",
                  r"HKLM32\SOFTWARE\Blizzard Entertainment"),
    ),
    Family(
        pattern=r"^(ea app|ea desktop|origin)$",
        aliases=("Electronic Arts", "EA Desktop"),
        paths=(
            r"%LOCALAPPDATA%\Electronic Arts",
            r"%ProgramData%\Electronic Arts",
            r"%ProgramData%\EA Desktop",
        ),
        registry=(r"HKLM32\SOFTWARE\Electronic Arts",),
    ),
    Family(
        pattern=r"^ubisoft connect$",
        aliases=("Ubisoft", "Ubisoft Game Launcher"),
        paths=(
            r"%LOCALAPPDATA%\Ubisoft Game Launcher",
            r"%ProgramFiles(x86)%\Ubisoft",
        ),
        registry=(r"HKLM32\SOFTWARE\Ubisoft",),
        keep_markers=("games", "savegames"),
        keep_note="Holds your installed games or local saves.",
    ),
    Family(
        pattern=r"^gog galaxy$",
        aliases=("GOG.com",),
        paths=(r"%ProgramData%\GOG.com", r"%LOCALAPPDATA%\GOG.com"),
        registry=(r"HKLM32\SOFTWARE\GOG.com",),
    ),
)


def family_for(display_name: str) -> Optional[Family]:
    for family in FAMILIES:
        if re.search(family.pattern, display_name.strip(), re.IGNORECASE):
            return family
    return None
