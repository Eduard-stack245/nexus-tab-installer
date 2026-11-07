"""One move from the layout of older versions to one flat folder per download run.

In early builds an archive lay in library/downloads/<game>/<mod>/<file>/ and again in
sessions/<time>_<id>/archives/<game>/<mod>/<file>/ of every run that used it.

Nothing unique is ever removed. Archives are moved, and a copy goes away only after its hash
proved it byte for byte equal to the one that stays. An archive stays where it was if anything
goes wrong with it, and the next start tries again.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nexus_tab_installer.core.session_library import (MANIFEST, ModCollection, file_hash, place_archive,
                                                      record_archive)

OLD_SESSION = re.compile(r'^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_[0-9a-f]+$')
UNFINISHED = ('.part', '.tmp')
SAME_RUN_SECONDS = 30 * 60


@dataclass
class Found:
    path: Path
    game: str
    mod_id: int
    file_id: int
    session: Path | None
    when: datetime


def _identity(path, root):
    """<game>/<mod>/<file or 'unsorted'>/<name>; anything else is an archive of an unknown file."""
    parts = path.relative_to(root).parts
    if len(parts) == 4 and parts[1].isdigit():
        return parts[0], int(parts[1]), int(parts[2]) if parts[2].isdigit() else 0
    return '', 0, 0


def _files(root):
    return (path for path in sorted(root.rglob('*')) if path.is_file() and not path.is_symlink()
            and not path.name.startswith('.') and not path.name.endswith(UNFINISHED)
            and path.name != MANIFEST) if root.is_dir() else ()


def _old_time(folder):
    match = OLD_SESSION.match(folder.name)
    return datetime.strptime(f'{match[1]} {match[2]}', '%Y-%m-%d %H-%M-%S')


def _manifest(folder):
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remove_empty_tree(root):
    """Empty folders only: rmdir refuses anything else."""
    if not root.is_dir():
        return
    for path in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def _content_groups(found):
    """Copies of one file together. Only files of the same size need to be read at all."""
    by_size = {}
    for item in found:
        by_size.setdefault(item.path.stat().st_size, []).append(item)
    groups = []
    for items in by_size.values():
        if len(items) == 1:
            groups.append(items)
            continue
        by_hash = {}
        for item in items:
            by_hash.setdefault(file_hash(item.path), []).append(item)
        groups.extend(by_hash.values())
    return groups


def migrate_old_layout(sessions, old_library, retired):
    """Returns the number of archives now in new session folders."""
    sessions, old_library, retired = Path(sessions), Path(old_library), Path(retired)
    old_sessions = sorted(p for p in sessions.iterdir() if p.is_dir() and OLD_SESSION.match(p.name)) \
        if sessions.is_dir() else []
    manifests = {folder: _manifest(folder) for folder in old_sessions}
    found = []
    for folder in old_sessions:
        archives = folder / 'archives'
        for path in _files(archives):
            found.append(Found(path, *_identity(path, archives), folder, _old_time(folder)))
    for path in _files(old_library):
        found.append(Found(path, *_identity(path, old_library), None, datetime.fromtimestamp(path.stat().st_mtime)))
    if not found and not old_sessions:
        return 0

    titles = {}
    for data in manifests.values():
        for mod in data.get('mods') or []:
            if isinstance(mod, dict) and mod.get('title'):
                titles[(str(mod.get('game')), int(mod.get('mod_id') or 0))] = mod

    # Each file goes to the first run that had it. A file known only to the old library
    # goes to a run made of the files that were downloaded around the same time.
    groups = [sorted(group, key=lambda item: (item.session is None, item.when)) for group in _content_groups(found)]
    homes, loose = {}, []
    for group in groups:
        if group[0].session is not None:
            homes.setdefault(group[0].session, []).append(group)
        else:
            loose.append(group)
    bundles = []
    for group in sorted(loose, key=lambda g: g[0].when):
        if bundles and (group[0].when - bundles[-1][-1][0].when).total_seconds() <= SAME_RUN_SECONDS:
            bundles[-1].append(group)
        else:
            bundles.append([group])

    def move_in(batch_groups, moment):
        mods = {}
        for group in batch_groups:
            key = (group[0].game, group[0].mod_id)
            if group[0].game:
                mods[key] = titles.get(key) or {'game': group[0].game, 'mod_id': group[0].mod_id}
        batch = ModCollection.create(sessions, list(mods.values()), clock=lambda: moment)
        placed = 0
        for group in batch_groups:
            keeper = group[0]
            try:
                saved = place_archive(keeper.path, batch.folder)
                if keeper.game:
                    record_archive(saved, keeper.game, keeper.mod_id, keeper.file_id)
                placed += 1
            except OSError:
                continue
            for copy in group[1:]:
                try:
                    copy.path.unlink()  # the hash has shown that it equals the one that stays
                except OSError:
                    pass
        batch.discard_if_empty()
        return placed

    moved = 0
    for folder in old_sessions:
        if folder in homes:
            moved += move_in(homes[folder], _old_time(folder))
    for bundle in bundles:
        moved += move_in(bundle, bundle[0][0].when)

    for folder in old_sessions:
        _remove_empty_tree(folder / 'archives')
        # The note of an old run holds the list of links it was given: kept, but not among the downloads.
        if (folder / MANIFEST).is_file() and all(entry.name == MANIFEST for entry in folder.iterdir()):
            retired.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder / MANIFEST), str(retired / f'{folder.name}.json'))
        try:
            folder.rmdir()
        except OSError:
            pass
    _remove_empty_tree(old_library)
    return moved
