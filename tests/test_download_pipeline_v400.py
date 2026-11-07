"""Links keep the chosen file, direct CDN links, per-file batch tracking."""
import io
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError

import pytest

from nexus_tab_installer.core.links import parse_mod, normalize_entries, extract_mod_links
from nexus_tab_installer.core.workflow_session import WorkflowSession
from nexus_tab_installer.downloads.transfer import (download, LinkRejected, name_from_url,
                                                    name_from_disposition)
from nexus_tab_installer.downloads.queue import DownloadQueue

MOD = 'https://www.nexusmods.com/skyrimspecialedition/mods/12604'


def test_tab_on_a_file_page_keeps_that_file():
    item = parse_mod(MOD + '?tab=files&file_id=749043&nmm=1')
    assert item['file_ids'] == [749043]
    assert 'file_ids' not in parse_mod(MOD + '?tab=description')
    assert parse_mod('https://www.nexusmods.com/games/skyrimspecialedition/mods/12604?file_id=5')['file_ids'] == [5]


def test_duplicate_tabs_merge_their_files():
    rows = normalize_entries([
        {'url': MOD, 'title': 'SkyUI'},
        {'url': MOD + '?tab=files&file_id=1'},
        {'url': MOD + '?tab=files&file_id=2'},
        {'url': MOD + '?tab=files&file_id=1'},
    ])
    assert len(rows) == 1
    assert rows[0]['file_ids'] == [1, 2] and rows[0]['title'] == 'SkyUI'


def test_pasted_list_of_links_with_and_without_files():
    text = f'{MOD}\n{MOD}?tab=files&file_id=7 и ещё https://www.nexusmods.com/skyrim/mods/3'
    links = extract_mod_links(text)
    rows = normalize_entries([{'url': u} for u in links])
    assert [(r['game'], r['mod_id'], r.get('file_ids')) for r in rows] == [
        ('skyrimspecialedition', 12604, [7]), ('skyrim', 3, None)]


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None, url='https://cdn.example/x'):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self._url = url
    def geturl(self):
        return self._url
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


def test_name_comes_from_cdn_url_or_disposition():
    url = 'https://cf-files.nexusmods.com/cdn/1704/12604/SkyUI_5_2_SE-12604-5-2SE.7z?md5=a&expires=1'
    assert name_from_url(url) == 'SkyUI_5_2_SE-12604-5-2SE.7z'
    assert name_from_disposition('attachment; filename="A B.zip"') == 'A B.zip'
    assert name_from_disposition("attachment; filename*=UTF-8''Mod%20%D0%A4.7z") == 'Mod Ф.7z'
    with tempfile.TemporaryDirectory() as d:
        opener = lambda req, timeout: Response(b'7z\xbc\xaf\x27\x1cdata', url=url,
                                               headers={'Content-Length': '10'})
        saved = download(url, d, None, opener=opener, file_identity='sse-12604-1')
        assert saved.name == 'SkyUI_5_2_SE-12604-5-2SE.7z'
        # A complete file of that name is reused instead of downloaded again.
        def fail(*a, **k): raise AssertionError('must not download twice')
        assert download(url, d, None, opener=fail) == saved


def test_refused_signed_link_is_reported_for_browser_fallback():
    def opener(req, timeout):
        raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(b''))
    with tempfile.TemporaryDirectory() as d, pytest.raises(LinkRejected) as error:
        download('https://cdn.example/a.7z', d, 'a.7z', opener=opener)
    assert error.value.code == 403


def test_queue_downloads_direct_url_jobs():
    seen = []
    done = threading.Event()
    def process(job, progress):
        seen.append((job.url, job.task))
        done.set()
    queue = DownloadQueue(process)
    try:
        job = queue.add('skyrimspecialedition', 1, 2, '', url='https://cdn.example/a.7z', task='skyrimspecialedition:1:2')
        assert done.wait(3)
        assert seen == [('https://cdn.example/a.7z', 'skyrimspecialedition:1:2')]
        assert job.task == 'skyrimspecialedition:1:2'
    finally:
        queue.close()


def test_history_never_stores_signed_links(tmp_path):
    history = tmp_path / 'h.json'
    queue = DownloadQueue(lambda job, progress: None, history_path=history)
    try:
        queue.add('skyrim', 1, 2, 'a.7z', url='https://cdn.example/a.7z?md5=secret')
    finally:
        queue.close()
    assert 'secret' not in history.read_text(encoding='utf-8')


def test_session_tracks_every_selected_file():
    item = {'game': 'skyrim', 'mod_id': 5}
    other = {'game': 'skyrim', 'mod_id': 6}
    run = WorkflowSession([item, other])
    run.expect(item, 'skyrim:5:0')
    run.expect(item, 'skyrim:5:9')
    run.expect(other, 'skyrim:6:1')
    # An unresolved main file matches whatever file the browser delivers.
    assert run.task_for('skyrim', 5, 123) == 'skyrim:5:0'
    assert run.task_for('skyrim', 5, 9) == 'skyrim:5:9'
    assert run.finish('skyrim:5:9', True)
    assert not run.finish('skyrim:5:9', True)
    run.fail_mod(other)
    assert run.task_counts() == {'pending': 1, 'done': 1, 'failed': 1}
    run.finish('skyrim:5:0', True)
    assert run.task_counts()['pending'] == 0
    assert run.mod_counts(item) == {'pending': 0, 'done': 2, 'failed': 0}
