"""Downloaded archives live in sessions: one folder per download run, named by its date and time.

    data/sessions/2026-11-06 19-07-12/Some Mod-123-1-0.zip
                                      Other Mod-456-2-1.7z
                                      collection.json   (hidden: which Nexus file each archive is)

An archive lies in the session that downloaded it and is never copied. The folder on disk is the
truth: an archive that was deleted or moved away simply stops being listed. Manifests only add
details to files that are really there, so they can never point outside their own folder.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock

from nexus_tab_installer.core.display import clean_mod_title

MANIFEST = 'collection.json'
NAME_FORMAT = '%Y-%m-%d %H-%M-%S'
_FOLDER_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})')
_UNFINISHED = ('.part', '.tmp')
_lock = RLock()


@dataclass
class Archive:
    path: Path
    size: int
    title: str = ''
    game: str = ''
    mod_id: int = 0
    file_id: int = 0


@dataclass
class Session:
    folder: Path
    when: datetime
    archives: list[Archive] = field(default_factory=list)

    @property
    def size(self):
        return sum(archive.size for archive in self.archives)


def _hide(path):
    """The person opens a session folder to see archives, not our notes about them."""
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 2)


def _write_manifest(folder, data):
    manifest = Path(folder) / MANIFEST
    temporary = manifest.with_name(MANIFEST + '.tmp')
    with _lock:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Windows refuses to replace a file while anyone reads it, and that includes an antivirus.
        for attempt in range(40):
            try:
                temporary.replace(manifest)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
        _hide(manifest)


def _read_manifest(folder):
    try:
        with _lock:  # never during a write of this program: see above
            text = (Path(folder) / MANIFEST).read_text(encoding='utf-8')
        data = json.loads(text)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _records(data, key):
    entries = data.get(key)
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def same_content(first, second):
    first, second = Path(first), Path(second)
    if first.stat().st_size != second.stat().st_size:
        return False
    return file_hash(first) == file_hash(second)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def place_archive(source, folder):
    """Move an archive into a session folder. Different files with one name both stay."""
    source, folder = Path(source), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if target.exists() and target.samefile(source):
        return target
    for number in itertools.count(2):
        if not target.exists():
            break
        if same_content(source, target):
            source.unlink()
            return target
        target = folder / f'{source.stem} ({number}){source.suffix}'
    shutil.move(str(source), str(target))
    return target


def record_archive(path, game, mod_id, file_id):
    """Note in the session next to the archive which Nexus file it is."""
    path = Path(path)
    with _lock:
        data = _read_manifest(path.parent)
        files = [entry for entry in _records(data, 'files') if entry.get('name') != path.name]
        files.append({'game': str(game), 'mod_id': int(mod_id), 'file_id': int(file_id),
                      'name': path.name, 'bytes': path.stat().st_size})
        data['files'] = files
        _write_manifest(path.parent, data)
    return path


class ModCollection:
    """One download run. Its folder is removed again if nothing ends up in it."""

    def __init__(self, folder):
        self.folder = Path(folder)

    @classmethod
    def create(cls, root, mods, *, clock=None):
        moment = (clock or datetime.now)()
        name = moment.strftime(NAME_FORMAT)
        for attempt in itertools.count(1):
            folder = Path(root) / (name if attempt == 1 else f'{name} ({attempt})')
            try:
                folder.mkdir(parents=True)
                break
            except FileExistsError:
                continue
        _write_manifest(folder, {'id': folder.name, 'created_at': name,
                                 'mods': [{'game': m['game'], 'mod_id': int(m['mod_id']),
                                           'title': str(m.get('title') or ''), 'url': m.get('url', '')}
                                          for m in mods],
                                 'files': []})
        return cls(folder)

    def set_selection(self, chosen):
        with _lock:
            data = _read_manifest(self.folder)
            data['selection'] = [{'game': item['game'], 'mod_id': item['mod_id'], 'destination': target}
                                 for item, target in chosen]
            _write_manifest(self.folder, data)

    def discard_if_empty(self):
        _remove_if_empty(self.folder)


def _remove_if_empty(folder):
    """Only a folder holding nothing but its manifest goes away."""
    with _lock:
        try:
            if all(entry.name == MANIFEST for entry in folder.iterdir()):
                (folder / MANIFEST).unlink(missing_ok=True)
                folder.rmdir()
        except OSError:
            pass


def remove_archive(path, discard):
    """One file out of its session; `discard` decides where it goes. An emptied session goes too."""
    path = Path(path)
    with _lock:
        discard(path)
        data = _read_manifest(path.parent)
        if data:
            data['files'] = [entry for entry in _records(data, 'files') if entry.get('name') != path.name]
            _write_manifest(path.parent, data)
        _remove_if_empty(path.parent)


def remove_session(folder, discard):
    """A whole session with its notes, so that restoring it from the Recycle Bin restores all of it."""
    with _lock:
        discard(Path(folder))


def remove_empty_sessions(root):
    """A run that was interrupted may have left its empty folder behind."""
    root = Path(root)
    if root.is_dir():
        for folder in root.iterdir():
            if folder.is_dir():
                _remove_if_empty(folder)


def archives_in(folder):
    return sorted((path for path in Path(folder).iterdir()
                   if path.is_file() and not path.is_symlink() and path.name != MANIFEST
                   and not path.name.startswith('.') and not path.name.endswith(_UNFINISHED)),
                  key=lambda path: path.name.lower())


def _folder_time(folder):
    match = _FOLDER_TIME.match(folder.name)
    if match:
        try:
            return datetime.strptime(match[1], NAME_FORMAT)
        except ValueError:
            pass
    return datetime.fromtimestamp(folder.stat().st_mtime)


def list_sessions(root):
    """Sessions with archives, the newest first."""
    root = Path(root)
    if not root.is_dir():
        return []
    sessions = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        files = archives_in(folder)
        if not files:
            continue
        data = _read_manifest(folder)
        recorded = {entry.get('name'): entry for entry in _records(data, 'files')}
        titles = {(str(mod.get('game')), _number(mod.get('mod_id'))): str(mod.get('title') or '')
                  for mod in _records(data, 'mods')}
        archives = []
        for path in files:
            entry = recorded.get(path.name, {})
            game, mod_id = str(entry.get('game') or ''), _number(entry.get('mod_id'))
            title = clean_mod_title(titles.get((game, mod_id), ''), game, mod_id)
            archives.append(Archive(path, path.stat().st_size, '' if title == f'Мод #{mod_id}' else title,
                                    game, mod_id, _number(entry.get('file_id'))))
        sessions.append(Session(folder, _folder_time(folder), archives))
    return sorted(sessions, key=lambda session: (session.when, session.folder.name), reverse=True)


class ArchiveIndex:
    """What has already been downloaded, so that the site is not asked for it again."""

    def __init__(self, root):
        self.archives = [archive for session in list_sessions(root) for archive in session.archives]

    def find(self, game, mod_id, file_id):
        """The archive of this exact Nexus file."""
        if not file_id:
            return None
        wanted = (str(game), int(mod_id), int(file_id))
        return next((archive for archive in self.archives
                     if archive.size > 0 and (archive.game, archive.mod_id, archive.file_id) == wanted), None)

    def recent(self, game, mod_id, max_age, now=None):
        """The newest fresh archive of a mod whose main file is not known yet (no API key)."""
        now = time.time() if now is None else now
        fresh = [(archive.path.stat().st_mtime, archive) for archive in self.archives
                 if archive.size > 0 and archive.game == str(game) and archive.mod_id == int(mod_id)]
        fresh = [entry for entry in fresh if now - entry[0] <= max_age]
        return max(fresh, key=lambda entry: entry[0])[1] if fresh else None
