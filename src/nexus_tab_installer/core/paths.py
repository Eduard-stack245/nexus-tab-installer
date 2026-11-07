"""Portable, relocatable application data layout.

All application-owned data lives under <application>/data. External manager
locations are opt-in destinations and are never part of the app state.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def application_root() -> Path:
    override = os.environ.get("NEXUS_TAB_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # .../src/nexus_tab_installer/core/paths.py -> repository root
    return Path(__file__).resolve().parents[3]


def data_root() -> Path:
    return application_root() / "data"


def portable_path(value):
    """How a path goes into the settings: inside the program folder it stays relative,
    so the whole folder can be moved to another place or a flash drive."""
    if not value:
        return value
    try:
        return Path(value).expanduser().resolve().relative_to(application_root()).as_posix()
    except (ValueError, OSError):
        return str(value)


def saved_path(value):
    """The real path of a value from the settings, wherever the program folder is now."""
    if not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return str(application_root() / path)
    # Early builds wrote absolute paths, which still point to the old place.
    parts = [part.lower() for part in path.parts]
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == 'data' and parts[index + 1] in ('packs', 'library'):
            return str(data_root().joinpath(*path.parts[index + 1:]))
    return str(value)


def ensure_layout() -> Path:
    base = data_root()
    for relative in ("config", "library/manual_imports", "packs",
                     "history", "sessions", "queue", "nxm/inbox", "staging", "managed-installs", "logs"):
        (base / relative).mkdir(parents=True, exist_ok=True)
    return base


def storage_paths() -> dict[str, Path]:
    root = data_root()
    return {"base":root, "config":root/"config"/"config.json",
            "pack":root/"packs"/"my-mods.json", "sessions":root/"sessions", "downloads":root/"sessions",
            "old_downloads":root/"library"/"downloads", "retired_sessions":root/"history"/"old-sessions",
            "transfers":root/"queue"/"transfers",
            "imports":root/"library", "history":root/"history"/"download-history.json",
            "free":root/"queue"/"free-pending.json", "nxm":root/"nxm"/"inbox",
            "pace":root/"queue"/"download-pace.json", "unfinished":root/"queue"/"unfinished-batch.json", "updates":root/"queue"/"updates.json",
            "staging":root/"staging", "managed":root/"managed-installs"}
