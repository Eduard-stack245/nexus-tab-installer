"""Is there room for what is about to be downloaded? A warning, the decision stays with the person."""
from __future__ import annotations

import shutil
from pathlib import Path

RESERVE = 300 * 1024 ** 2  # the system and other programs need some room as well


def _existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def free_bytes(path):
    return shutil.disk_usage(_existing(path)).free


def _drive(path):
    return Path(_existing(path)).resolve().anchor.casefold() or str(path)


def shortages(folders, needed=0, free_of=free_bytes):
    """[(folder, free bytes)] for each disk among these folders that has too little room.

    Every file passes through all of them, so each has to hold the whole amount.
    """
    problems, seen = [], set()
    for folder in folders:
        if not folder:
            continue
        drive = _drive(folder)
        if drive in seen:
            continue
        seen.add(drive)
        try:
            free = free_of(folder)
        except OSError:
            continue
        if free < needed + RESERVE:
            problems.append((Path(folder), free))
    return problems
