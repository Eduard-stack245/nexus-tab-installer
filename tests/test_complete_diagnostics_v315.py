from __future__ import annotations

import json
from queue import Queue
from threading import Thread
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest

from nexus_tab_installer.core.trace import TraceLog, safe_event
from nexus_tab_installer.browser.auto_free import AutoFreeBatch
from nexus_tab_installer.browser.import_server import server_factory


def test_trace_redacts_sensitive_fields_and_rejects_arbitrary_text(tmp_path):
    logger = TraceLog(tmp_path)
    logger.emit('extension.page.stage', step='slow', mod_id=197526, file_id=829609,
        api_key='very-sensitive-secret', token='secret-token',
        nxm='nxm://skyrimspecialedition/mods/197526/files/829609?key=private',
        url='https://example.org/private?token=secret',
        reason='https://example.org/private',
        source_id='a-profile-source-id')
    payload = logger.path.read_text(encoding='utf-8')
    for sensitive in ['very-sensitive', 'secret-token', 'nxm://', 'https://', 'a-profile-source-id']:
        assert sensitive not in payload
    data=json.loads(payload)['data']
    assert data['file_id']==829609
    assert data['step']=='slow'
    assert len(data['source_id'])==12
    assert 'reason' not in data
    with pytest.raises(ValueError):
        safe_event('extension.page.private_secret', {'api_key':'secret'})


def test_log_rotation_and_sanitized_export(tmp_path):
    logger=TraceLog(tmp_path,max_bytes=140)
    for i in range(8):
        logger.emit('ui.heartbeat',count=i,stage='active')
    assert (tmp_path/'download-trace.previous.jsonl').exists()
    pack=logger.export()
    with ZipFile(pack) as z:
        assert set(z.namelist())=={
            'download-trace.previous.jsonl','download-trace.jsonl','README.txt','SUMMARY.txt'}
        assert 'data/config/config.json' not in z.namelist()
        for name in z.namelist():
            assert 'api_key' not in z.read(name).decode('utf-8')


def test_extension_to_local_http_to_log_and_authorization(tmp_path):
    trace=TraceLog(tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),server_factory(
        'private-secret',Queue(),auto_free=AutoFreeBatch(trace=trace),trace=trace))
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/diagnostics/event'
        body=json.dumps({'event':'page.slow_button_found','data':{
            'job_id':'a'*24,'tab_id':12,'reason':'nxm://token',
            'nxm':'nxm://private?key=secret','step':'slow'}}).encode()
        unauth=Request(url,data=body,headers={'Content-Type':'application/json'})
        with pytest.raises(HTTPError) as exc:
            urlopen(unauth,timeout=3)
        assert exc.value.code==403
        assert not trace.path.exists()
        authorized=Request(url,data=body,headers={
            'Content-Type':'application/json','X-Nexus-Token':'private-secret'})
        with urlopen(authorized,timeout=3) as result:
            assert result.status==200
        data=trace.path.read_text(encoding='utf-8')
        assert 'extension.page.slow_button_found' in data
        assert 'nxm://private' not in data
        assert 'private-secret' not in data
        assert 'slow' in data
    finally:
        server.shutdown()
        server.server_close()


def test_autofree_batch_records_job_and_progress_without_urls(tmp_path):
    trace=TraceLog(tmp_path)
    batch=AutoFreeBatch(trace=trace)
    link='https://www.nexusmods.com/skyrimspecialedition/mods/197526?tab=files&file_id=829609'
    batch.start([link])
    job=batch.claim('edge-profile-id')
    assert job
    assert batch.progress('edge-profile-id',job['id'],'page')
    assert batch.progress('edge-profile-id',job['id'],'slow')
    records=[json.loads(line) for line in trace.path.read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in records]==[
        'batch.started','job.leased','job.progress','job.progress']
    assert 'https://' not in trace.path.read_text(encoding='utf-8')
    assert job['id'] in trace.path.read_text(encoding='utf-8')
