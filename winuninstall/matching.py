"""Does this folder name belong to this app? Pure string logic.

The rule that matters: whole names only, never substrings. "steam" must match
Steam and SteamLibrary, and must not match upstream, mainstream, Steamworks or
steam_api64.dll sitting inside some other game.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

EXACT = "exact"
PREFIX = "prefix"

# Never a token on its own: too generic, or shared by unrelated products.
STOP_WORDS = {
    "app", "apps", "application", "applications", "application data",
    "bin", "browser", "build", "cache", "caches", "client", "common",
    "config", "core", "data", "default", "desktop", "dist", "docs",
    "documents", "driver", "drivers", "edition", "extensions", "files",
    "framework", "game", "games", "helper", "installer", "launcher", "lib",
    "local", "locallow", "logs", "main", "manager", "media", "packages",
    "player", "plugin", "plugins", "program", "programs", "public",
    "redistributable", "roaming", "runtime", "service", "services",
    "settings", "setup", "shared", "software", "src", "studio", "suite",
    "support", "system", "temp", "test", "tests", "tmp", "tool", "tools",
    "uninstall", "uninstaller", "update", "updater", "user", "users",
    "microsoft", "windows", "google", "apple", "intel", "amd", "nvidia",
}

# Executable names that say nothing about which app they came from.
GENERIC_EXE = STOP_WORDS | {
    "au", "uninst", "unins000", "unins001", "unwise", "msiexec", "rundll32",
    "cmd", "powershell", "wscript", "start", "run", "launch", "icon", "icons",
}

_PUBLISHER_NOISE = re.compile(
    r"\b(corporation|corp|incorporated|inc|llc|ltd|limited|gmbh|co|company|"
    r"software|technologies|technology|studios?|s\.?a\.?|b\.?v\.?|ag)\b\.?",
    re.IGNORECASE)
_NAME_NOISE = re.compile(
    r"\(.*?\)|\[.*?\]|"                            # (x64 en-US), [64-bit]
    r"\b(x64|x86|amd64|arm64|64[- ]?bit|32[- ]?bit)\b|"
    r"\bv?\d+(?:\.\d+)+\S*|"                       # 1.2.3, v2.0-beta
    r"\s\d+$",                                     # trailing " 3"
    re.IGNORECASE)
_GUID = re.compile(r"^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                   r"[0-9a-f]{12}\}?$", re.IGNORECASE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def compact(text: str) -> str:
    """Lower-case with everything but letters and digits removed."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_words(text: str) -> List[str]:
    """SteamLibrary, steam-library and "Steam Library" all give the same."""
    spaced = _CAMEL.sub(" ", text)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def clean_name(display_name: str) -> str:
    """The product name without its version, architecture or locale."""
    return re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", display_name)).strip(" -_.,")


def clean_publisher(publisher: str) -> str:
    return re.sub(r"\s+", " ", _PUBLISHER_NOISE.sub(" ", publisher)).strip(" -_.,")


def is_guid(text: str) -> bool:
    return bool(_GUID.match(text.strip()))


def usable(token: str, whole_name: bool = False) -> bool:
    """Is this specific enough to identify an app by?

    Three letters is allowed only when it is the app's entire name (Git, VLC).
    """
    if not token or token in STOP_WORDS or token.isdigit():
        return False
    return len(token) >= (3 if whole_name else 4)


def name_tokens(display_name: str, publisher: str = "") -> Set[str]:
    """Tokens derived from the name shown in Apps & Features."""
    cleaned = clean_name(display_name)
    out = set()
    whole = compact(cleaned)
    if usable(whole, whole_name=True):
        out.add(whole)

    # "Mozilla Firefox" should also find a folder called just "Firefox".
    pub = clean_publisher(publisher)
    pub_words = {w for w in split_words(pub)}
    remaining = [w for w in split_words(cleaned) if w not in pub_words]
    if remaining and len(remaining) < len(split_words(cleaned)):
        short = compact("".join(remaining))
        if usable(short):
            out.add(short)
    return out


def publisher_token(publisher: str) -> str:
    token = compact(clean_publisher(publisher))
    return token if usable(token) else ""


def publisher_folder(publisher: str) -> str:
    """What the vendor's folder is called. Unlike publisher_token this keeps
    names like Google: too shared to delete, but fine to look inside."""
    return compact(clean_publisher(publisher))


def exe_token(exe_stem: str) -> str:
    token = compact(exe_stem)
    if token in GENERIC_EXE or not usable(token) or token.startswith("unins"):
        return ""
    return token


def strip_version(name: str) -> str:
    """"Foo 1.2.3" and "Foo-1.2" compare equal to "Foo"."""
    return re.sub(r"[ _.-]+v?\d+(?:\.\d+)*$", "", name)


def match_name(name: str, tokens: Iterable[str],
               allow_prefix: bool = True) -> Optional[str]:
    """EXACT if the name is a token, PREFIX if it starts with a whole token."""
    tokens = set(tokens)
    if not tokens:
        return None
    if compact(name) in tokens or compact(strip_version(name)) in tokens:
        return EXACT
    if allow_prefix:
        words = split_words(name)
        for count in range(1, len(words)):
            head = "".join(words[:count])
            if len(head) >= 5 and head in tokens:
                return PREFIX
    return None
