"""Regression coverage for diagnostics while the browser waits on the file page."""
import queue
import json
import threading
from urllib.request import Request, urlopen
from nexus_tab_installer.browser.auto_free import AutoFreeBatch
from nexus_tab_installer.browser.import_server import ImportServer, server_factory

PAGE='https://www.nexusmods.com/skyrimspecialedition/mods/197526?tab=files&file_id=829609'

def test_progress_is_authenticated_and_does_not_confirm_a_download():
    batch=AutoFreeBatch(max_attempts=1)
    assert batch.start([PAGE])==1
    job=batch.claim('edge-profile')
    assert not batch.progress('wrong-profile',job['id'],'file')
    assert not batch.progress('edge-profile',job['id'],'https://secret.example/abc')
    assert batch.progress('edge-profile',job['id'],'file')
    assert batch.job_state(job['id'])=='leased'
    assert batch.state()['jobs'][0]['step']=='Ищем кнопку скачивания'
    out = batch.complete('edge-profile',job['id'],'failed',reason='no_button')
    assert out['kind'] == 'failed'
    assert not batch.state()['paused']
    assert batch.state()['jobs'][0]['error']=='На странице не найдена кнопка скачивания'


def test_legacy_attention_result_is_a_retryable_page_error():
    batch=AutoFreeBatch()
    batch.start([PAGE])
    job=batch.claim('edge-profile')
    out = batch.complete('edge-profile',job['id'],'attention',note='No download control found')
    assert out['kind'] == 'retry'
    assert 'No download control found' in batch.state()['jobs'][0]['error']


def test_progress_http_not_a_download_completion():
    batch=AutoFreeBatch()
    batch.start([PAGE]); q=queue.Queue()
    server=ImportServer(('127.0.0.1',0),server_factory('secret-token',q,auto_free=batch))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    try:
        headers={'X-Nexus-Token':'secret-token','X-Nexus-Source':'edge-profile'}
        with urlopen(Request(base+'/auto/next',headers=headers),timeout=3) as r:job=json.load(r)['job']
        data={'source_id':'edge-profile','job_id':job['id'],'status':'progress','step':'clicked'}
        with urlopen(Request(base+'/auto/complete',method='POST',headers=headers|{'Content-Type':'application/json'},data=json.dumps(data).encode()),timeout=3) as r:
            assert json.load(r)=={'ok':True}
        assert q.get(timeout=1)==('auto_free_update',)
        assert batch.job_state(job['id'])=='leased'
    finally:
        server.shutdown();server.server_close()
