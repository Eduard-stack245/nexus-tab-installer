"""Conservative import of browser-completed, user-approved Nexus files.

A browser may send the file via native download instead of nxm://. This watcher
accepts only an *explicitly selected* file with its Nexus API filename, in an
allowed Downloads directory, created after the batch starts, whose size has
stabilized. It never reads unrelated downloads or watches arbitrary directories.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re
import time

from nexus_tab_installer.core.file_kinds import is_program

UNFINISHED_EXTENSIONS = {'.crdownload', '.part', '.partial', '.download', '.opdownload', '.tmp'}


def browser_download_folders(extra=None):
    paths = [Path.home() / 'Downloads']
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                downloads, _ = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')
                paths.append(Path(os.path.expandvars(downloads)))
        except (OSError, ImportError):
            pass
    if extra:
        paths.append(Path(extra).expanduser())
    # Do not create directories or broaden scanning to the home directory.
    return tuple(dict.fromkeys(p.resolve() for p in paths if p.is_dir()))


def _looks_complete(path):
    """An archive must start like one. Any other file has no signature to check."""
    try:
        with path.open('rb') as handle:
            signature = handle.read(8)
        extension = path.suffix.lower()
        if extension == '.zip':
            return signature.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'))
        if extension == '.7z':
            return signature.startswith(b'7z\xbc\xaf\x27\x1c')
        if extension == '.rar':
            return signature.startswith((b'Rar!\x1a\x07\x00', b'Rar!\x1a\x07\x01\x00'))
        return True
    except OSError:
        return False


def _equivalent(browser_name, expected):
    """Only exact filename, or browser's (N) collision suffix before extension."""
    a, b = Path(browser_name).name, Path(expected).name
    if a.casefold() == b.casefold():
        return True
    stem, ext = os.path.splitext(b)
    return bool(re.fullmatch(re.escape(stem) + r' \([1-9][0-9]?\)' + re.escape(ext), a, flags=re.I))


@dataclass
class _Pending:
    game: str
    mod_id: int
    file_id: int
    filename: str
    size_kb: int
    started: float
    observed: tuple | None = None
    seen_at: float = 0.0


class BrowserDownloadWatcher:
    def __init__(self, folders, *, clock=time.time, steady_seconds=1.0):
        self.folders = tuple(Path(folder).resolve() for folder in folders if Path(folder).is_dir())
        self.clock = clock
        self.steady_seconds = steady_seconds
        self.pending: dict[tuple, _Pending] = {}

    def register(self, game, mod_id, file_id, filename, size_kb=0):
        if not filename or Path(filename).name != filename or is_program(filename):
            return False
        key = (game, int(mod_id), int(file_id))
        self.pending[key] = _Pending(*key, filename, int(size_kb or 0), self.clock())
        return True

    def forget(self, game, mod_id, file_id):
        return self.pending.pop((game, int(mod_id), int(file_id)), None) is not None

    def clear(self):
        self.pending.clear()

    def poll(self):
        """Return [(game, mod_id, file_id, Path)] once a file is stable.

        A browser download that is still being written is never imported.
        An ambiguous filename matching multiple selected jobs is never guessed.
        """
        now = self.clock()
        candidates = {}
        for folder in self.folders:
            try:
                for p in folder.iterdir():
                    if p.suffix.lower() in UNFINISHED_EXTENSIONS or not p.is_file() or p.is_symlink():
                        continue
                    try:
                        stat = p.stat()
                    except OSError:
                        continue
                    for key, job in self.pending.items():
                        if not _equivalent(p.name, job.filename):
                            continue
                        if stat.st_mtime < job.started - 2 or stat.st_size <= 0 or stat.st_size > 30*1024**3:
                            continue
                        if job.size_kb:
                            expected = job.size_kb * 1024
                            # Nexus size_kb is sometimes rounded; tolerate 2% or 64KiB.
                            tolerance = max(int(expected * .02), 65536)
                            if abs(stat.st_size - expected) > tolerance:
                                continue
                        candidates.setdefault(p, []).append((key, stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
        # One candidate file can satisfy only one pending task, otherwise it is ambiguous.
        identified = {}
        for path, matches in candidates.items():
            if len(matches) == 1:
                key, size, mtime = matches[0]
                identified.setdefault(key, []).append((path, size, mtime))
        completed = []
        for key, options in identified.items():
            if len(options) != 1:
                continue
            path, size, mtime = options[0]
            job = self.pending[key]
            stamp = (str(path), size, mtime)
            if job.observed != stamp:
                job.observed, job.seen_at = stamp, now
            elif now - job.seen_at >= self.steady_seconds and _looks_complete(path):
                completed.append((*key, path))
                del self.pending[key]
        return completed
