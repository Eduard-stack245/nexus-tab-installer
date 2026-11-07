"""Safe archive inspection and isolated extraction. Game directories are never touched."""
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from nexus_tab_installer.downloads.transfer import DownloadError, safe_name

MAX_FILES = 30000
MAX_UNPACKED = 8 * 1024**3
MAX_RATIO = 1000


def _validate_members(members):
    """members: (name, size, compressed_size, is_dir, is_link) tuples."""
    total, count, seen = 0, 0, set()
    for name, size, compressed, is_dir, is_link in members:
        normalized_name = name.replace('\\', '/')
        parts = PurePosixPath(normalized_name).parts
        if (not normalized_name or normalized_name.startswith('/') or
            re.match(r'^[A-Za-z]:', normalized_name) or
            '..' in parts or '\x00' in normalized_name or is_link or
            any(':' in part for part in parts)):
            raise DownloadError(f'Опасный путь или ссылка: {name[:120]}')
        key = '/'.join(parts).casefold().rstrip('/')
        if key in seen:
            raise DownloadError('Архив содержит повторяющиеся имена файлов.')
        seen.add(key)
        if not is_dir:
            count += 1
            total += size
            if size < 0 or total > MAX_UNPACKED:
                raise DownloadError('Архив превышает безопасный лимит распаковки (8 ГБ).')
            if size > 1024 * 1024 and compressed is not None and size / max(1, compressed) > MAX_RATIO:
                raise DownloadError('Подозрительно высокая степень сжатия.')
        if len(seen) > MAX_FILES:
            raise DownloadError('Слишком много файлов в архиве.')
    return count, total


def inspect_archive(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise DownloadError('Архив не найден.')
    ext = path.suffix.lower()
    try:
        if ext == '.zip':
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                members = [(i.filename, i.file_size, i.compress_size, i.is_dir(),
                            stat.S_IFMT(i.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)) for i in infos]
                count, total = _validate_members(members)
        elif ext == '.rar':
            try:
                import rarfile
            except ImportError as exc:
                raise DownloadError('Установите поддержку RAR: pip install rarfile') from exc
            with rarfile.RarFile(path) as archive:
                infos = archive.infolist()
                members = [(i.filename, i.file_size, i.compress_size, i.isdir(),
                            bool(getattr(i, 'is_symlink', lambda: False)())) for i in infos]
                count, total = _validate_members(members)
        elif ext == '.7z':
            try:
                import py7zr
            except ImportError as exc:
                raise DownloadError('Установите поддержку 7z: pip install py7zr') from exc
            with py7zr.SevenZipFile(path, mode='r') as archive:
                infos = archive.list()
                members = [(i.filename, i.uncompressed or 0, getattr(i,'compressed',None),
                            i.is_directory, getattr(i,'is_symlink',False)) for i in infos]
                count, total = _validate_members(members)
        else:
            raise DownloadError('Поддерживаются архивы ZIP, RAR и 7z.')
        return {'files': count, 'bytes': total, 'name': path.name, 'format': ext[1:]}
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise DownloadError(f'Ошибка чтения архива: {exc}') from exc


def stage_archive(path, staging_root):
    path = Path(path).expanduser().resolve()
    info = inspect_archive(path)
    root = Path(staging_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (safe_name(path.stem) + '-' + __import__('hashlib').sha256(str(path).encode()).hexdigest()[:10])
    if destination.exists():
        raise DownloadError('Архив уже подготовлен. Удалите папку staging для повторной обработки.')
    temp = Path(tempfile.mkdtemp(prefix='.nexus-stage-', dir=root))
    try:
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    target = temp.joinpath(*PurePosixPath(entry.filename.replace('\\','/')).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open('xb') as sink:
                        shutil.copyfileobj(source, sink, 1024*1024)
        elif path.suffix.lower() == '.rar':
            import rarfile
            with rarfile.RarFile(path) as archive:
                for entry in archive.infolist():
                    if entry.isdir():
                        continue
                    target = temp.joinpath(*PurePosixPath(entry.filename.replace('\\','/')).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open('xb') as sink:
                        shutil.copyfileobj(source, sink, 1024*1024)
        else:
            import py7zr
            with py7zr.SevenZipFile(path, mode='r') as archive:
                archive.extractall(path=temp)
        # Defense in depth: never publish links or unexpected expanded content.
        actual_count, actual_size = 0, 0
        for current, dirs, files in os.walk(temp, followlinks=False):
            for name in dirs + files:
                entry = Path(current)/name
                if entry.is_symlink():
                    raise DownloadError('После распаковки обнаружена символическая ссылка.')
            for name in files:
                entry = Path(current)/name
                actual_count += 1
                actual_size += entry.stat().st_size
                if actual_size > MAX_UNPACKED or actual_count > MAX_FILES:
                    raise DownloadError('Превышен лимит распакованных файлов.')
        if actual_count != info['files']:
            raise DownloadError('Число файлов после распаковки не совпадает с архивом.')
        temp.rename(destination)
        return destination, info
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

# Compatibility with earlier UI/tests.
def inspect_zip(path):
    if Path(path).suffix.lower() != '.zip':
        raise DownloadError('Ожидается ZIP-архив.')
    return inspect_archive(path)

def stage_zip(path, staging_root):
    if Path(path).suffix.lower() != '.zip':
        raise DownloadError('Ожидается ZIP-архив.')
    return stage_archive(path, staging_root)
