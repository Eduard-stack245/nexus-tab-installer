"""Resumable, cancellable HTTPS downloads plus safe ZIP staging.

Never attempts to manufacture or bypass a Nexus Free confirmation token.
The URL must already be authorised: returned by the Nexus API, or issued by
the Nexus website to the user's browser after its own download flow.
"""
import os
import re
import shutil
import socket
import time
import zipfile
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


class LinkRejected(DownloadError):
    """The server refused this signed link (expired, foreign IP, revoked)."""
    def __init__(self, code):
        super().__init__(f'Сервер отклонил ссылку загрузки: HTTP {code}')
        self.code = code


class RateLimited(DownloadError):
    """HTTP 429 even after waiting: the whole queue must slow down."""
    def __init__(self):
        super().__init__('Сервер загрузки просит подождать: HTTP 429')


USER_AGENT = 'NexusTabInstaller/1.0.0'
MAX_RETRY_AFTER = 900


def retry_after_seconds(headers, default):
    """The server's own Retry-After in seconds, within reason; else ``default``."""
    value = (headers.get('Retry-After', '') if headers is not None else '').strip()
    return min(int(value), MAX_RETRY_AFTER) if value.isdigit() else default


def safe_name(name):
    name = re.sub(r'[^\w. ()\[\]+-]+', '_', name, flags=re.UNICODE).strip(' .')
    return (name[:160] or 'mod-download')


def name_from_url(url):
    """File name from a CDN path such as /cdn/1704/12604/SkyUI_5_2_SE-12604-5-2SE.7z."""
    try:
        last = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
    except ValueError:
        return ''
    return last if '.' in last.strip('.') else ''


def name_from_disposition(header):
    if not header:
        return ''
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', header)
    return match.group(1).strip() if match else ''


