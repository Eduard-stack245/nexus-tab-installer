"""Import user-downloaded archives, never extracting or writing into game folders."""
from pathlib import Path
import shutil
import re

class ImportArchiveError(Exception):
    pass

def import_archive(source, library_root):
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in ('.zip', '.7z', '.rar'):
        raise ImportArchiveError('Выберите существующий архив ZIP, 7z или RAR.')
    if source.stat().st_size == 0:
        raise ImportArchiveError('Архив пустой.')
    if source.stat().st_size > 30 * 1024**3:
        raise ImportArchiveError('Архив слишком большой для автоматического импорта (30 GB).')
    name = re.sub(r'[^\w.() +\-]', '_', source.name)[:180]
    destdir = Path(library_root).expanduser().resolve() / 'manual_imports'
    destdir.mkdir(parents=True, exist_ok=True)
    dest = destdir / name
    if dest == source:
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 1
    while dest.exists():
        if dest.stat().st_size == source.stat().st_size:
            import hashlib
            def digest(path):
                h = hashlib.sha256()
                with path.open('rb') as f:
                    for block in iter(lambda: f.read(1024*1024), b''):
                        h.update(block)
                return h.digest()
            if digest(dest) == digest(source):
                return dest
        dest = destdir / f'{stem} ({i}){suffix}'
        i += 1
    partial = dest.with_name(dest.name + '.partial')
    try:
        with source.open('rb') as inp, partial.open('xb') as out:
            shutil.copyfileobj(inp, out, length=1024*1024)
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)
    return dest
