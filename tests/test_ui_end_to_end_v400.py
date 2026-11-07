"""The real Flet controller without a window; the test plays the browser extension.

Covers the whole path: links or tabs -> selected files -> browser job -> signed
link or saved file -> portable library -> finished batch.
"""
import asyncio
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ft = pytest.importorskip('flet')

from nexus_tab_installer.browser.import_server import ImportServer
from nexus_tab_installer.browser.windows_tabs import TabScan
from nexus_tab_installer.core.paths import storage_paths
from nexus_tab_installer.core.session_library import ArchiveIndex, ModCollection, record_archive
from nexus_tab_installer.core.settings import load_settings, save_settings
from nexus_tab_installer.core.trace import EVENTS
from nexus_tab_installer.downloads.transfer import LinkRejected, name_from_url
from nexus_tab_installer.nexus.api import NexusError

ROOT = Path(__file__).resolve().parents[1]
GAME = 'skyrimspecialedition'
MOD_A = f'https://www.nexusmods.com/{GAME}/mods/196600'
MOD_B = 'https://www.nexusmods.com/skyrim/mods/7'
CDN = f'https://cf-files.nexusmods.com/cdn/1704/196600/Mod-196600-1-0.7z?md5=abc&expires=1&user_id=2'
SOURCE = 'a' * 32


def walk(control, seen):
    if control is None or id(control) in seen:
        return
    seen.add(id(control))
    yield control
    for attr in ('controls', 'content'):
        child = getattr(control, attr, None)
        if isinstance(child, (list, tuple)):
            for entry in child:
                yield from walk(entry, seen)
        elif isinstance(child, ft.Control):
            yield from walk(child, seen)


