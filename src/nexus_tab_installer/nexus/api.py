"""Nexus Mods v1 API adapter. No downloads without authorized API link."""
import json
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'https://api.nexusmods.com/v1'
APP_VERSION = '1.0.0'


class NexusError(Exception):
    pass


class NexusRateLimited(NexusError):
    """HTTP 429: the API budget (20 000 a day, then 500 an hour) is used up."""



def nxm_parts(uri):
    from urllib.parse import urlsplit, parse_qs
    u = urlsplit(uri.strip())
    m = re.fullmatch(r'/mods/(\d+)/files/(\d+)', u.path)
    if u.scheme != 'nxm' or not re.fullmatch(r'[A-Za-z0-9_-]{2,80}', u.netloc) or not m:
        raise NexusError('Ожидается ссылка nxm://<game>/mods/<id>/files/<id>?key=...&expires=...')
    q = parse_qs(u.query)
    key = q.get('key', [None])[0]
    expires = q.get('expires', [None])[0]
    user_id = q.get('user_id', [None])[0]
    if not key or not expires or not user_id:
        raise NexusError('Это не полная NXM-ссылка. Нужны key, expires и user_id, полученные через Mod Manager Download → Slow Download на сайте Nexus Mods.')
    if not expires.isdigit() or not user_id.isdigit():
        raise NexusError('Неверные параметры expires или user_id в NXM-ссылке')
    return u.netloc.lower(), int(m[1]), int(m[2]), key, expires, user_id


class NexusClient:
    min_interval = 1.0  # seconds between two API requests of this program
    _turn_lock = threading.Lock()
    _next_turn = 0.0

    def __init__(self, apikey):
        if not apikey.strip():
            raise NexusError('Введите API key Nexus Mods')
        self.apikey = apikey.strip()

    def _wait_for_turn(self):
        with NexusClient._turn_lock:
            now = time.monotonic()
            turn = max(now, NexusClient._next_turn)
            NexusClient._next_turn = turn + self.min_interval
        if turn > now:
            time.sleep(turn - now)

    def get(self, path, params=None):
        url = BASE + path
        if params:
            url += '?' + urlencode(params)
        req = Request(url, headers={'apikey': self.apikey, 'User-Agent': f'NexusTabInstaller/{APP_VERSION}', 'Application-Name': 'NexusTabInstaller', 'Application-Version': APP_VERSION, 'Accept': 'application/json'})
        self._wait_for_turn()
        try:
            with urlopen(req, timeout=30) as r:
                return json.load(r)
        except HTTPError as e:
            detail = e.read(2048).decode('utf-8', errors='replace')
            try:
                message = json.loads(detail).get('message') or detail
            except ValueError:
                message = detail
            if e.code == 429:
                raise NexusRateLimited('Исчерпан лимит запросов Nexus API. Он обновляется каждый час и в 00:00 GMT.') from e
            if e.code == 403 and '/download_link' in path:
                raise NexusError('Nexus отклонил загрузку (403). Проверьте, что NXM-ссылка получена через Slow Download, ещё действительна и принадлежит вашему аккаунту. Ответ Nexus: ' + str(message)[:350]) from e
            raise NexusError(f'Ошибка Nexus API (HTTP {e.code}): {str(message)[:350]}') from e
        except Exception as e:
            raise NexusError(f'Ошибка Nexus API: {e}') from e

    def validate(self):
        return self.get('/users/validate.json')

    def mod(self, game, mod_id):
        return self.get(f'/games/{game}/mods/{int(mod_id)}.json')

    def files(self, game, mod_id):
        data = self.get(f'/games/{game}/mods/{int(mod_id)}/files.json')
        return data.get('files', [])

    def download_links(self, game, mod_id, file_id, key=None, expires=None, user_id=None):
        params = {'key': key, 'expires': expires, 'user_id': user_id} if key and expires and user_id else None
        links = self.get(f'/games/{game}/mods/{int(mod_id)}/files/{int(file_id)}/download_link.json', params)
        if not isinstance(links, list) or not links:
            raise NexusError('API не вернул ссылку загрузки')
        # Do not accept arbitrary URLs or leak API key to remote CDN.
        uri = links[0].get('URI')
        if not uri or not uri.startswith('https://'):
            raise NexusError('Неожиданный адрес загрузки')
        return uri
