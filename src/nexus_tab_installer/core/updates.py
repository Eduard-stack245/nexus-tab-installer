"""Did a mod that was downloaded get a newer version?

Nexus tells this in two steps. The date of the last change of all mods costs one request, and only
the mods changed after the download are asked about their files. A change of the date alone is not
an update, an author may just have edited the description. What counts is the file:

- the downloaded file has been moved to the old versions, and a main file is newer;
- or the downloaded file is still a main one, and a newer main file has the same name or another version.

Files that were never main (optional parts, patches) are not compared: nothing replaces them for sure.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

MAIN_FAMILY = ('MAIN', 'OLD_VERSION', 'ARCHIVED', 'REMOVED')
MARGIN_SECONDS = 86400  # clocks differ; one more question costs little
MAX_MODS_ASKED = 100


@dataclass
class Owned:
    game: str
    mod_id: int
    title: str = ''
    file_ids: tuple = ()
    when: float = 0.0  # when the newest file of this mod was downloaded


@dataclass
class Update:
    game: str
    mod_id: int
    title: str
    file_id: int
    version: str = ''
    name: str = ''
    size: int = 0

    @property
    def key(self):
        return self.game, self.mod_id


def owned_mods(sessions):
    """{(game, mod_id): Owned} from what lies in the session folders."""
    owned = {}
    for session in sessions:
        moment = session.when.timestamp()
        for archive in session.archives:
            if not (archive.game and archive.mod_id):
                continue
            mod = owned.setdefault((archive.game, archive.mod_id), Owned(archive.game, archive.mod_id))
            mod.title = mod.title or archive.title
            mod.when = max(mod.when, moment)
            if archive.file_id and archive.file_id not in mod.file_ids:
                mod.file_ids += (archive.file_id,)
    return owned


def _name_without_version(name):
    """Real names carry the version: "BEES-1_3" and "BEES-1_4" are one file."""
    return re.sub(r'[\W\d_]+', '', re.sub(r'\bv?\d[\d._-]*', '', str(name).casefold()))


def _continuing(candidates, family):
    """Of several main files, the one that continues what was downloaded: by name, then by size, then the newest."""
    names = [_name_without_version(row['name']) for row in family]
    size = max(row['size'] for row in family)
    return max(candidates, key=lambda row: (round(max(SequenceMatcher(None, _name_without_version(row['name']), name).ratio() for name in names), 2),
                                            -abs(row['size'] - size), row['date']))


def newer_file(owned, files):
    """The file that replaces what the person has, or None."""
    mine = [row for row in files if row['file_id'] in owned.file_ids]
    mains = [row for row in files if row['category'] == 'MAIN' and row['file_id'] not in owned.file_ids]
    if not mains:
        return None
    if owned.file_ids and not mine:
        # The downloaded files are gone from the site altogether: only the date is left.
        newest = max(mains, key=lambda row: row['date'])
        return newest if newest['date'] > owned.when else None
    if not owned.file_ids:
        # Downloaded without knowing the file. A newer main file is all there is to go by.
        newest = max(mains, key=lambda row: row['date'])
        return newest if newest['date'] > owned.when else None
    family = [row for row in mine if row['category'] in MAIN_FAMILY]
    if not family:
        return None
    newest_mine = max(row['date'] for row in family)
    names = {row['name'] for row in family}
    versions = {row['version'] for row in family}
    still_main = any(row['category'] == 'MAIN' for row in family)
    candidates = [row for row in mains if row['date'] > newest_mine
                  and (not still_main or row['name'] in names or row['version'] not in versions)]
    return _continuing(candidates, family) if candidates else None


def find_updates(sessions, infos_of, files_of):
    """[Update]. infos_of and files_of are mod_infos and mod_files of the GraphQL module."""
    owned = owned_mods(sessions)
    if not owned:
        return []
    infos = infos_of(list(owned))
    changed = [(key, mod) for key, mod in owned.items()
               if key in infos and (not infos[key].updated_at or infos[key].updated_at + MARGIN_SECONDS > mod.when)]
    updates = []
    for key, mod in changed[:MAX_MODS_ASKED]:
        info = infos[key]
        row = newer_file(mod, files_of(mod.mod_id, info.game_number))
        if row is not None:
            updates.append(Update(mod.game, mod.mod_id, info.name or mod.title, row['file_id'], row['version'], row['name'], row['size']))
    return updates


def load_found(path):
    """What the last check has found, and when it ran."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return float(data['checked_at']), [Update(**row) for row in data['found']]
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, []


def save_found(path, updates, now=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps({'checked_at': time.time() if now is None else now,
                                     'found': [asdict(update) for update in updates]}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
