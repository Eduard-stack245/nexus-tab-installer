import json
import queue
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from nexus_tab_installer.browser.auto_free import (AutoFreeBatch, identify_file_url, file_page_url,
                                                   safe_download_url, MAX_ATTEMPTS)
from nexus_tab_installer.browser.import_server import ImportServer, server_factory

NXM = 'nxm://skyrimspecialedition/mods/196600/files/12345?key=secret&expires=9999999999&user_id=456'
PAGE = 'https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files&file_id=12345'
PAGE2 = 'https://www.nexusmods.com/skyrimspecialedition/mods/196601?tab=files&file_id=12346'
CDN = 'https://cf-files.nexusmods.com/cdn/1704/196600/Some%20Mod-196600-1-0.7z?md5=abc&expires=1&user_id=2'


def spec(mod=196600, fid=12345, **extra):
    return {'game': 'skyrimspecialedition', 'mod_id': mod, 'file_id': fid, **extra}


def test_url_check_and_duplicates():
    assert identify_file_url(PAGE) == ('skyrimspecialedition', 196600, 12345)
    assert identify_file_url('https://www.nexusmods.com/games/skyrimspecialedition/mods/196600?file_id=12345') == ('skyrimspecialedition', 196600, 12345)
    for bad in ('http://evil.example/mods/12?file_id=1', 'https://www.nexusmods.com/',
                'https://www.nexusmods.com/skyrimspecialedition/mods/196600',
                'https://www.nexusmods.com/skyrimspecialedition/mods/196600?file_id=xx'):
        with pytest.raises(ValueError): identify_file_url(bad)
    batch = AutoFreeBatch()
    assert batch.start([PAGE, PAGE, PAGE2]) == 2
    job = batch.claim('browser-a')
    assert job['file_id'] == 12345
    assert job['url'] == file_page_url('skyrimspecialedition', 196600, 12345)
    assert batch.claim('browser-b') is None


def test_file_page_url_without_file_resolves_on_page():
    assert file_page_url('skyrimspecialedition', 5, 0) == 'https://www.nexusmods.com/skyrimspecialedition/mods/5?tab=files'
    batch = AutoFreeBatch()
    batch.start([spec(fid=0)])
    job = batch.claim('p')
    assert job['file_id'] == 0 and job['url'].endswith('?tab=files')
    # The page reports which main file it chose.
    assert batch.progress('p', job['id'], 'resolved', {'file_id': 777, 'size_kb': 10})
    out = batch.complete('p', job['id'], 'url', url=CDN)
    assert out['kind'] == 'url' and out['job']['file_id'] == 777


def test_signed_link_is_handed_to_app_and_queue_continues():
    batch = AutoFreeBatch()
    batch.start([spec(), spec(196601, 12346)])
    job = batch.claim('chrome-profile')
    assert not batch.complete('other-profile', job['id'], 'url', url=CDN)
    out = batch.complete('chrome-profile', job['id'], 'url', url=CDN, filename='Some Mod.7z')
    assert out['kind'] == 'url' and out['url'] == CDN
    assert out['job']['filename'] == 'Some Mod.7z'
    assert batch.job_state(job['id']) == 'confirmed'
    assert batch.claim('chrome-profile')['file_id'] == 12346


def test_browser_saved_file_is_reported_by_path():
    batch = AutoFreeBatch()
    batch.start([spec(transfer='browser')])
    job = batch.claim('p')
    assert job['transfer'] == 'browser'
    assert batch.progress('p', job['id'], 'download', {'bytes': 50, 'total': 100})
    assert batch.state()['jobs'][0]['bytes'] == 50
    out = batch.complete('p', job['id'], 'file', path=r'C:\Users\x\Downloads\mod.7z')
    assert out['kind'] == 'file' and out['path'].endswith('mod.7z')


def test_unsafe_links_are_rejected():
    assert safe_download_url(CDN)
    for bad in ('http://cf-files.nexusmods.com/x.7z', 'https://127.0.0.1/x.7z', 'https://localhost/x.7z',
                'https://10.0.0.5/x.7z', 'https://user:pw@cdn.example/x.7z', 'file:///c:/x.7z', '', None):
        assert not safe_download_url(bad)
    batch = AutoFreeBatch()
    batch.start([spec()])
    job = batch.claim('p')
    out = batch.complete('p', job['id'], 'url', url='https://127.0.0.1/evil.7z')
    assert out['kind'] == 'retry'