class Harness:
    def __init__(self, module, page, server, home):
        self.module, self.page, self.server, self.home = module, page, server, home
        self.process_queue = page.run_task.call_args.args[0]
        self.token = load_settings(storage_paths()['config'])['browser_token']
        self.base = f'http://127.0.0.1:{server.server_address[1]}'
        self.sessions = home / 'data' / 'sessions'

    def archive_named(self, name):
        return next(self.sessions.glob(f'*/{name}'), None)

    def archive_of(self, game, mod_id, file_id):
        return ArchiveIndex(self.sessions).find(game, mod_id, file_id)

    def seed_archive(self, game, mod_id, file_id, name, age_days=0):
        """An archive that an earlier download run left behind."""
        folder = ModCollection.create(self.sessions, [], clock=lambda: datetime(2020, 1, 1, 10, 0, 0)).folder
        path = folder / name
        path.write_bytes(b'7z')
        moment = path.stat().st_mtime - age_days * 86400
        os.utime(path, (moment, moment))
        return record_archive(path, game, mod_id, file_id)

    def tree(self):
        seen = set()
        for root in self.page.add.call_args.args:
            yield from walk(root, seen)

    def click(self, label):
        button = next(c for c in self.tree() if isinstance(c, ft.Button) and c.content == label)
        button.on_click(None)

    def paste_links(self, text):
        field = next(c for c in self.tree() if isinstance(c, ft.TextField)
                     and 'Вставьте ссылки Nexus' in (c.hint_text or ''))
        field.value = text
        self.click('Добавить ссылки')

    def text_starting(self, prefix):
        return next((c.value for c in self.tree() if isinstance(c, ft.Text)
                     and isinstance(c.value, str) and c.value.startswith(prefix)), None)

    def _http(self, path, data=None):
        headers = {'X-Nexus-Token': self.token, 'X-Nexus-Source': SOURCE}
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data).encode()
        try:
            with urlopen(Request(self.base + path, data=body, headers=headers,
                                 method='POST' if body is not None else 'GET'), timeout=5) as reply:
                return reply.status, json.load(reply)
        except HTTPError as error:
            return error.code, {}

    async def get(self, path):
        return (await asyncio.to_thread(self._http, path))[1]

    async def report(self, job, **data):
        status, reply = await asyncio.to_thread(
            self._http, '/auto/complete', {'source_id': SOURCE, 'job_id': job['id'], **data})
        assert status == 200, (status, data)
        return reply

    async def snapshot(self, tabs):
        status, _ = await asyncio.to_thread(self._http, '/snapshot',
                                            {'browser': 'edge', 'source_id': SOURCE, 'tabs': tabs})
        assert status == 200

    async def until(self, condition, timeout=15):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            value = condition()
            if value:
                return value
            await asyncio.sleep(0.05)
        raise AssertionError('Condition not reached. Status: ' + str(self.status_lines()))

    def status_lines(self):
        log = self.home / 'data' / 'logs' / 'download-trace.jsonl'
        failures = [line for line in log.read_text(encoding='utf-8').splitlines() if 'exception' in line]
        terminal = [line for line in log.read_text(encoding='utf-8').splitlines() if 'job.completed' in line or 'terminal' in line]
        return {'exceptions': failures[-5:], 'events': terminal[-8:],
                'text': [c.value for c in self.tree() if isinstance(c, ft.Text) and isinstance(c.value, str)
                         and any(word in c.value for word in ('Готово', 'Ошибк', 'Браузер', 'Файлы подгот', 'Скач'))]}

    async def next_job(self, timeout=15):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            reply = await self.get('/auto/next')
            if reply.get('job'):
                return reply['job']
            await asyncio.sleep(0.1)
        raise AssertionError('No browser job. Status: ' + str(self.status_lines()))

    def finished(self):
        return self.text_starting('Готово: скачано')

    def run(self, scenario):
        async def runner():
            loop_task = asyncio.create_task(self.process_queue())
            try:
                await asyncio.wait_for(scenario(), 60)
            finally:
                loop_task.cancel()
        asyncio.run(runner())


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path))
    import nexus_tab_installer.ui.flet_app as module
    from nexus_tab_installer.browser import pacing
    # The pace has its own tests; here files must follow each other at once.
    monkeypatch.setitem(pacing.PRESETS, 'careful', pacing.PacePreset('test', 0, 10**6, 0, 10**6, 0))
    monkeypatch.setattr(pacing, 'MIN_FAILURE_GAP', 0)
    paths = storage_paths()
    for name, value in (('PATHS', paths), ('STATE_DIR', paths['base']),
                        ('PACK_FILE', paths['pack']), ('CONFIG_FILE', paths['config'])):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, 'pending_nxm', lambda: [])
    # The search for managers must see nothing of the computer the tests run on.
    monkeypatch.setattr(module, 'find_mo2_instances', lambda *_: [])
    monkeypatch.setattr(module.vortex_link, 'find_vortex', lambda *a, **k: None)
    # Nor may they talk to Nexus, read other programs, show messages or depend on the language of Windows.
    monkeypatch.setattr(module, 'mod_infos', lambda ids: {})
    monkeypatch.setattr(module, 'mod_files', lambda *a: [])
    monkeypatch.setattr(module, 'installed_mods', lambda *a: {})
    monkeypatch.setattr(module, 'notify', lambda *a, **k: None)
    monkeypatch.setattr(module, 'system_language', lambda: 'ru')
    monkeypatch.setattr(module, 'AUTO_SCAN', False)
    # main() installs its own exception hooks: restore them after the test.
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    monkeypatch.setattr(threading, 'excepthook', threading.excepthook)
    servers = []

    class RecordingServer(ImportServer):
        def __init__(self, _address, handler):
            super().__init__(('127.0.0.1', 0), handler)
            servers.append(self)
    monkeypatch.setattr(module, 'ImportServer', RecordingServer)

    def start(config=None, download=None, client=None):
        if config:
            save_settings(paths['config'], config)
        def saved_file(url, directory, name, on_progress=None, cancel=None, **_):
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (name or name_from_url(url))
            target.write_bytes(b'7z-data')
            if on_progress:
                on_progress(7, 7)
            return target
        monkeypatch.setattr(module, 'download', download or saved_file)
        if client is not None:
            monkeypatch.setattr(module, 'NexusClient', client)
        page = MagicMock()
        module.main(page)
        return Harness(module, page, servers[-1], tmp_path)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def account(premium=False, files=None, listing_error=None, link_error=None, link=CDN):
    class FakeClient:
        def __init__(self, _key):
            pass
        def validate(self):
            return {'is_premium': premium}
        def files(self, game, mod_id):
            if listing_error:
                raise NexusError(listing_error)
            return (files or {}).get((game, mod_id), [])
        def download_links(self, *args):
            if link_error:
                raise NexusError(link_error)
            return link
    return FakeClient


def nexus_file(file_id, category='MAIN', name=None, when=1000):
    return {'file_id': file_id, 'category_name': category, 'file_name': name or f'file-{file_id}.7z',
            'name': f'File {file_id}', 'version': '1', 'size_kb': 10, 'uploaded_timestamp': when}


KEY = {'remember_api_key': True, 'api_key': 'test-key'}


