"""Durable handoff from a browser nxm:// launch to the running desktop app.

Files are never removed until the GUI explicitly acknowledges that a job was
queued. This prevents losing short-lived Nexus tokens on a temporary API error.
"""
import json
import os
import time
import uuid
from pathlib import Path
from nexus_tab_installer.nexus.api import nxm_parts

from nexus_tab_installer.core.paths import storage_paths
INBOX = storage_paths()['nxm']


def submit_nxm(uri, inbox=INBOX):
    nxm_parts(uri)
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    name = f'{time.time_ns()}-{uuid.uuid4().hex}'
    temp = inbox / (name + '.tmp')
    ready = inbox / (name + '.json')
    with temp.open('x', encoding='utf-8') as f:
        if os.name != 'nt':
            os.chmod(temp, 0o600)
        json.dump({'nxm': uri, 'created': int(time.time())}, f)
    os.replace(temp, ready)
    return ready


def pending_nxm(inbox=INBOX, max_age_seconds=900):
    """Return (path, uri) records. Remove only expired or invalid records."""
    inbox = Path(inbox)
    if not inbox.exists():
        return []
    now = time.time()
    result = []
    for entry in sorted(inbox.glob('*.json'))[:100]:
        try:
            record = json.loads(entry.read_text(encoding='utf-8'))
            uri = record['nxm']
            _, _, _, _, expires, _ = nxm_parts(uri)
            if now - int(record['created']) > max_age_seconds or now >= int(expires):
                entry.unlink(missing_ok=True)
                continue
            result.append((entry, uri))
        except (ValueError, KeyError, OSError, TypeError):
            entry.unlink(missing_ok=True)
    return result


def acknowledge_nxm(path):
    Path(path).unlink(missing_ok=True)


def collect_nxm(inbox=INBOX, max_age_seconds=900):
    """Legacy consuming interface for old clients and compatibility tests."""
    records = pending_nxm(inbox, max_age_seconds)
    for path, _ in records:
        acknowledge_nxm(path)
    return [uri for _, uri in records]