def test_failed_file_retries_then_fails_without_blocking_the_rest():
    batch = AutoFreeBatch()
    batch.start([spec(), spec(196601, 12346)])
    for attempt in range(MAX_ATTEMPTS):
        job = batch.claim('p')
        assert job['file_id'] == 12345
        out = batch.complete('p', job['id'], 'failed', reason='no_button')
        assert out['kind'] == ('retry' if attempt + 1 < MAX_ATTEMPTS else 'failed')
    assert not batch.state()['paused']
    assert batch.claim('p')['file_id'] == 12346


def test_unavailable_file_is_not_retried():
    batch = AutoFreeBatch()
    batch.start([spec()])
    job = batch.claim('p')
    out = batch.complete('p', job['id'], 'failed', reason='file_unavailable')
    assert out['kind'] == 'failed'
    assert 'недоступен' in out['job']['error']
    assert batch.is_idle()


def test_login_pauses_and_resume_continues_the_same_file():
    batch = AutoFreeBatch()
    batch.start([spec()])
    job = batch.claim('p')
    out = batch.complete('p', job['id'], 'failed', reason='login_required')
    assert out['kind'] == 'paused' and batch.state()['paused']
    assert batch.claim('p') is None
    assert batch.resume()
    again = batch.claim('p')
    assert again['id'] == job['id'] and again['file_id'] == 12345


def test_wrong_nxm_fails_only_that_file():
    batch = AutoFreeBatch(max_attempts=1)
    batch.start([PAGE, PAGE2]); job = batch.claim('browser-a'); saved = []
    wrong = 'nxm://skyrimspecialedition/mods/196601/files/12346?key=secret&expires=9999999999&user_id=456'
    out = batch.complete('browser-a', job['id'], 'nxm', wrong, submit=saved.append)
    assert saved == [] and out['kind'] == 'failed'
    assert not batch.state()['paused']
    assert batch.claim('browser-a')['file_id'] == 12346


def test_correct_nxm_delivered_and_no_token_in_status():
    batch = AutoFreeBatch()
    batch.start([PAGE, PAGE2])
    job = batch.claim('chrome-profile')
    saved = []
    assert batch.complete('chrome-profile', job['id'], 'nxm', NXM, submit=saved.append)['kind'] == 'nxm'
    assert saved == [NXM]
    assert all('secret' not in str(x) for x in batch.state()['jobs'])


def test_lost_browser_lease_is_retried_not_paused():
    tick = [100]
    batch = AutoFreeBatch(clock=lambda: tick[0])
    batch.start([PAGE, PAGE2]); job = batch.claim('browser-a')
    tick[0] += 1000
    expired = batch.tick()
    assert expired['kind'] == 'retry'
    again = batch.claim('browser-b')
    assert again['id'] == job['id']
    assert not batch.state()['paused']


def test_heartbeat_keeps_the_lease_and_owner_gets_lost_job_again():
    tick = [100]
    batch = AutoFreeBatch(clock=lambda: tick[0], lease_seconds=60)
    batch.start([PAGE]); job = batch.claim('a')
    for _ in range(5):
        tick[0] += 50
        assert batch.progress('a', job['id'], 'heartbeat')
    assert batch.tick() is None
    # Service worker restarted and lost its job: same job, same attempt.
    again = batch.claim('a')
    assert again['id'] == job['id']
    assert batch.state()['jobs'][0]['attempts'] == 1
    assert batch.claim('b') is None


def test_login_wait_extends_lease():
    tick = [0]
    batch = AutoFreeBatch(clock=lambda: tick[0], lease_seconds=60)
    batch.start([PAGE]); job = batch.claim('a')
    assert batch.progress('a', job['id'], 'login')
    tick[0] += 600
    assert batch.tick() is None
    assert batch.job_state(job['id']) == 'leased'


def test_requeue_to_browser_transfer_after_cdn_refusal():
    batch = AutoFreeBatch()
    batch.start([spec()])
    job = batch.claim('p')
    batch.complete('p', job['id'], 'url', url=CDN)
    assert batch.requeue('skyrimspecialedition', 196600, 12345, transfer='browser')
    again = batch.claim('p')
    assert again['transfer'] == 'browser'
    assert not batch.requeue('skyrimspecialedition', 196600, 12345, transfer='browser')


