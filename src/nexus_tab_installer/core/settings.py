"""Portable settings. Sensitive API credentials are stored only with explicit opt-in.

A portable directory cannot silently use OS-bound encryption across computers;
users must protect copies of data/config/config.json if they opt in.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

FIELDS = {"folder", "destination_mode", "mo2_ini", "pack_path", "remember_api_key", "api_key", "browser_token", "auto_free_enabled", "mo2_profile", "pace_gap", "pace_batch", "pace_rest", "pace_daily", "vortex_exe", "default_target", "vortex_folder", "game_folder", "browser_download_folder", "browser_paired", "download_pace", "language", "notify_enabled", "auto_scan", "requirements_mode", "updates_auto", "known_elsewhere"}


def load_settings(path: Path) -> dict:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
        return {k: v for k, v in obj.items() if k in FIELDS and isinstance(v, (str, bool))}
    except (OSError, ValueError, UnicodeError):
        return {}


def save_settings(path: Path, settings: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: v for k, v in settings.items() if k in FIELDS and isinstance(v, (str, bool))}
    if not cleaned.get("remember_api_key"):
        cleaned.pop("api_key", None)
    # Don't leak pre-existing credential via a backup or temporary cleartext file.
    temporary = path.with_name(path.name + '.tmp')
    try:
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(cleaned, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
