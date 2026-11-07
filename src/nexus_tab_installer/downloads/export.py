"""Copy an archive to an explicitly configured external mod-manager inbox.

The portable library retains its original. No game or MO2 mods files are touched.
"""
from __future__ import annotations
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


def _sha(path: Path):
    d = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            d.update(chunk)
    return d.digest()


def free_target(source: Path, folder: Path):
    """(place for the file, whether the same file is already there). Nothing gets overwritten."""
    target = folder / source.name
    if target.exists():
        if target.is_file() and _sha(source) == _sha(target):
            return target, True
        for n in range(1, 10000):
            candidate = folder / f'{source.stem} ({n}){source.suffix}'
            if not candidate.exists():
                return candidate, False
        raise ValueError('Слишком много одноимённых файлов.')
    return target, False


def put_file(source: Path, target: Path, link: bool = False):
    """Puts the file in place at once or not at all. `link` saves the disk space when it can:
    a hard link is one more name of the same file, and removing either name leaves the other."""
    if link:
        temporary = target.with_name(f'.nexus-copy-{os.urandom(6).hex()}')
        try:
            os.link(source, temporary)
            os.replace(temporary, target)
            return target
        except OSError:
            Path(temporary).unlink(missing_ok=True)  # another disk: an ordinary copy will do
    fd, temp = tempfile.mkstemp(prefix='.nexus-copy-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as dst, source.open('rb') as src:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if _sha(source) != _sha(Path(temp)):
            raise IOError('Контрольная сумма передачи не совпадает.')
        os.replace(temp, target)
        return target
    finally:
        Path(temp).unlink(missing_ok=True)


def export_archive(source: str | Path, destination: str | Path, link: bool = False) -> Path:
    source, folder = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or not folder.is_dir():
        raise ValueError('Архив или выбранная папка менеджера не существует.')
    if source.parent == folder:
        return source
    target, already = free_target(source, folder)
    return target if already else put_file(source, target, link=link)
