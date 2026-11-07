"""Portable per-file download storage (avoids filename collisions across versions)."""
from pathlib import Path
import re


def file_directory(root, game, mod_id, file_id):
    slug = str(game).lower()
    if not re.fullmatch(r'[a-z0-9_-]{2,80}', slug):
        raise ValueError('Invalid game domain')
    mod_id, file_id = int(mod_id), int(file_id)
    if mod_id < 1 or file_id < 1:
        raise ValueError('Invalid Nexus file identity')
    return Path(root) / slug / str(mod_id) / str(file_id)
