"""Nexus file list normalization and a persisted, explicit per-mod selection.

No Flet or HTTP code. A file is identified by its Nexus file_id, not name or
version string: one version may contain several distinct downloadable files.
"""
from __future__ import annotations
from dataclasses import dataclass
from .file_kinds import is_plain_file
from .file_selection import latest_main, _time

CATEGORIES = ('MAIN', 'UPDATE', 'OPTIONAL', 'MISCELLANEOUS', 'OLD_VERSION')
BLOCKED_CATEGORIES = ('ARCHIVED', 'REMOVED', 'DELETED')
CATEGORY_LABELS = {
    'MAIN': 'Основные файлы',
    'UPDATE': 'Обновления',
    'OPTIONAL': 'Дополнительные файлы',
    'MISCELLANEOUS': 'Разное',
    'OLD_VERSION': 'Старые версии',
}


def catalog(files):
    """Defensively normalize Nexus v1 files with deterministic category ordering."""
    seen = set()
    result = []
    for raw in files if isinstance(files, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            fid = int(raw['file_id'])
            if fid <= 0 or fid in seen:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(fid)
        category = str(raw.get('category_name') or 'MISCELLANEOUS').upper()
        state = str(raw.get('file_status') or '').lower()
        available = (category not in BLOCKED_CATEGORIES
                     and state not in ('removed', 'archived', 'deleted')
                     and raw.get('available') is not False)
        try:
            size_kb = max(0, int(float(raw.get('size_kb') or 0)))
        except (ValueError, TypeError):
            size_kb = 0
        result.append({
            'file_id': fid,
            'category_name': category,
            'file_name': str(raw.get('file_name') or raw.get('name') or f'File #{fid}'),
            'name': str(raw.get('name') or raw.get('file_name') or f'File #{fid}'),
            'version': str(raw.get('version') or '—'),
            'size_kb': size_kb,
            'uploaded_timestamp': _time(raw),
            'available': available,
        })
    result.sort(key=lambda f: (CATEGORIES.index(f['category_name']) if f['category_name'] in CATEGORIES else len(CATEGORIES),
                                -f['uploaded_timestamp'], -f['file_id']))
    return result


def default_files(files):
    """The latest MAIN archive and every plain MAIN file; never auto-add patches/optional.

    Archives of one mod are alternatives or versions of one another, so one is enough. Files
    without an archive are the mod itself, split into parts: one of three would be a broken mod.
    Of plain files sharing a name only the newest counts, the rest are its older versions.
    """
    listed = [f for f in catalog(files) if f['available']]
    main = [f for f in listed if f['category_name'] == 'MAIN']
    newest_plain = {}
    for file in main:
        if is_plain_file(file['file_name']):
            known = newest_plain.get(file['file_name'].casefold())
            if known is None or (file['uploaded_timestamp'], file['file_id']) > (known['uploaded_timestamp'], known['file_id']):
                newest_plain[file['file_name'].casefold()] = file
    archive = latest_main([f for f in main if not is_plain_file(f['file_name'])])
    return [f['file_id'] for f in main if f is archive or newest_plain.get(f['file_name'].casefold()) is f]


@dataclass
class FileChoice:
    mode: str = 'auto'  # auto = latest MAIN at moment of download
    file_ids: tuple[int, ...] = ()

    def as_dict(self):
        return {'mode': self.mode, 'file_ids': list(self.file_ids)}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or raw.get('mode') != 'manual':
            return cls()
        ids = []
        for value in raw.get('file_ids', []) if isinstance(raw.get('file_ids'), list) else []:
            try:
                fid = int(value)
                if fid > 0 and fid not in ids:
                    ids.append(fid)
            except (TypeError, ValueError):
                continue
        return cls('manual', tuple(ids))

    def resolve(self, files):
        listed = catalog(files)
        if self.mode == 'auto':
            return [f for f in listed if f['file_id'] in default_files(listed)]
        lookup = {f['file_id']: f for f in listed}
        missing = [fid for fid in self.file_ids if fid not in lookup or not lookup[fid]['available']]
        if missing:
            raise ValueError('Выбранные файлы Nexus недоступны: ' + ', '.join(str(x) for x in missing))
        return [lookup[fid] for fid in self.file_ids]

    def toggle(self, file_id, enabled, files):
        listed = catalog(files)
        allowed = {f['file_id'] for f in listed if f['available']}
        file_id = int(file_id)
        if file_id not in allowed:
            raise ValueError('Выбранный файл недоступен')
        ids = list(default_files(listed) if self.mode == 'auto' else self.file_ids)
        if enabled and file_id not in ids:
            ids.append(file_id)
        if not enabled and file_id in ids:
            ids.remove(file_id)
        self.mode, self.file_ids = 'manual', tuple(ids)

    def reset(self):
        self.mode, self.file_ids = 'auto', ()
