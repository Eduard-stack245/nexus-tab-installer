"""Deterministic latest MAIN file selection for Nexus Mods v1 file metadata."""
from datetime import datetime, timezone


def _time(file):
    raw = file.get('uploaded_timestamp')
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    raw = file.get('uploaded_time') or ''
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=timezone.utc).timestamp() if datetime.fromisoformat(raw.replace('Z', '+00:00')).tzinfo is None else datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    return 0.0


def latest_main(files):
    """Choose most recently uploaded MAIN file, never silently choose optional/archived."""
    main = [f for f in files if str(f.get('category_name', '')).upper() == 'MAIN' and str(f.get('file_id', '')).isdigit()]
    if not main:
        return None
    return max(main, key=lambda f: (_time(f), int(f['file_id'])))
