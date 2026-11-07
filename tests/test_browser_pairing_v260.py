import http.client
import json
import queue
import threading
from http.server import ThreadingHTTPServer

from nexus_tab_installer.browser.pairing import PairingRequests, is_extension_origin
from nexus_tab_installer.browser.import_server import server_factory
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots

ORIGIN = 'chrome-extension://abcdefghijklmnoabcdefghijklmnoa'


def test_pairing_requires_explicit_approval_and_correct_origin():
    clock = [1.0]
    pairing = PairingRequests(token='private-secret', clock=lambda:clock[0])
    rid = pairing.request('chrome', ORIGIN)
    assert pairing.status(rid, ORIGIN) == {'state':'pending'}
    assert pairing.status(rid, 'moz-extension://different') == {'state':'expired'}
    assert pairing.approve(rid)
    assert pairing.status(rid, ORIGIN) == {'state':'approved', 'token':'private-secret'}
    assert pairing.status(rid, ORIGIN) == {'state':'expired'}
    assert not pairing.approve(rid)


def test_pairing_rejects_web_page_origin_and_ttl():
    clock=[0]
    pairing = PairingRequests(token='abc',clock=lambda:clock[0],ttl=10)
    assert not is_extension_origin('https://www.nexusmods.com')
    try:
        pairing.request('chrome','https://www.nexusmods.com')
    except ValueError:
        pass
    else:
        raise AssertionError('web page must not request pairing')
    rid=pairing.request('firefox','moz-extension://1234-abcd')
    clock[0]=11
    assert pairing.pending()==[]
    assert pairing.status(rid,'moz-extension://1234-abcd')=={'state':'expired'}


def test_pairing_http_end_to_end_and_snapshot_with_approved_token():
    inbox = queue.Queue()
    pairing = PairingRequests(token='approved-token')
    snapshots = TabSnapshots()
    server = ThreadingHTTPServer(('127.0.0.1',0),server_factory('approved-token',inbox,snapshots,pairing))
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        def call(method,path,payload=None,origin=ORIGIN,token=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Origin':origin,'Content-Type':'application/json'}
            if token: headers['X-Nexus-Token']=token
            conn.request(method,path,None if payload is None else json.dumps(payload),headers)
            res=conn.getresponse()
            code=res.status
            body=json.loads(res.read())
            conn.close()
            return code,body
        assert call('POST','/pair/request',{'browser':'chrome'},origin='http://evil.example')[0]==403
        status,data=call('POST','/pair/request',{'browser':'chrome'})
        assert status==200
        rid=data['request_id']
        assert inbox.get_nowait()==('pair_request',rid)
        assert call('GET','/pair/status?request_id='+rid)[1]['state']=='pending'
        assert call('GET','/pair/status?request_id='+rid,origin='https://evil.example')[0]==403
        assert pairing.approve(rid)
        authorized=call('GET','/pair/status?request_id='+rid)[1]
        assert authorized=={'state':'approved','token':'approved-token'}
        payload={'browser':'chrome','tabs':[{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/196600'}]}
        assert call('POST','/snapshot',payload,token=authorized['token'])[0]==200
        assert len(snapshots.collect())==1
    finally:
        server.shutdown()
        server.server_close()


def test_browser_extensions_support_automatic_pairing():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name in ('chromium','firefox'):
        background=(root/'extensions'/name/'background.js').read_text(encoding='utf8')
        assert '/pair/request' in background
        assert '/pair/status' in background
        assert 'authToken' in background
        assert '38471' in background
        assert 'nexusmods' in background
    view=(root/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf8')
    assert 'browser_pair_requests' in view
    assert 'pairing.approve(request_id)' in view
