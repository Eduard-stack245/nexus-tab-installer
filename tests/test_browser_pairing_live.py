"""Real local HTTP handshake: extension origin -> desktop approval -> snapshot."""
import json
from http.client import HTTPConnection
from queue import Queue
from threading import Thread

from nexus_tab_installer.browser.import_server import ImportServer, server_factory
from nexus_tab_installer.browser.pairing import PairingRequests
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots


def test_extension_one_time_authorization_over_local_http():
    token = 'safely-generated-token-for-tests'
    origin = 'chrome-extension://opnaiifglhcghnfelogjbneomgalpkbp'
    pairing = PairingRequests(token=token)
    received = Queue()
    snapshots = TabSnapshots()
    server = ImportServer(('127.0.0.1', 0), server_factory(token, received, snapshots, pairing))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def request(method, url, data=None, headers=None):
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            params = {'Origin': origin, **(headers or {})}
            if data is not None:
                params['Content-Type'] = 'application/json'
            conn.request(method, url, None if data is None else json.dumps(data), params)
            response = conn.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            conn.close()
            return result

        code, headers, _ = request('OPTIONS', '/pair/request', headers={
            'Access-Control-Request-Method':'POST',
            'Access-Control-Request-Headers':'content-type'})
        assert code == 204
        assert headers['Access-Control-Allow-Origin'] == origin

        code, _, body = request('POST', '/pair/request', {'browser':'edge'})
        assert code == 200
        rid = json.loads(body)['request_id']
        assert received.get_nowait() == ('pair_request', rid)
        code, _, body = request('GET', '/pair/status?request_id='+rid)
        assert json.loads(body)['state'] == 'pending'
        assert pairing.approve(rid)
        code, _, body = request('GET', '/pair/status?request_id='+rid)
        assert json.loads(body)['token'] == token

        code, _, body = request('POST', '/snapshot', {'browser':'edge',
            'source_id': 'a'*32,
            'tabs':[{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/197526',
                     'title':'Mod'},{'url':'https://google.com/private', 'title':'Private'}]},
            {'X-Nexus-Token':token})
        assert code == 200
        assert json.loads(body)['accepted'] == 1
        assert snapshots.summary() == {'edge':1}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
