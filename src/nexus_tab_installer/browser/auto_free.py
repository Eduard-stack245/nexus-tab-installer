"""Browser-assisted Nexus downloads, coordinated with the paired extension.

The desktop app never visits Nexus pages itself. The extension opens each
selected file page in its own browser window, presses the site's own
"Slow download" (Free) or "Start download" (Premium) button, lets the site's
countdown finish and intercepts the download the site starts. The extension
then either hands the signed CDN link to the app (transfer="app") or lets the
browser save the file and reports its path (transfer="browser").

Jobs run one at a time, at the pace set by browser/pacing.py. A failure of one
file never blocks the whole queue: retryable problems are retried, everything else
marks only that file failed. The queue stops only when every next file would hit
the same wall: no login, a block by the site, or a run of site failures. Login and
verification pages are shown to the user; CAPTCHAs are never solved or bypassed.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs

from nexus_tab_installer.browser.pacing import MAX_FAILURES
from nexus_tab_installer.nexus.api import nxm_parts, NexusError

MAX_ATTEMPTS = 3
LEASE_SECONDS = 120          # extended by every heartbeat from the browser
USER_WAIT_SECONDS = 900      # login / verification page waiting for the user
PAUSED_POLL_SECONDS = 120    # reported as the wait while the queue is on hold
REQUIRED_EXTENSION = '1.0.0'

# Failures worth another attempt in a fresh tab.
RETRYABLE = frozenset({'timeout', 'no_button', 'site_error', 'tab_closed',
                       'lease_expired', 'page_error', 'transfer_failed', 'navigation'})
# Failures on the site's side: a run of them means something is wrong with Nexus or
# with this program, and hammering the site further would only make it worse.
SITE_FAILURES = frozenset({'timeout', 'no_button', 'site_error', 'page_error'})
# Every next file would fail the same way: hold the queue until the user acts.
QUEUE_STOPS = frozenset({'login_required', 'verification', 'blocked', 'no_site_access'})
PAUSE_TEXT = {
    'login_required': 'Загрузка ждёт входа в аккаунт Nexus. Войдите в окне браузера, '
                      'затем нажмите «Продолжить загрузку».',
    'verification': 'Nexus просит пройти проверку (CAPTCHA). Программа её не проходит и не обходит: пройдите её сами '
                    'в окне браузера, затем нажмите «Продолжить загрузку».',
    'blocked': 'Nexus ограничил доступ к сайту, поэтому загрузка остановлена. Откройте nexusmods.com '
               'в браузере и проверьте аккаунт. Нажимайте «Продолжить загрузку», только когда сайт снова работает.',
    'no_site_access': 'Расширению не разрешён доступ к nexusmods.com. Нажмите значок Nexus Tab Collector в браузере, '
                      'затем «Разрешить доступ к Nexus» и «Продолжить загрузку».',
    'site_failures': f'Nexus {MAX_FAILURES} раз подряд не отдал файл, поэтому загрузка приостановлена, '
                     'чтобы не нагружать сайт. Проверьте сайт в браузере, затем нажмите «Продолжить загрузку».',
    'ask_where_to_save': 'Firefox спрашивает, куда сохранять каждый файл, и на каждый файл открывал бы новое окно. '
                         'Этот файл программа скачивает сама. В настройках Firefox (Основные → Загрузки) выключите '
                         '«Всегда выдавать запрос на сохранение файлов», закройте окно сохранения кнопкой «Отмена» '
                         'и нажмите «Продолжить загрузку».',
}
REASON_TEXT = {
    'rate_limited': 'Nexus просит подождать: слишком много запросов',
    'blocked': 'Nexus ограничил доступ к сайту',
    'no_site_access': 'Расширению не разрешён доступ к nexusmods.com',
    'timeout': 'Сайт не начал скачивание вовремя',
    'no_button': 'На странице не найдена кнопка скачивания',
    'site_error': 'Nexus сообщил об ошибке получения ссылки',
    'tab_closed': 'Окно скачивания было закрыто',
    'lease_expired': 'Браузер перестал отвечать',
    'page_error': 'Ошибка сценария на странице',
    'transfer_failed': 'Браузер не смог сохранить файл',
    'navigation': 'Страница ушла с выбранного мода',
    'login_required': 'Нужно войти в аккаунт Nexus в браузере',
    'verification': 'Nexus просит пройти проверку (CAPTCHA) в окне браузера',
    'file_unavailable': 'Файл недоступен на Nexus (удалён или скрыт)',
    'mismatch': 'Браузер вернул другой файл',
    'premium_only': 'Файл доступен только Premium',
    'cancelled': 'Отменено',
}
STEP_TEXT = {
    'opening': 'Открываем страницу файла в браузере',
    'page': 'Страница файла открыта',
    'resolve': 'Определяем основной файл мода',
    'resolved': 'Файл определён, открываем его страницу',
    'file': 'Ищем кнопку скачивания',
    'target': 'Ищем кнопку скачивания',
    'manager': 'Нажата кнопка скачивания',
    'clicked': 'Нажата кнопка скачивания',
    'slow': 'Нажата кнопка Slow download',
    'modal': 'Подтверждаем обычную загрузку',
    'countdown': 'Ожидаем отсчёт сайта перед скачиванием',
    'link': 'Ожидаем ссылку на файл',
    'login': 'Войдите в Nexus в открытом окне браузера — загрузка продолжится сама',
    'verify': 'Nexus просит пройти проверку (CAPTCHA). Программа её не проходит: пройдите её сами в окне браузера, '
              'загрузка продолжится сама',
    'download': 'Браузер скачивает файл',
    'heartbeat': '',
}


def file_page_url(game, mod_id, file_id=0):
    base = f'https://www.nexusmods.com/{game}/mods/{int(mod_id)}?tab=files'
    return base + (f'&file_id={int(file_id)}' if int(file_id or 0) > 0 else '')


def identify_file_url(url: str):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ('www.nexusmods.com', 'nexusmods.com'):
        raise ValueError('Only https Nexus URLs are supported')
    bits = p.path.lower().strip('/').split('/')
    if len(bits) == 4 and bits[0] == 'games':
        bits = bits[1:]
    if len(bits) != 3 or bits[1] != 'mods' or not bits[2].isdigit():
        raise ValueError('A mod page URL is required')
    q = parse_qs(p.query)
    fid = q.get('file_id', [''])[0]
    if not fid.isdigit() or int(fid) <= 0:
        raise ValueError('file_id is required')
    return bits[0], int(bits[2]), int(fid)


def safe_download_url(url) -> bool:
    """A signed CDN link from the browser: public https host, no credentials."""
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or not host or p.username or p.password:
        return False
    if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return '.' in host
    return address.is_global


def _spec(raw):
    """Accept a dict spec or a legacy file-page URL."""
    if isinstance(raw, str):
        game, mod_id, file_id = identify_file_url(raw)
        raw = {'game': game, 'mod_id': mod_id, 'file_id': file_id}
    game = str(raw['game']).lower()
    if not game or len(game) > 80 or not all(c.isalnum() or c in '_-' for c in game):
        raise ValueError('Invalid game domain')
    mod_id, file_id = int(raw['mod_id']), int(raw.get('file_id') or 0)
    if mod_id <= 0 or file_id < 0:
        raise ValueError('Invalid Nexus identity')
    transfer = raw.get('transfer', 'app')
    return {
        'game': game, 'mod_id': mod_id, 'file_id': file_id,
        'filename': str(raw.get('filename') or '')[:200],
        'size_kb': max(0, int(raw.get('size_kb') or 0)),
        'transfer': transfer if transfer in ('app', 'browser') else 'app',
        'task': str(raw.get('task') or f'{game}:{mod_id}:{file_id}')[:120],
    }


class AutoFreeBatch:
    def __init__(self, clock=time.monotonic, trace=None, max_attempts=MAX_ATTEMPTS,
                 lease_seconds=LEASE_SECONDS):
        self.trace = trace
        self._lock = threading.RLock()
        self._clock = clock
        self._jobs = []
        self._owner = None
        self._lease_until = 0.0
        self._enabled = False
        self._paused = False
        self._pause_reason = ''
        self._started = 0.0
        self._last_activity = 0.0
        self._max_attempts = max(1, int(max_attempts))
        self._lease_seconds = lease_seconds
        self._helpers = {}  # authenticated extension polls, keyed by browser profile
        self._helper_versions = {}
        # Optional callable: False holds the next job back. Signed CDN links are
        # short-lived, so a new one is requested only when it can be used at once.
        self.gate = None
        self.pacer = None  # browser/pacing.py: decides when the next page may open
        self.premium_seen = False  # a page showed a Premium account (possible without an API key)

    # ----- helpers -------------------------------------------------------
    def _emit(self, event, **data):
        if self.trace:
            try:
                self.trace.emit(event, **data)
            except Exception:
                pass

    def _snapshot(self, job):
        return {k: job[k] for k in ('id', 'game', 'mod_id', 'file_id', 'filename', 'size_kb',
                                    'state', 'error', 'reason', 'step', 'attempts',
                                    'transfer', 'task', 'bytes', 'total')}

    def _find(self, job_id, state=None):
        return next((j for j in self._jobs if j['id'] == job_id and
                     (state is None or j['state'] == state)), None)

    def _new_job(self, spec):
        return {**spec, 'id': secrets.token_hex(12),
                'url': file_page_url(spec['game'], spec['mod_id'], spec['file_id']),
                'state': 'pending', 'error': '', 'reason': '', 'step': 'В очереди',
                'attempts': 0, 'bytes': 0, 'total': 0, 'verify_seen': False}

    def _release(self):
        self._owner = None
        self._lease_until = 0.0
        self._started = self._clock()

    def _pause(self, reason):
        self._paused = True
        self._pause_reason = reason
        self._emit('batch.paused', reason=reason)

    def _fail_or_retry(self, job, reason, note=''):
        """Return 'retry', 'failed', 'paused' or 'cooldown'. Caller holds the lock."""
        reason = reason if reason in REASON_TEXT else 'page_error'
        job['reason'] = reason
        job['error'] = (REASON_TEXT[reason] + (f': {note}' if note else ''))[:200]
        if reason in QUEUE_STOPS or reason == 'rate_limited':
            # Not this file's fault: it keeps its turn and its attempts.
            job['state'] = 'pending'
            job['step'] = REASON_TEXT[reason]
            job['attempts'] = max(0, job['attempts'] - 1)
            if reason == 'rate_limited':
                if self.pacer is not None:
                    self.pacer.cool_down()
                self._emit('batch.cooldown', reason=reason)
                return 'cooldown'
            self._pause(reason)
            return 'paused'
        tripped = (self.pacer is not None and reason in SITE_FAILURES
                   and self.pacer.record_failure() >= MAX_FAILURES)
        if tripped:
            self._pause('site_failures')
        if reason in RETRYABLE and job['attempts'] < self._max_attempts:
            job['state'] = 'pending'
            job['step'] = f"Повторная попытка ({job['attempts'] + 1} из {self._max_attempts})"
            return 'paused' if tripped else 'retry'
        job['state'] = 'failed'
        job['step'] = 'Ошибка'
        return 'failed'

    def _expire_lease(self, now):
        """Recover a job whose browser stopped answering. Caller holds the lock."""
        if self._owner is None or now < self._lease_until:
            return None
        job = next((j for j in self._jobs if j['state'] == 'leased'), None)
        self._release()
        if job is None:
            return None
        outcome = self._fail_or_retry(job, 'lease_expired')
        self._emit('job.lease_expired', job_id=job['id'], mod_id=job['mod_id'], file_id=job['file_id'])
        return {'kind': outcome, 'job': self._snapshot(job)}

    # ----- helper liveness -----------------------------------------------
    def live_helpers(self, max_age=90):
        """Sources that have recently asked for a job or reported on the one they hold.

        Merely synchronizing a tab list is not evidence that the click helper
        is running: these calls must be reached with the authenticated token.
        """
        with self._lock:
            now = self._clock()
            return {source for source, when in self._helpers.items()
                    if now - when <= max_age}

    def has_live_helper(self, max_age=90):
        return bool(self.live_helpers(max_age))

    def outdated_helper(self, max_age=90):
        """The version of an extension older than this program expects, or '' when all are fine."""
        def parts(version):
            return tuple(int(n) for n in version.split('.')) if version else (0,)
        with self._lock:
            now = self._clock()
            for source, when in self._helpers.items():
                version = self._helper_versions.get(source, '')
                if now - when <= max_age and parts(version) < parts(REQUIRED_EXTENSION):
                    return version or 'более старая'
            return ''

    # ----- batch control -------------------------------------------------
    def start(self, specs):
        """Replace the batch with new jobs (duplicates by file are dropped)."""
        jobs, seen = [], set()
        for raw in specs:
            spec = _spec(raw)
            identity = (spec['game'], spec['mod_id'], spec['file_id'] or spec['task'])
            if identity in seen:
                continue
            seen.add(identity)
            jobs.append(self._new_job(spec))
        with self._lock:
            self._jobs = jobs
            self._owner = None
            self._lease_until = 0
            self._enabled = bool(jobs)
            self._started = self._clock()
            self._paused = False
            self._pause_reason = ''
        self._emit('batch.started', count=len(jobs))
        return len(jobs)

    def add(self, specs):
        """Append jobs to the running batch; files already waiting are skipped."""
        added = 0
        with self._lock:
            active = {(j['game'], j['mod_id'], j['file_id'] or j['task']) for j in self._jobs
                      if j['state'] in ('pending', 'leased')}
            for raw in specs:
                spec = _spec(raw)
                identity = (spec['game'], spec['mod_id'], spec['file_id'] or spec['task'])
                if identity in active:
                    continue
                active.add(identity)
                self._jobs.append(self._new_job(spec))
                added += 1
            if added:
                if not self._enabled:
                    self._started = self._clock()
                self._enabled = True
        if added:
            self._emit('batch.started', count=added)
        return added

    def stop(self):
        with self._lock:
            self._enabled = False
            self._owner = None
            self._lease_until = 0
            for job in self._jobs:
                if job['state'] in ('pending', 'leased'):
                    job['state'] = 'cancelled'
                    job['reason'] = 'cancelled'
            self._emit('batch.stopped', count=len(self._jobs))

    def resume(self):
        """Continue after the user signed in; nothing is lost while paused."""
        with self._lock:
            if not self._paused:
                return False
            self._paused = False
            self._pause_reason = ''
            self._started = self._clock()
            if self.pacer is not None:
                self.pacer.reset_failures()
            return True

    def requeue(self, game, mod_id, file_id, *, transfer='browser', task=None):
        """The app could not use the handed-over link: let the browser save it."""
        with self._lock:
            job = next((j for j in reversed(self._jobs)
                        if (j['game'], j['mod_id']) == (game, int(mod_id))
                        and (j['file_id'] == int(file_id) or (task and j['task'] == task))
                        and j['state'] == 'confirmed'), None)
            if job is None or job['transfer'] == transfer or job['attempts'] >= self._max_attempts + 1:
                return False
            job.update(state='pending', transfer=transfer, file_id=int(file_id) or job['file_id'],
                       step='Повтор: скачивание средствами браузера', error='', reason='')
            job['url'] = file_page_url(job['game'], job['mod_id'], job['file_id'])
            self._enabled = True
            self._started = self._clock()
            return True

    # ----- extension protocol --------------------------------------------
    def claim(self, source_id, version=''):
        """Next job for a browser profile, or None. Expired leases are recovered."""
        if not source_id or len(source_id) > 128:
            return None
        with self._lock:
            now = self._clock()
            self._helpers[source_id] = now
            self._helper_versions[source_id] = version if re.fullmatch(r'\d{1,4}(\.\d{1,6}){0,3}', version or '') else ''
            if not self._enabled or self._paused:
                return None
            self._expire_lease(now)
            leased = next((j for j in self._jobs if j['state'] == 'leased'), None)
            if leased is not None:
                if self._owner != source_id:
                    return None
                # The owner asks for work while holding a lease: its job was lost
                # (service worker restarted). Deliver the same job again.
                self._lease_until = now + self._lease_seconds
                return {k: leased[k] for k in ('id', 'url', 'game', 'mod_id', 'file_id', 'filename', 'transfer')}
            if self.gate is not None and not self.gate():
                return None
            pending = next((j for j in self._jobs if j['state'] == 'pending'), None)
            if pending is None:
                return None
            if self.pacer is not None:
                if self.pacer.wait()[0] > 0:
                    return None
                self.pacer.record_start()
            pending['state'] = 'leased'
            pending['attempts'] += 1
            pending['step'] = STEP_TEXT['opening']
            pending['bytes'] = pending['total'] = 0
            self._owner = source_id
            self._lease_until = now + self._lease_seconds
            self._started = now
            self._last_activity = now
            self._emit('job.leased', job_id=pending['id'], source_id=source_id,
                       mod_id=pending['mod_id'], file_id=pending['file_id'])
            return {k: pending[k] for k in ('id', 'url', 'game', 'mod_id', 'file_id', 'filename', 'transfer')}

    def progress(self, source_id, job_id, step, info=None):
        """Heartbeat / stage of the leased job. No URLs or page text are accepted."""
        info = info if isinstance(info, dict) else {}
        with self._lock:
            if self._owner != source_id or not self._enabled:
                return False
            job = self._find(job_id, 'leased')
            if job is None or step not in STEP_TEXT:
                return False
            now = self._clock()
            self._helpers[source_id] = now  # busy with this job, so it does not ask for the next one
            waiting_user = step in ('login', 'verify')
            self._lease_until = now + (USER_WAIT_SECONDS if waiting_user else self._lease_seconds)
            self._last_activity = now
            if STEP_TEXT[step]:
                job['step'] = STEP_TEXT[step]
            if info.get('premium') is True:
                self.premium_seen = True
            if step == 'verify' and not job['verify_seen']:
                # The site wants proof of a person: take that as a request to go slower.
                job['verify_seen'] = True
                if self.pacer is not None:
                    self.pacer.record_warning()
            if step == 'resolved':
                try:
                    fid = int(info.get('file_id') or 0)
                except (TypeError, ValueError):
                    fid = 0
                if fid > 0 and (job['file_id'] in (0, fid)):
                    job['file_id'] = fid
                    name = info.get('filename')
                    if isinstance(name, str) and name and not job['filename']:
                        job['filename'] = name[:200]
                    try:
                        job['size_kb'] = job['size_kb'] or max(0, int(info.get('size_kb') or 0))
                    except (TypeError, ValueError):
                        pass
            if step == 'download':
                for key in ('bytes', 'total'):
                    value = info.get(key)
                    if isinstance(value, int) and 0 <= value < 2**45:
                        job[key] = value
            if step != 'heartbeat':
                self._emit('job.progress', job_id=job_id, source_id=source_id, step=step)
            return True

    def complete(self, source_id, job_id, status, nxm='', note='', submit=None, *,
                 url='', path='', reason='', file_id=0, filename='', warning=''):
        """Result of a leased job.

        Returns False for stale/cross-profile results, otherwise an outcome dict
        ``{'kind': 'url'|'file'|'nxm'|'retry'|'failed'|'paused'|'cooldown', 'job': ...}``
        that the caller forwards to the UI. NXM tokens never enter the outcome.
        """
        with self._lock:
            if not self._enabled or self._owner != source_id:
                return False
            job = self._find(job_id, 'leased')
            if job is None:
                return False
            try:
                fid = int(file_id or 0)
            except (TypeError, ValueError):
                fid = 0
            if fid > 0 and job['file_id'] in (0, fid):
                job['file_id'] = fid
            if isinstance(filename, str) and filename and not job['filename']:
                job['filename'] = filename.replace('\\', '/').rsplit('/', 1)[-1][:200]
            outcome = {'kind': 'failed'}
            if status == 'url':
                if safe_download_url(url):
                    job['state'] = 'confirmed'
                    job['step'] = 'Ссылка получена; файл скачивает программа'
                    outcome = {'kind': 'url', 'url': url}
                    # This file is fine, but the next ones would each open a browser dialog.
                    if warning == 'ask_where_to_save':
                        self._pause(warning)
                else:
                    outcome['kind'] = self._fail_or_retry(job, 'site_error', 'недопустимая ссылка')
            elif status == 'file':
                if isinstance(path, str) and 3 <= len(path) <= 1024:
                    job['state'] = 'confirmed'
                    job['step'] = 'Браузер сохранил файл; переносим в библиотеку'
                    outcome = {'kind': 'file', 'path': path}
                else:
                    outcome['kind'] = self._fail_or_retry(job, 'transfer_failed')
            elif status == 'nxm':
                try:
                    game, mod, file, _key, expires, _uid = nxm_parts(nxm)
                    if time.time() >= int(expires):
                        raise ValueError('NXM token already expired')
                    if (game, mod) != (job['game'], job['mod_id']) or job['file_id'] not in (0, file):
                        raise ValueError('NXM link does not match selected file')
                    if submit is None:
                        raise ValueError('NXM inbox not configured')
                    submit(nxm)
                except (ValueError, OSError, NexusError) as exc:
                    outcome['kind'] = self._fail_or_retry(job, 'mismatch', str(exc)[:80])
                else:
                    job['file_id'] = file
                    job['state'] = 'confirmed'
                    job['step'] = 'Ссылка получена; загрузка передана приложению'
                    outcome = {'kind': 'nxm'}
            else:
                # 'failed' with a reason code, or the legacy 'attention' result.
                code = reason if reason in REASON_TEXT else 'page_error'
                outcome['kind'] = self._fail_or_retry(job, code, str(note or '')[:120]
                                                       if code == 'page_error' else '')
            if job['state'] == 'confirmed' and self.pacer is not None:
                self.pacer.record_success()
            self._release()
            self._emit('job.completed', job_id=job_id, source_id=source_id,
                       result='confirmed' if job['state'] == 'confirmed' else
                       ('pending' if job['state'] == 'pending' else 'attention'),
                       reason=job['reason'] or outcome['kind'])
            outcome['job'] = self._snapshot(job)
            return outcome

    def acknowledge_external(self, game, mod_id, file_id):
        """OS NXM handler or the Downloads watcher delivered the leased file."""
        with self._lock:
            if not self._enabled:
                return False
            for job in self._jobs:
                if (job['state'] in ('leased', 'pending') and
                        (job['game'], job['mod_id']) == (game, int(mod_id)) and
                        job['file_id'] in (0, int(file_id))):
                    was_leased = job['state'] == 'leased'
                    job['state'] = 'confirmed'
                    job['file_id'] = int(file_id)
                    job['step'] = 'Файл получен; загрузка передана приложению'
                    if was_leased:
                        self._release()
                    self._emit('job.external_ack', job_id=job['id'], mod_id=mod_id, file_id=file_id)
                    return True
            return False

    def tick(self):
        """Periodic housekeeping from the UI loop: recover dead browser leases."""
        with self._lock:
            if not self._enabled:
                return None
            return self._expire_lease(self._clock())

    # ----- state ---------------------------------------------------------
    def job_state(self, job_id):
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return 'unknown'
            if job['state'] == 'pending':
                # Retried in a new tab: the old tab must stop.
                return 'retry'
            return job['state']

    def stalled_page(self, threshold=30):
        """True if a claimed browser job produced no heartbeat for a while."""
        with self._lock:
            return (self._enabled and self._owner is not None
                    and any(j['state'] == 'leased' for j in self._jobs)
                    and self._clock() - self._last_activity >= threshold)

    def needs_browser(self, grace=45):
        """Pending jobs, but no browser profile has asked for work recently."""
        with self._lock:
            now = self._clock()
            return (self._enabled and not self._paused and self._owner is None
                    and any(j['state'] == 'pending' for j in self._jobs)
                    and now - self._started > grace
                    and not any(now - when <= grace for when in self._helpers.values()))

    def wait_status(self):
        """(seconds, reason) until the next file may start; (0, '') when nothing holds it."""
        with self._lock:
            if self._enabled and not self._paused and self.pacer is not None:
                return self.pacer.wait()
            return 0, ''

    def next_wait(self):
        """Seconds the extension may idle before the next job can possibly appear."""
        with self._lock:
            if self._paused:
                return PAUSED_POLL_SECONDS
            return int(self.wait_status()[0])

    def remaining(self):
        """Files still waiting for their turn."""
        with self._lock:
            return sum(1 for j in self._jobs if j['state'] == 'pending')

    def has_leased(self):
        with self._lock:
            return any(j['state'] == 'leased' for j in self._jobs)

    def attention(self):
        """What the person has to do right now in the browser, or ''."""
        with self._lock:
            if self._paused:
                return PAUSE_TEXT.get(self._pause_reason, 'Загрузка приостановлена.')
            waiting = (STEP_TEXT['login'], STEP_TEXT['verify'])
            return next((j['step'] for j in self._jobs if j['state'] == 'leased' and j['step'] in waiting), '')

    def is_active(self):
        with self._lock:
            return self._enabled and any(j['state'] in ('pending', 'leased') for j in self._jobs)

    def is_idle(self):
        return not self.is_active()

    def pending_for(self, game, mod_id):
        with self._lock:
            return [self._snapshot(j) for j in self._jobs
                    if (j['game'], j['mod_id']) == (game, int(mod_id))
                    and j['state'] in ('pending', 'leased')]

    def expects(self, game, mod_id, file_id):
        """A file (or an unresolved main file) of this mod is waiting in the batch."""
        with self._lock:
            return any((j['game'], j['mod_id']) == (game, int(mod_id))
                       and j['file_id'] in (0, int(file_id))
                       and j['state'] in ('pending', 'leased') for j in self._jobs)

    def state(self):
        with self._lock:
            return {'enabled': self._enabled, 'paused': self._paused,
                    'pause_reason': self._pause_reason,
                    'jobs': [{k: j[k] for k in ('game', 'mod_id', 'file_id', 'state', 'error', 'step',
                                                'reason', 'attempts', 'filename', 'bytes', 'total', 'task')}
                             for j in self._jobs]}