def test_free_account_without_key_downloads_a_list_of_links(launch):
    h = launch()
    source = h.home / 'Downloads' / 'main.7z'
    source.parent.mkdir()
    source.write_bytes(b'7z-browser')

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=12345\n{MOD_B}')
        h.click('Скачать выбранные')
        first = await h.next_job()
        assert (first['game'], first['mod_id'], first['file_id']) == (GAME, 196600, 12345)
        assert first['url'] == f'{MOD_A}?tab=files&file_id=12345'
        await h.report(first, status='progress', step='clicked')
        await h.report(first, status='url', url=CDN, file_id=12345)
        # No file known and no API key: the page chooses the main file.
        second = await h.next_job()
        assert second['file_id'] == 0 and second['url'] == f'{MOD_B}?tab=files'
        await h.report(second, status='progress', step='resolved', info={'file_id': 99, 'size_kb': 1})
        await h.report(second, status='file', path=str(source), file_id=99, filename='main.7z')
        await h.until(h.finished)

    h.run(scenario)
    assert 'Готово: скачано и обработано 2 из 2 файлов.' == h.finished()
    assert h.archive_named('Mod-196600-1-0.7z').parent == h.archive_named('main.7z').parent, 'One run, one folder'
    assert h.archive_named('main.7z').read_bytes() == b'7z-browser'
    assert not source.exists()
    manifests = list((h.home / 'data' / 'sessions').glob('*/collection.json'))
    recorded = [json.loads(p.read_text(encoding='utf-8'))['files'] for p in manifests]
    assert sorted(f['file_id'] for files in recorded for f in files) == [99, 12345]
    assert 'md5=abc' not in (h.home / 'data' / 'history' / 'download-history.json').read_text(encoding='utf-8')


def test_open_tabs_in_any_state_keep_the_file_of_the_tab(launch):
    h = launch()

    async def scenario():
        await h.snapshot([])  # the extension is connected
        h.click('Найти моды')
        await h.until(lambda: h.text_starting('Запрашиваем свежий список'))
        await h.get('/auto/next')  # the extension sees the scan request
        await h.snapshot([
            {'url': f'{MOD_A}?tab=description', 'title': 'Mod A'},
            {'url': f'{MOD_A}?tab=files&file_id=11&nmm=1', 'title': 'Mod A'},
            {'url': f'{MOD_A}?tab=files&file_id=22', 'title': 'Mod A'},
            {'url': f'{MOD_B}?tab=images', 'title': 'Mod B'},
        ])
        await h.until(lambda: h.text_starting('Получено 2 модов'))
        h.click('Скачать выбранные')
        seen = []
        for _ in range(3):
            job = await h.next_job()
            seen.append((job['mod_id'], job['file_id']))
            await h.report(job, status='url', url=CDN.replace('196600', str(job['mod_id'])), file_id=job['file_id'] or 5)
        assert seen == [(196600, 11), (196600, 22), (7, 0)]
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 3 из 3 файлов.'


def windows_scan_of(url):
    def scan(**_):
        return TabScan(mods=[{'url': url, 'title': 'Windows'}], browsers=['Edge'])
    return scan


def test_scan_waits_for_a_known_extension_instead_of_windows(launch, monkeypatch):
    h = launch(config={'browser_paired': True})
    def unexpected(**_):
        raise AssertionError('Windows scan must not run while the extension can answer')
    monkeypatch.setattr(h.module, 'scan_open_windows', unexpected)

    async def scenario():
        h.click('Найти моды')
        await h.until(lambda: h.text_starting('Ждём ответ расширения'))
        marker = (await h.get('/auto/next'))['scan_marker']
        assert not marker.endswith(':0')  # otherwise the extension would not sync
        await h.snapshot([{'url': f'{MOD_A}?tab=files&file_id=11', 'title': 'Mod A'}])
        await h.until(lambda: h.text_starting('Получено 1 модов'))

    h.run(scenario)


def test_silent_known_extension_falls_back_to_windows(launch, monkeypatch):
    h = launch(config={'browser_paired': True})
    monkeypatch.setattr(h.module, 'FIRST_SNAPSHOT_WAIT', 0.3)
    monkeypatch.setattr(h.module, 'scan_open_windows', windows_scan_of(MOD_B))

    async def scenario():
        h.click('Найти моды')
        await h.until(lambda: h.text_starting('Найдено 1 модов'))

    h.run(scenario)


def test_first_contact_is_remembered_for_the_next_scan(launch, monkeypatch):
    h = launch()
    monkeypatch.setattr(h.module, 'scan_open_windows', windows_scan_of(MOD_B))

    async def scenario():
        # Never connected before: no point in waiting for an extension.
        h.click('Найти моды')
        await h.until(lambda: h.text_starting('Найдено 1 модов'))
        await h.snapshot([])
        config = storage_paths()['config']
        await h.until(lambda: load_settings(config).get('browser_paired'))

    h.run(scenario)