def test_gate_holds_next_link_while_the_app_is_downloading():
    tick = [0]
    busy = [True]
    batch = AutoFreeBatch(clock=lambda: tick[0])
    batch.gate = lambda: not busy[0]
    batch.start([PAGE])
    assert batch.claim('a') is None
    # Still a live helper and active work: no false "browser does not answer".
    tick[0] = 100
    assert batch.claim('a') is None
    assert batch.has_live_helper() and batch.is_active() and not batch.needs_browser()
    busy[0] = False
    assert batch.claim('a')['file_id'] == 12345


def test_add_appends_without_duplicates():
    batch = AutoFreeBatch()
    batch.start([spec()])
    assert batch.add([spec(), spec(196601, 12346)]) == 1
    assert len(batch.state()['jobs']) == 2


def test_cancel_and_external_os_protocol_callback():
    batch = AutoFreeBatch(); batch.start([PAGE]); job = batch.claim('browser-a')
    assert batch.acknowledge_external('skyrimspecialedition', 196600, 12345)
    assert not batch.complete('browser-a', job['id'], 'nxm', NXM, submit=lambda x: None)
    batch.start([PAGE]); batch.stop()
    assert batch.claim('browser-a') is None
    assert batch.state()['jobs'][0]['state'] == 'cancelled'
    assert batch.job_state(batch._jobs[0]['id']) == 'cancelled'


def test_unanswered_browser_warning():
    tick = [100]
    batch = AutoFreeBatch(clock=lambda: tick[0]); batch.start([PAGE]); tick[0] = 146
    assert batch.needs_browser()
    batch.claim('one')
    assert not batch.needs_browser()


def test_http_protocol_handoff_and_status(monkeypatch):
    batch = AutoFreeBatch(); batch.start([PAGE]); incoming = queue.Queue(); saved = []
    monkeypatch.setattr('nexus_tab_installer.browser.import_server.submit_nxm', saved.append)
    server = ImportServer(('127.0.0.1', 0), server_factory('my-token', incoming, auto_free=batch))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_address[1])
    def http(method, path, token=None, data=None, source='profile-1'):
        headers = {'X-Nexus-Source': source}
        if token: headers['X-Nexus-Token'] = token
        if data is not None: headers['Content-Type'] = 'application/json'
        with urlopen(Request(origin + path, data=json.dumps(data).encode() if data is not None else None,
                             headers=headers, method=method), timeout=3) as r:
            return json.load(r)
    try:
        with pytest.raises(HTTPError) as error:
            http('GET', '/auto/next')
        assert error.value.code == 403
        reply = http('GET', '/auto/next', 'my-token')
        job = reply['job']
        assert job['file_id'] == 12345 and reply['active'] is True
        assert http('GET', '/auto/status?job_id=' + job['id'], 'my-token')['state'] == 'leased'
        assert http('POST', '/auto/complete', 'my-token', data={
            'source_id': 'profile-1', 'job_id': job['id'], 'status': 'progress',
            'step': 'clicked'}) == {'ok': True}
        assert http('POST', '/auto/complete', 'my-token', data={
            'source_id': 'profile-1', 'job_id': job['id'], 'status': 'url', 'url': CDN,
            'file_id': 12345, 'filename': 'Some Mod.7z'}) == {'ok': True}
        events = []
        while not incoming.empty():
            events.append(incoming.get_nowait())
        outcomes = [e[1] for e in events if e[0] == 'auto_outcome']
        assert outcomes and outcomes[-1]['kind'] == 'url' and outcomes[-1]['url'] == CDN
        assert ('auto_free_update',) in events
        assert http('GET', '/auto/status?job_id=' + job['id'], 'my-token')['state'] == 'confirmed'
        assert http('GET', '/auto/next', 'my-token')['active'] is False
    finally:
        server.shutdown(); server.server_close()


def test_free_queue_supports_games_path(tmp_path):
    from nexus_tab_installer.nexus.free_workflow import FreeQueue
    f = FreeQueue(tmp_path / 'free.json')
    f.add(['https://www.nexusmods.com/games/skyrimspecialedition/mods/196600?tab=files&file_id=12345'])
    assert f.contains_file('skyrimspecialedition', 196600, 12345)
    assert f.remove_file('skyrimspecialedition', 196600, 12345)