def download(url, directory, name, on_progress=None, cancel=None, *,
             attempts=3, retry_delay=0.3, rate_delay=30, file_identity=None, opener=None, sleeper=None):
    """Download an authorised HTTPS URL, retry transient errors and resume .part files.

    Partial files contain no credentials and can be resumed with a newly-issued
    CDN URL. If a server ignores Range, restart cleanly instead of appending.
    Atomic final rename prevents incomplete archives appearing in the library.
    ``name`` may be empty: it is then taken from Content-Disposition or the URL.
    An already complete file of the same name is reused, not downloaded again.
    """
    if urlsplit(url).scheme != 'https':
        raise DownloadError('Разрешены только HTTPS-ссылки')
    if attempts < 1:
        raise ValueError('attempts must be positive')
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = name or name_from_url(url)
    dest = target_dir / safe_name(name) if name else None
    if dest is not None and dest.is_file() and dest.stat().st_size > 0:
        return dest
    part = target_dir / ('.' + (safe_name(name) + '.' if name else '') +
                         safe_name(file_identity or 'single') + '.part')
    transient = (URLError, TimeoutError, ConnectionError, OSError, IncompleteRead, socket.timeout)

    for attempt in range(attempts):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled('Загрузка отменена')
        offset = part.stat().st_size if part.exists() else 0
        headers = {'User-Agent': USER_AGENT}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with opener(Request(url, headers=headers), timeout=60) as response:
                actual = response.geturl() if hasattr(response, 'geturl') else url
                if urlsplit(actual).scheme != 'https':
                    raise DownloadError('Сервер перенаправил загрузку на небезопасный HTTP-адрес')
                status = getattr(response, 'status', 200)
                if offset and status == 206:
                    content_range = response.headers.get('Content-Range', '')
                    m = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)', content_range)
                    if not m or int(m[1]) != offset:
                        raise DownloadError('Сервер вернул неправильный диапазон файла')
                    mode = 'ab'
                    overall = int(m[3]) if m[3] != '*' else 0
                    if overall and (int(m[2]) >= overall or int(m[2]) < offset):
                        raise DownloadError('Сервер вернул неправильный размер диапазона')
                elif status == 200:
                    # Server ignored Range. Truncate the partial file and restart.
                    offset, mode = 0, 'wb'
                    overall = int(response.headers.get('Content-Length') or 0)
                elif status == 206 and offset == 0:
                    content_range = response.headers.get('Content-Range', '')
                    m = re.fullmatch(r'bytes 0-(\d+)/(\d+|\*)', content_range)
                    if not m:
                        raise DownloadError('Неправильный ответ диапазона')
                    mode = 'wb'
                    overall = int(m[2]) if m[2] != '*' else 0
                else:
                    raise DownloadError(f'Неожиданный ответ сервера: HTTP {status}')

                content_type = response.headers.get('Content-Type', '').lower()
                if 'text/html' in content_type or 'application/json' in content_type:
                    raise DownloadError('Вместо архива сервер вернул страницу или JSON-ответ')
                if dest is None:
                    found = (name_from_disposition(response.headers.get('Content-Disposition', ''))
                             or name_from_url(actual))
                    dest = target_dir / safe_name(found or (file_identity or 'mod-download') + '.bin')

                done = offset
                if on_progress:
                    on_progress(done, overall)
                sniff = bytearray()
                with part.open(mode) as out:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled('Загрузка отменена')
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        if len(sniff) < 128 and offset == 0:
                            sniff.extend(chunk[:128-len(sniff)])
                            prefix = bytes(sniff).lstrip().lower()
                            if prefix.startswith(b'<html') or prefix.startswith(b'<!doctype html'):
                                raise DownloadError('Вместо архива возвращена HTML-страница')
                        out.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, overall)
                if overall and done != overall:
                    raise DownloadError(f'Неполная загрузка: {done} из {overall} байт')
                if done == 0:
                    raise DownloadError('Сервер вернул пустой файл')
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled('Загрузка отменена')
                os.replace(part, dest)
                return dest
        except DownloadCancelled:
            # Preserve partial data for explicit retry; no completed library item.
            raise
        except HTTPError as exc:
            if exc.code == 429:
                if attempt + 1 == attempts:
                    raise RateLimited() from exc
                # The server asks to slow down: a quick retry would only annoy it.
                delay = retry_after_seconds(exc.headers, rate_delay * (2 ** attempt))
                if cancel is not None:
                    cancel.wait(delay)
                else:
                    sleeper(delay)
                continue
            if exc.code in (500, 502, 503, 504) and attempt + 1 < attempts:
                sleeper(retry_delay * (2 ** attempt))
                continue
            if exc.code in (401, 403, 410):
                raise LinkRejected(exc.code) from exc
            if exc.code == 416 and offset:
                # Stale partial file larger than the object: restart from zero.
                part.unlink(missing_ok=True)
                if attempt + 1 < attempts:
                    continue
            raise DownloadError(f'Ошибка сервера загрузки: HTTP {exc.code}') from exc
        except DownloadError as exc:
            # Only interrupted streams should be retried; malformed replies
            # must discard stale partial bytes (especially HTML and wrong ranges).
            if str(exc).startswith('Неполная загрузка') and attempt + 1 < attempts:
                sleeper(retry_delay * (2 ** attempt))
                continue
            if not str(exc).startswith('Неполная загрузка'):
                part.unlink(missing_ok=True)
            raise
        except transient as exc:
            if attempt + 1 < attempts:
                sleeper(retry_delay * (2 ** attempt))
                continue
            raise DownloadError(f'Сетевое соединение прервалось ({attempts} попытки): {exc}') from exc
    raise DownloadError('Не удалось скачать файл')


def extract_zip_to_staging(archive, staging_root, game, mod_id, max_bytes=8*1024**3):
    """Only ZIP; refuse absolute paths, traversal, links, and zip bombs."""
    archive = Path(archive)
    output = Path(staging_root) / safe_name(game) / str(int(mod_id)) / safe_name(archive.stem)
    if output.exists():
        raise DownloadError('Папка назначения уже существует')
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        total = sum(x.file_size for x in infos)
        if total > max_bytes or len(infos) > 30000:
            raise DownloadError('Архив слишком большой для безопасной распаковки')
        for info in infos:
            name = info.filename.replace('\\', '/')
            path = Path(name)
            if (name.startswith('/') or re.match(r'^[A-Za-z]:', name) or
                    '..' in path.parts or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise DownloadError('Опасный путь или символическая ссылка в архиве')
        output.mkdir(parents=True)
        try:
            for info in infos:
                if info.is_dir(): continue
                destination = output.joinpath(*Path(info.filename.replace('\\', '/')).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, destination.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            return output
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise
