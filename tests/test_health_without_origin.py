"""The public, non-secret health check works even when a service worker omits Origin."""
from http.client import HTTPConnection
from queue import Queue
from threading import Thread
from nexus_tab_installer.browser.import_server import ImportServer,server_factory
from nexus_tab_installer.browser.pairing import PairingRequests


def test_health_get_without_origin_and_pairing_protection():
    server=ImportServer(('127.0.0.1',0),server_factory('private-token', Queue(), pairing=PairingRequests(token='private-token')))
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        def call(method,path,headers=None,body=None):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request(method,path,body,headers or {})
            r=conn.getresponse()
            status=r.status; data=r.read().decode();conn.close()
            return status,data
        status,data=call('GET','/health')
        assert status==200 and 'nexus-tab-installer' in data and 'private-token' not in data
        assert call('GET','/health',{'Origin':'https://random.example'})[0]==403
        assert call('POST','/pair/request',{'Content-Type':'application/json'},'{"browser":"edge"}')[0]==403
        assert call('GET','/pair/status?request_id=guess')[0] == 200
        import json
        assert json.loads(call('GET','/pair/status?request_id=guess')[1])['state'] == 'expired'
        assert call('GET','/auto/next')[0]==403
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)


def test_pair_status_after_user_approval_without_origin_header():
    import json
    from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
    token='test-secret'
    origin='chrome-extension://abcdefghijklmnopabcdefghijklmnop'
    pairing=PairingRequests(token=token)
    server=ImportServer(('127.0.0.1',0),server_factory(token,Queue(),TabSnapshots(),pairing))
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        rid=pairing.request('edge',origin)
        assert pairing.approve(rid)
        def read(origin_header=None):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'X-Nexus-Pairing':'1'}
            if origin_header:headers['Origin']=origin_header
            conn.request('GET','/pair/status?request_id='+rid,headers=headers)
            res=conn.getresponse();status=res.status;headers=dict(res.getheaders());data=json.loads(res.read());conn.close()
            return status,headers,data
        assert read('https://evil.test')[0] == 403
        status, headers, response=read()
        assert status==200 and response['token']==token
        assert 'Access-Control-Allow-Origin' not in headers
        assert read()[2]['state']=='expired'
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)
