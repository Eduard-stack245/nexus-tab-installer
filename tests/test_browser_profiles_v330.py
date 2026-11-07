"""End-to-end regression: browser windows and profiles must not overwrite each other."""
import http.client
import json
import queue
import threading
from http.server import ThreadingHTTPServer

import pytest
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
from nexus_tab_installer.browser.import_server import server_factory


def mod(number):
    return {'url':f'https://www.nexusmods.com/skyrimspecialedition/mods/{number}', 'title':f'Mod {number}'}


def test_two_edge_profiles_do_not_overwrite_or_duplicate():
    now = [10]
    snaps = TabSnapshots(clock=lambda: now[0])
    a, b = 'a'*32, 'b'*32
    assert snaps.update('edge', [mod(197516), mod(197526), {'url':'https://www.nexusmods.com/'}], source_id=a) == 2
    assert snaps.update('edge', [mod(197526), mod(196600)], source_id=b) == 2
    assert {e['mod_id'] for e in snaps.collect()} == {197516, 197526, 196600}
    assert snaps.summary() == {'edge':4}
    assert snaps.profile_counts() == {'edge':2}
    snaps.update('edge', [mod(197516)], source_id=a)
    assert {e['mod_id'] for e in snaps.collect()} == {197516, 197526, 196600}
    assert len(snaps.sources()) == 2
    now[0] = 611
    assert snaps.collect() == []
    assert snaps.sources() == {}


def test_invalid_profile_ids_are_rejected():
    snaps = TabSnapshots()
    for source_id in ('../../foo', 'not-a-uuid', 'A'*32, 'x'*100):
        with pytest.raises(ValueError):
            snaps.update('chrome', [mod(196600)], source_id=source_id)


def test_http_snapshot_distinguishes_firefox_profiles_and_respects_auth():
    inbox=queue.Queue()
    snaps=TabSnapshots()
    server=ThreadingHTTPServer(('127.0.0.1', 0),server_factory('secret',inbox,snaps))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def send(token, source, mods):
        con=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        body=json.dumps({'browser':'firefox','source_id':source,'tabs':mods})
        con.request('POST','/snapshot',body,{'Content-Type':'application/json','X-Nexus-Token':token})
        reply=con.getresponse()
        status=reply.status
        reply.read()
        con.close()
        return status
    try:
        assert send('secret','1'*32,[mod(10),mod(11)]) == 200
        assert send('wrong','2'*32,[mod(12)]) == 403
        assert send('secret','2'*32,[mod(12)]) == 200
        assert {x['mod_id'] for x in snaps.collect()} == {10,11,12}
        assert len(snaps.sources()) == 2
        assert send('secret','foo',[mod(13)]) == 400
    finally:
        server.shutdown()
        server.server_close()


def test_browser_background_uses_per_profile_source_id_without_tab_activation():
    from pathlib import Path
    project=Path(__file__).resolve().parents[1]
    for browser in ('chromium','firefox'):
        js=(project/'extensions'/browser/'background.js').read_text(encoding='utf8')
        assert 'source_id:await getSourceId()' in js
        assert "api.storage.local.set({sourceId:id})" in js
        assert 'api.tabs.query({})' in js
        assert 'api.tabs.update(' not in js
        assert 'chrome.debugger' not in js