def test_refused_link_is_saved_by_the_browser_instead(launch):
    def refused(*_args, **_kwargs):
        raise LinkRejected(403)
    h = launch(download=refused)
    source = h.home / 'mod.7z'
    source.write_bytes(b'7z')

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=12345')
        h.click('Скачать выбранные')
        job = await h.next_job()
        assert job['transfer'] == 'app'
        await h.report(job, status='url', url=CDN, file_id=12345)
        again = await h.next_job()
        assert again['id'] == job['id'] and again['transfer'] == 'browser'
        await h.report(again, status='file', path=str(source), file_id=12345)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'
    assert h.archive_named('mod.7z')


def test_failed_file_does_not_stop_the_others(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        for _ in range(3):
            job = await h.next_job()
            assert job['file_id'] == 1
            await h.report(job, status='failed', reason='no_button')
        job = await h.next_job()
        assert job['file_id'] == 2
        await h.report(job, status='url', url=CDN, file_id=2)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished().startswith('Готово: скачано и обработано 1 из 2 файлов. Ошибок: 1')


def test_login_pause_resumes_with_the_same_file(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='failed', reason='login_required')
        await h.until(lambda: next(c.visible for c in h.tree() if isinstance(c, ft.Button)
                                   and c.content == 'Продолжить загрузку'))
        assert (await h.get('/auto/next'))['job'] is None
        h.click('Продолжить загрузку')
        again = await h.next_job()
        assert again['id'] == job['id']
        await h.report(again, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_premium_with_key_downloads_through_the_api_without_a_browser(launch):
    files = {(GAME, 196600): [nexus_file(1, when=1), nexus_file(2, name='new.7z', when=2)]}
    h = launch(config=KEY, client=account(premium=True, files=files))

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        await h.until(h.finished)
        assert (await h.get('/auto/next'))['job'] is None

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'
    assert h.archive_of(GAME, 196600, 2).path.name == 'new.7z'


def test_premium_api_refusal_falls_back_to_the_browser(launch):
    files = {(GAME, 196600): [nexus_file(2)]}
    h = launch(config=KEY, client=account(premium=True, files=files, link_error='HTTP 403'))

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        job = await h.next_job()
        assert job['file_id'] == 2
        await h.report(job, status='url', url=CDN, file_id=2)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_free_key_chooses_newest_main_and_handles_mods_without_main(launch):
    files = {(GAME, 196600): [nexus_file(1, when=1), nexus_file(2, name='new.7z', when=2),
                              nexus_file(3, category='OPTIONAL', when=3)],
             ('skyrim', 7): [nexus_file(8, category='OLD_VERSION', when=9),
                             nexus_file(9, category='OPTIONAL', name='only.7z', when=1)]}
    h = launch(config=KEY, client=account(files=files))

    async def scenario():
        h.paste_links(f'{MOD_A}\n{MOD_B}')
        h.click('Скачать выбранные')
        first = await h.next_job()
        assert first['file_id'] == 2 and first['filename'] == 'new.7z'
        await h.report(first, status='url', url=CDN, file_id=2)
        second = await h.next_job()
        assert second['file_id'] == 9
        await h.report(second, status='url', url=CDN, file_id=9)
        await h.until(h.finished)

    h.run(scenario)
    assert h.archive_of(GAME, 196600, 2).path.name == 'new.7z'
    assert h.archive_of('skyrim', 7, 9).path.name == 'only.7z'


def test_broken_api_listing_still_downloads_through_the_browser(launch):
    h = launch(config=KEY, client=account(listing_error='HTTP 429'))

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        job = await h.next_job()
        assert job['file_id'] == 0
        await h.report(job, status='progress', step='resolved', info={'file_id': 4})
        await h.report(job, status='url', url=CDN, file_id=4)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_already_downloaded_archive_is_not_downloaded_again(launch):
    h = launch()
    h.seed_archive(GAME, 196600, 5, 'old.7z')

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        h.click('Скачать выбранные')
        await h.until(h.finished)
        assert (await h.get('/auto/next'))['job'] is None

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_every_diagnostic_event_of_the_extension_is_allowed():
    # One rejected event would block the extension's offline diagnostics ring.
    names = set()
    for browser in ('chromium', 'firefox'):
        folder = ROOT / 'extensions' / browser
        background = (folder / 'background.js').read_text(encoding='utf-8')
        auto = (folder / 'auto_download.js').read_text(encoding='utf-8')
        page = (folder / 'auto_free_content.js').read_text(encoding='utf-8')
        names |= set(re.findall(r"traceEvent\('([a-z0-9_.-]+)'", background + auto))
        names |= {'page.' + n for n in re.findall(r"\btrace\('([a-z0-9_.-]+)'", page)}
        names |= {'page.' + n for n in re.findall(r"trace\(premium \? '([a-z_]+)' : '([a-z_]+)'", page)[0]}
    assert names
    missing = sorted(n for n in names if not n.endswith('.') and 'extension.' + n not in EVENTS)
    assert not missing, missing
