"""Offline HTTP simulations: verify real downloaded bytes, not UI strings."""
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
import io
import tempfile

import pytest

from nexus_tab_installer.downloads.transfer import download, DownloadError
from nexus_tab_installer.nexus.free_workflow import FreeQueue, files_page_url

URL = 'https://cdn.example.org/archive'


class FakeResponse:
    def __init__(self, content, *, status=200, headers=None, fail_after=None, redirect=None):
        self.contents = content
        self.pos = 0
        self.status = status
        self.headers = headers or {'Content-Length': str(len(content)), 'Content-Type': 'application/octet-stream'}
        self.fail_after = fail_after
        self.redirect = redirect or URL
        self.failed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.redirect

    def read(self, size):
        if self.fail_after is not None and self.pos >= self.fail_after and not self.failed:
            self.failed = True
            raise TimeoutError('test connection drop')
        if self.pos >= len(self.contents):
            return b''
        # Force multiple reads to exercise progress + retry.
        upper = min(self.pos + 4, len(self.contents))
        chunk = self.contents[self.pos:upper]
        self.pos = upper
        return chunk


def test_resume_after_network_dropout():
    calls = []
    response1 = FakeResponse(b'ABCDEFGH', fail_after=4)
    response2 = FakeResponse(b'EFGH', status=206, headers={
        'Content-Range': 'bytes 4-7/8', 'Content-Length': '4',
        'Content-Type': 'application/octet-stream'})

    def open_fake(req, timeout):
        calls.append(req.get_header('Range'))
        return [response1, response2][len(calls)-1]

    with tempfile.TemporaryDirectory() as d:
        dest = download(URL, d, 'mod.7z', file_identity='skyrim-42-13', opener=open_fake, sleeper=lambda n: None)
        assert dest.read_bytes() == b'ABCDEFGH'
        assert calls == [None, 'bytes=4-']
        assert not list(Path(d).glob('*.part'))


def test_restart_when_cdn_ignores_range():
    with tempfile.TemporaryDirectory() as d:
        partial = Path(d) / '.mod.zip.skyrim-42-13.part'
        partial.write_bytes(b'OLD')
        headers = []
        def opener(req, timeout):
            headers.append(req.get_header('Range'))
            return FakeResponse(b'NEWFILE')
        output = download(URL, d, 'mod.zip', file_identity='skyrim-42-13', opener=opener)
        assert headers == ['bytes=3-']
        assert output.read_bytes() == b'NEWFILE'


def test_retry_transient_http_status():
    calls = []
    def opener(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError(URL, 503, 'Service Unavailable', {}, io.BytesIO(b''))
        return FakeResponse(b'OK')
    with tempfile.TemporaryDirectory() as d:
        assert download(URL, d, 'f.zip', opener=opener, sleeper=lambda n: None).read_bytes() == b'OK'
        assert len(calls) == 2


def test_403_does_not_loop_or_create_fake_archive():
    calls = []
    def opener(req, timeout):
        calls.append(1)
        raise HTTPError(URL, 403, 'Forbidden', {}, io.BytesIO(b''))
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(DownloadError, match='HTTP 403'):
            download(URL, d, 'f.zip', opener=opener, sleeper=lambda n: None)
        assert len(calls) == 1
        assert not (Path(d) / 'f.zip').exists()


@pytest.mark.parametrize('content_type,body', [
    ('text/html', b'<html>Denied</html>'),
    ('application/octet-stream', b'<!DOCTYPE html>bad'),
    ('application/json', b'{"message":"forbidden"}'),
])
def test_never_save_html_or_json_as_mod(content_type, body):
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(DownloadError, match='страниц|HTML'):
            download(URL, d, 'f.zip', attempts=1,
                     opener=lambda *a, **kw: FakeResponse(body, headers={'Content-Length': str(len(body)), 'Content-Type': content_type}))
        assert not (Path(d) / 'f.zip').exists()


def test_no_insecure_redirect():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(DownloadError, match='небезопасный'):
            download(URL, d, 'mod.zip', opener=lambda *a, **kw: FakeResponse(b'FINE', redirect='http://example.org/file'))


def test_free_queue_does_not_accept_unselected_file():
    with tempfile.TemporaryDirectory() as d:
        q = FreeQueue(Path(d) / 'queue.json')
        q.add([files_page_url('https://www.nexusmods.com/skyrimspecialedition/mods/42', 123)])
        assert q.contains_file('skyrimspecialedition', 42, 123)
        assert not q.contains_file('skyrimspecialedition', 42, 999)
        assert not q.contains_file('fallout4', 42, 123)
        assert len(FreeQueue(q.path)) == 1


def test_rescan_does_not_erase_pending_free_requests():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    start = src[src.index('    def start_workflow('):src.index('    def download_selected(')]
    assert 'free_queue.clear()' not in start
    handler = src[src.index('    def import_nxm('):src.index('    def open_free(')]
    assert 'free_queue.contains_file(' in handler
    assert 'NexusClient(key.value.strip()).files(' not in handler

def test_html_invalidates_partial_cache():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(DownloadError):
            download(URL, d, 'fake.7z', attempts=1, file_identity='test',
                opener=lambda *a, **kw: FakeResponse(b'<!DOCTYPE html>access denied'))
        assert not (Path(d) / '.fake.7z.test.part').exists()


def test_remove_one_file_does_not_remove_other_mod_prefix():
    with tempfile.TemporaryDirectory() as d:
        q = FreeQueue(Path(d) / 'queue.json')
        q.add([files_page_url('https://www.nexusmods.com/skyrimspecialedition/mods/420', 123)])
        assert not q.remove_file('skyrimspecialedition', 42, 123)
        assert len(q) == 1
