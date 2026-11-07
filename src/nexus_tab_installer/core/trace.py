"""Structured, bounded, credential-safe diagnostics for the local download process.

Only allowlisted short event codes and scalar metadata are written. Never accept
full URLs, page titles, cookies, authentication tokens, NXM links or filenames.
"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
# Strict codes: a compromised page/extension cannot inject arbitrary strings
# (including secrets) into logs. All text metadata is likewise enumerated.
EVENTS = set('''app.started app.export_requested server.started server.start_failed
server.request server.auto_result server.job_claimed server.response batch.started batch.stopped
batch.paused batch.cooldown ui.rate_limited ui.mods_restored
job.leased job.lease_expired job.progress job.completed job.external_ack
ui.scan_clicked ui.scan_results ui.download_clicked ui.download_preparing
ui.free_queue_created ui.heartbeat ui.browser_sync_received ui.auto_update
ui.download_job_updated ui.extension_scan_timeout ui.extension_scanned
ui.windows_scanned ui.file_catalog_failed ui.file_catalog_received
ui.nxm_import_failed ui.nxm_import_accepted ui.download_job_terminal
ui.archive_delivery_result ui.nexus_account_verified
ui.transfer_started ui.transfer_link_acquired ui.transfer_finished
ui.browser_archive_detected server.snapshot_received
ui.auto_handoff ui.auto_failed ui.auto_retry ui.auto_paused ui.direct_link_rejected
ui.archive_reused ui.browser_file_imported library.migrated
ui.log_opened ui.log_exported ui.log_refresh ui.action ui.operation_error
app.exception app.thread_exception'''.split())
EXT_EVENTS = set('''snapshot.started job.received window.create_requested window.created
window.tab_ready content.command_delivered content.command_failed
content.result_received server.result_rejected server.result_accepted
content.ready_received content.ready_early job.poll_failed
page.job_started page.stage page.challenge_detected page.login_required
page.adblock_offer_blocked page.adblock_offer_closed page.nxm_link_found
page.slow_button_found page.slow_button_clicked page.manager_button_found
page.manager_button_clicked page.no_actionable_button page.deadline_exceeded
page.command_received page.assignment_requested page.assignment_received
snapshot.finished snapshot.failed pairing.started pairing.approved helper.poll_ok helper.poll_failed
trace.delivered trace.buffered trace.delivery_failed
page.assignment_not_for_tab page.assignment_timeout page.attention page.control_scan page.cancel_received
page.login_required page.button_clicked page.modal_confirmed page.countdown page.no_button
page.site_error page.site_refused page.resolved_file page.navigate page.focus_requested page.resume_cancelled page.manual_link
page.premium_start tab.created window.reused window.closed job.timeout job.failed job.cancelled
job.retry_tab download.intercepted download.handoff download.browser_started
download.browser_complete download.browser_failed download.ignored'''.split())
EVENTS.update('extension.' + e for e in EXT_EVENTS)
ENUMS = {
 'step':set('page file manager slow link scan select target settings import cancel session download export '
            'opening resolve resolved clicked modal countdown login verify heartbeat'.split()),
 'state':set('pending leased confirmed attention cancelled unknown finished failed interrupted'.split()),
 'result':set('confirmed attention accepted rejected pending cancelled failed retry'.split()),
 'stage':set('active waiting progress attention nxm url file failed'.split()),
 'status':set('ok failed pending timeout'.split()),
 'browser':set('edge chrome opera-gx firefox'.split()),
 'method':{'post'},
 'endpoint':set('pair.request auto.complete snapshot diagnostics.event auto.next auto.status pair.status health'.split()),
 'reason':set('port_unavailable nxm attention content_script_unavailable network_or_server needs_user unknown '
              'timeout no_button site_error tab_closed lease_expired page_error transfer_failed navigation '
              'login_required verification file_unavailable mismatch premium_only cancelled '
              'url file retry failed paused app browser not_https html referrer no_click '
              'rate_limited blocked no_site_access site_failures cooldown ask_where_to_save'.split()),
 'area':set('ui server network queue scanner browser install export startup eventloop thread unknown'.split()),
 'error_type':set('exception valueerror typeerror keyerror attributeerror indexerror oserror permissionerror filenotfounderror timeouterror connectionerror runtimeerror nexuserro r'.replace('nexuserro r','nexuserror').split()),
 'ack':set('yes no pending'.split()),
}

FIELDS = {
    'job_id', 'mod_id', 'file_id', 'step', 'state', 'result', 'stage',
    'status', 'http_status', 'attempt', 'window_id', 'tab_id', 'count',
    'browser', 'method', 'endpoint', 'reason', 'elapsed_ms', 'selected',
    'source_id', 'profile_count', 'items', 'ack', 'age_ms', 'client_ts', 'body_hint', 'iframe_count', 'area', 'error_type'
}
TEXT = {'step', 'state', 'result', 'stage', 'status', 'browser', 'method',
        'endpoint', 'reason', 'ack', 'area', 'error_type'}
NUMERIC = {'mod_id', 'file_id', 'http_status', 'attempt', 'window_id',
           'tab_id', 'count', 'elapsed_ms', 'selected', 'profile_count', 'items', 'age_ms', 'client_ts', 'body_hint', 'iframe_count'}

def safe_event(event, details=None):
    if not isinstance(event, str) or event not in EVENTS:
        raise ValueError('Invalid diagnostic event')
    safe = {}
    for k, value in (details or {}).items():
        if k not in FIELDS or isinstance(value, (dict, list, bool)):
            continue
        if k in NUMERIC:
            if type(value) is int and 0 <= value <= (10_000_000_000_000 if k == 'client_ts' else 1_000_000_000):
                safe[k] = value
        elif k == 'source_id':
            if isinstance(value, str) and len(value) <= 128:
                # Never write the persistent browser installation identifier.
                safe[k] = hashlib.sha256(value.encode()).hexdigest()[:12]
        elif k == 'job_id':
            if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{24}', value):
                safe[k] = value
        elif k in TEXT and isinstance(value, str) and value in ENUMS[k]:
            safe[k] = value
    return {'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'event': event, 'data': safe}

class TraceLog:
    def __init__(self, directory: Path, max_bytes=8_000_000):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'download-trace.jsonl'
        self._lock = threading.RLock()
        self.max_bytes = max_bytes
        self.run_id = uuid.uuid4().hex[:12]

    def emit(self, event, **details):
        record = safe_event(event, details)
        self._append_record(record)
        return record

    def emit_exception(self, exc, area='unknown', event='app.exception'):
        """Report exception type and *code locations*, never exception text/locals.

        Callers can use this even if the exception message contains API tokens,
        filesystem paths, account identifiers or download URLs.
        """
        kind = type(exc).__name__.lower()
        if kind not in ENUMS['error_type']:
            kind = 'exception'
        if area not in ENUMS['area']:
            area = 'unknown'
        record = safe_event(event, {'area': area, 'error_type': kind})
        frame_list = []
        for frame in traceback.extract_tb(exc.__traceback__)[-8:]:
            file_name = Path(frame.filename).name
            # Only source-file names and line numbers, no absolute paths,
            # traceback source lines, local variables, or error messages.
            if ('nexus_tab_installer' in Path(frame.filename).parts and file_name.endswith('.py')
                    and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',file_name)):
                frame_list.append({'file': file_name, 'line': max(0, int(frame.lineno))})
        record['frames'] = frame_list
        self._append_record(record)
        return record

    def _append_record(self, record):
        record['run_id'] = self.run_id
        with self._lock:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                rotated = self.directory / 'download-trace.previous.jsonl'
                self.path.replace(rotated)
            with self.path.open('a', encoding='utf-8') as fp:
                fp.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')

    def read_recent(self, limit=200):
        """Read only valid events; tolerate interrupted/truncated JSONL writes."""
        from collections import deque
        rows = deque(maxlen=max(1, min(int(limit), 1000)))
        with self._lock:
            for name in ('download-trace.previous.jsonl','download-trace.jsonl'):
                path = self.directory / name
                if not path.exists():
                    continue
                with path.open('r', encoding='utf-8', errors='replace') as stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict) and record.get('event') in EVENTS and isinstance(record.get('data'),dict):
                                rows.append(record)
                        except (ValueError, TypeError):
                            continue
        return list(rows)

    def export(self):
        """Shareable diagnostic pack: allowlisted event lines only, no config/data."""
        destination = self.directory / 'diagnostics-for-support.zip'
        self.emit('ui.log_exported')
        with self._lock, ZipFile(destination, 'w', ZIP_DEFLATED) as z:
            for name in ('download-trace.previous.jsonl', 'download-trace.jsonl'):
                path = self.directory / name
                if path.exists():
                    z.write(path, arcname=name)
            z.writestr('SUMMARY.txt', self.summary_text())
            z.writestr('README.txt',
                'Nexus Tab Installer diagnostics. Event codes and numeric IDs only.\n'
                'No API keys, cookies, NXM links, URLs or page titles are included.\n'
                'Send this ZIP to support after reproducing the issue.\n')
        return destination

    def summary_text(self):
        entries = self.read_recent(1000)
        errors = [e for e in entries if classify_event(e['event']) == 'error']
        steps = [e for e in entries if e['event'] != 'ui.heartbeat']
        latest = steps[-1] if steps else None
        last_stage = diagnose_trace(entries)
        return ('Nexus Tab Installer - diagnostics overview (sanitized)\n'
                f'Events in available log: {len(entries)}\n'
                f'Errors / warnings: {len(errors)}\n'
                f'Last event: {latest["event"] if latest else "none"}\n'
                f'Diagnosis: {last_stage}\n'
                'No API keys, cookies, NXM links, page text or full URLs.\n'
                'An attempt to click is not proof that the website started a download.\n')


# User-visible names. Unknown event codes are still visible, never hidden.
EVENT_LABELS = {
    'app.started': 'Программа запущена',
    'server.started': 'Сервер подключения запущен',
    'server.start_failed': 'Сервер подключения не запустился',
    'ui.scan_clicked': 'Начат поиск модов',
    'ui.scan_results': 'Результаты поиска получены',
    'ui.download_clicked': 'Нажата кнопка скачивания',
    'ui.download_preparing': 'Подготавливаем выбранные файлы',
    'ui.free_queue_created': 'Создана очередь скачивания',
    'batch.started': 'Очередь передана браузеру',
    'job.leased': 'Браузер получил задание',
    'job.progress': 'Новый этап скачивания',
    'job.completed': 'Этап браузера завершён',
    'job.lease_expired': 'Браузер не ответил вовремя',
    'ui.transfer_started': 'Началось скачивание архива',
    'ui.transfer_finished': 'Архив скачан',
    'ui.download_job_terminal': 'Задание завершилось',
    'ui.file_catalog_failed': 'Не удалось получить перечень файлов',
    'app.exception': 'Ошибка приложения',
    'app.thread_exception': 'Ошибка фонового потока',
    'ui.operation_error': 'Операция завершилась ошибкой',
    'extension.window.create_requested': 'Запрошено отдельное окно',
    'extension.window.created': 'Окно браузера открыто',
    'extension.content.command_delivered': 'Команда доставлена странице',
    'extension.page.control_scan': 'Проверка доступных кнопок на странице',
    'extension.page.cancel_received': 'Страница получила команду отмены',
    'extension.content.command_failed': 'Команда не дошла до страницы',
    'extension.page.job_started': 'Страница начала выполнять задание',
    'extension.page.manager_button_found': 'Найдена кнопка менеджера',
    'extension.page.manager_button_clicked': 'Выполнено нажатие кнопки менеджера',
    'extension.page.slow_button_found': 'Найдена кнопка Slow download',
    'extension.page.slow_button_clicked': 'Выполнена попытка нажатия Slow download',
    'extension.page.nxm_link_found': 'Найдена NXM-ссылка',
    'extension.page.no_actionable_button': 'Подходящая кнопка не найдена',
    'extension.page.deadline_exceeded': 'Страница не ответила вовремя',
    'extension.trace.buffered': 'Событие временно сохранено расширением',
    'extension.page.button_clicked': 'Нажата кнопка скачивания на сайте',
    'extension.page.modal_confirmed': 'Выбрана обычная загрузка (Standard download)',
    'extension.page.countdown': 'Сайт ведёт отсчёт перед скачиванием',
    'extension.page.login_required': 'Nexus требует вход в аккаунт',
    'extension.page.no_button': 'Кнопка скачивания не найдена',
    'extension.page.site_error': 'Nexus показал ошибку загрузки',
    'extension.page.resolved_file': 'Определён основной файл мода',
    'extension.download.intercepted': 'Браузер начал скачивание файла',
    'extension.download.handoff': 'Ссылка на файл передана программе',
    'extension.download.browser_started': 'Файл скачивается средствами браузера',
    'extension.download.browser_complete': 'Браузер сохранил файл',
    'extension.download.browser_failed': 'Браузер не смог сохранить файл',
    'extension.job.timeout': 'Сайт не начал скачивание вовремя',
    'ui.auto_handoff': 'Файл получен от браузера',
    'ui.auto_failed': 'Файл не удалось получить',
    'ui.auto_retry': 'Повторная попытка файла',
    'ui.auto_paused': 'Загрузка ждёт действия человека',
    'ui.direct_link_rejected': 'Ссылка CDN отклонена, скачиваем через браузер',
    'ui.archive_reused': 'Архив уже был скачан ранее',
    'library.migrated': 'Старые архивы разложены по папкам сессий',
    'ui.browser_file_imported': 'Файл из браузера перенесён в библиотеку',
    'ui.archive_delivery_result': 'Архив обработан',
    'ui.rate_limited': 'Сервер загрузки просит подождать',
    'ui.mods_restored': 'Восстановлен список незавершённой загрузки',
    'ui.scan_results': 'Получен список модов',
    'ui.extension_scanned': 'Получен список вкладок из браузера',
    'ui.windows_scanned': 'Вкладки найдены средствами Windows',
    'ui.extension_scan_timeout': 'Браузер не ответил на поиск вкладок',
    'ui.file_catalog_received': 'Получен список файлов модов',
    'ui.nexus_account_verified': 'Аккаунт Nexus проверен',
    'ui.log_opened': 'Открыт журнал',
    'ui.log_exported': 'Сохранён отчёт ZIP',
    'ui.log_refresh': 'Журнал обновлён',
    'ui.action': 'Действие в программе',
    'ui.heartbeat': 'Программа работает',
    'ui.auto_update': 'Обновлён ход загрузки',
    'ui.browser_sync_received': 'Браузер прислал список вкладок',
    'ui.browser_archive_detected': 'Найден архив в папке браузера',
    'ui.nxm_import_accepted': 'Принята ссылка NXM',
    'ui.nxm_import_failed': 'Ссылка NXM не принята',
    'ui.transfer_link_acquired': 'Получена ссылка на файл',
    'batch.stopped': 'Очередь завершена',
    'batch.paused': 'Загрузка приостановлена',
    'batch.cooldown': 'Nexus просит подождать: пауза',
    'job.external_ack': 'Файл получен другим способом',
    'server.request': 'Запрос от расширения',
    'server.response': 'Ответ расширению',
    'server.auto_result': 'Результат от расширения',
    'server.job_claimed': 'Расширение взяло задание',
    'server.snapshot_received': 'Получен список вкладок',
    'extension.pairing.started': 'Расширение запрашивает подключение',
    'extension.pairing.approved': 'Браузер подключён',
    'extension.snapshot.started': 'Расширение собирает вкладки',
    'extension.snapshot.finished': 'Расширение передало вкладки',
    'extension.snapshot.failed': 'Расширение не смогло передать вкладки',
    'extension.helper.poll_ok': 'Расширение на связи',
    'extension.helper.poll_failed': 'Расширение потеряло связь с программой',
    'extension.job.received': 'Расширение получило задание',
    'extension.job.failed': 'Расширение сообщило об ошибке файла',
    'extension.job.cancelled': 'Задание отменено',
    'extension.tab.created': 'Открыта вкладка файла',
    'extension.window.reused': 'Использовано уже открытое окно браузера',
    'extension.window.closed': 'Окно браузера закрыто',
    'extension.content.ready_received': 'Страница готова к работе',
    'extension.page.stage': 'Новый этап на странице',
    'extension.page.navigate': 'Переход на страницу файла',
    'extension.page.command_received': 'Страница получила команду',
    'extension.page.assignment_received': 'Страница получила задание',
    'extension.page.challenge_detected': 'Nexus показал проверку (CAPTCHA): её проходит человек',
    'extension.page.site_refused': 'Nexus отказал в доступе или попросил подождать',
    'extension.page.focus_requested': 'Окно браузера выведено вперёд',
    'extension.page.resume_cancelled': 'Отменена старая незавершённая загрузка на сайте',
    'extension.page.manual_link': 'Взята ссылка на файл со страницы сайта',
    'extension.page.premium_start': 'Нажата кнопка Start download (Premium)',
    'extension.page.adblock_offer_closed': 'Закрыто окно про блокировщик рекламы',
    'extension.download.ignored': 'Эта загрузка браузера пропущена',
}

# Service chatter: the person reading the log is interested in the rest.
NOISE_EVENTS = frozenset({'ui.action', 'ui.heartbeat', 'ui.auto_update', 'ui.log_opened', 'ui.log_refresh',
                          'ui.browser_sync_received', 'job.progress',
                          # Every successful file ends with it: the copy in the browser is not needed.
                          'extension.page.cancel_received'})
NOISE_PREFIXES = ('server.', 'extension.snapshot.', 'extension.helper.poll_ok', 'extension.pairing.started',
                  'extension.content.', 'extension.page.stage', 'extension.page.command_received',
                  'extension.page.assignment_', 'extension.page.control_scan', 'extension.page.navigate',
                  'extension.trace.', 'extension.job.received', 'extension.tab.', 'extension.window.')


def local_clock(stamp):
    """A trace time is stored in UTC; the person compares it with the clock on their screen."""
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime('%H:%M:%S')
    except (TypeError, ValueError):
        return '--:--:--'


def is_main_event(event):
    if classify_event(event) == 'error':
        return True
    return event not in NOISE_EVENTS and not event.startswith(NOISE_PREFIXES)


def classify_event(event):
    if any(word in event for word in ('failed','error','expired','rejected','blocked','timeout','deadline','unavailable','attention','exception')):
        return 'error'
    if event.startswith(('extension.','server.')):
        return 'browser'
    if event.startswith(('job.','batch.','ui.download','ui.transfer','ui.nxm','ui.free')):
        return 'download'
    return 'app'


def describe_event(record):
    event = record.get('event','')
    data = record.get('data',{})
    label = EVENT_LABELS.get(event, event)
    if event == 'extension.page.control_scan':
        label += (' — элементов: ' + str(data.get('count','?')) +
                  ', совпадений Slow Download: ' + str(data.get('items','?')) +
                  ', доступных для нажатия: ' + str(data.get('selected','?')))
    if event == 'job.progress':
        step = {'page':'Открыта страница', 'file':'Поиск файла', 'manager':'Кнопка менеджера',
                'slow':'Нажатие Slow download', 'link':'Ожидание ссылки'}.get(data.get('step'), '')
        if step: label += ' — '+step
    if event == 'app.exception' or event == 'app.thread_exception':
        label += ' (' + str(data.get('error_type','exception')) + ')'
    return label


def diagnose_trace(records):
    """Summarize the most recent *actual* workflow, not a recovered early error.

    An initial sendMessage failure is common before content_script document_idle;
    it must not hide later proof of assignment and message delivery.
    """
    records = [r for r in records if isinstance(r, dict)]
    last_start = next((i for i in range(len(records)-1, -1, -1)
                       if records[i].get('event') in ('job.leased', 'batch.started')), 0)
    scoped = records[last_start:]
    names = [r.get('event') for r in scoped]
    if not any(r in names for r in ('job.leased','extension.page.job_started','extension.content.command_delivered')):
        return 'Скачивание через браузер ещё не запускалось.'
    clicked = 'extension.page.slow_button_clicked' in names or 'extension.page.button_clicked' in names
    file_delivered = 'extension.content.command_delivered' in names or 'extension.page.assignment_received' in names
    cancelled = 'batch.stopped' in names and names.index('batch.stopped') > (names.index('job.leased') if 'job.leased' in names else -1)
    if 'ui.transfer_finished' in names or 'ui.browser_file_imported' in names:
        return 'Получено подтверждение завершения передачи архива.'
    if 'extension.download.intercepted' in names or 'ui.auto_handoff' in names:
        return 'Сайт начал скачивание; файл передан программе.'
    if 'extension.page.login_required' in names:
        return 'Nexus требует вход в аккаунт в окне браузера.'
    if 'extension.page.no_button' in names:
        return 'Страница открыта, но кнопка скачивания не найдена.'
    if 'extension.page.countdown' in names:
        return 'Кнопка нажата, сайт вёл отсчёт; начало скачивания не подтверждено.'
    if 'extension.page.nxm_link_found' in names:
        return 'Страница сообщила об обнаружении NXM-ссылки; проверьте очередь.'
    if clicked:
        return ('Операция отменена после попытки нажатия Slow Download; ' if cancelled else 'Попытка нажатия Slow Download была, ') + 'получение файла не подтверждено.'
    probes = [r.get('data', {}) for r in scoped if r.get('event') == 'extension.page.control_scan']
    if probes:
        probe = probes[-1]
        if probe.get('items', 0) > 0 and probe.get('selected',0) == 0:
            explanation = 'Сценарий видит надпись Slow Download, но элемент недоступен для нажатия.'
        elif probe.get('items',0) == 0 and probe.get('body_hint',0):
            explanation = 'Текст Slow Download есть на странице, но ни одна поддерживаемая кнопка не совпала.'
        elif probe.get('items',0) == 0:
            explanation = 'Сценарий не обнаружил кнопку Slow Download в доступных элементах страницы.'
        else:
            explanation = 'Кнопка видна сценарию, но попытка нажатия не подтверждена.'
        if cancelled:
            explanation += ' Очередь отменена пользователем.'
        return explanation
    if file_delivered:
        return ('Команда успешно передана странице; достигнут поиск действия скачивания. '
                'Нахождение/нажатие Slow Download не подтверждено.' +
                (' Очередь отменена пользователем.' if cancelled else ''))
    if 'extension.content.command_failed' in names:
        return 'Команду не удалось передать сценарию страницы.'
    if cancelled:
        return 'Очередь была отменена до подтверждения этапа загрузки.'
    return 'Задание выдано, но работа сценария на странице не подтверждена.'
