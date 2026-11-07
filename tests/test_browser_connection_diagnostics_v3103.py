"""Desktop reachability and prominently visible browser pairing diagnostics."""
import json
from http.client import HTTPConnection
from queue import Queue
from threading import Thread
from pathlib import Path
from nexus_tab_installer.browser.import_server import ImportServer, server_factory
from nexus_tab_installer.browser.pairing import PairingRequests
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')


def test_desktop_health_is_public_but_does_not_disclose_token():
    token='very-private-token-for-test'
    server=ImportServer(('127.0.0.1',0),server_factory(token,Queue(),TabSnapshots(),PairingRequests(token=token)))
    worker=Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        conn.request('GET','/health',headers={'Origin':'chrome-extension://nexus-test'})
        resp=conn.getresponse(); text=resp.read().decode()
        assert resp.status==200
        result=json.loads(text)
        assert result=={'ok':True,'service':'nexus-tab-installer','protocol':1,'language':'ru'}
        assert token not in text
        assert resp.getheader('Access-Control-Allow-Origin')=='chrome-extension://nexus-test'
        conn.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_pairing_banner_always_visible_in_root_layout():
    assert "pairing_banner.visible = bool(requests)" in UI
    assert "                pairing_banner,\n                attention_banner,\n                main_area," in UI
    assert "server_notice.visible = True" in UI
    assert "pairing_banner_content.controls.append(request_actions(request))" in UI


def test_extensions_check_live_service_and_update_version():
    for name in ('chromium','firefox'):
        bg=(ROOT/f'extensions/{name}/background.js').read_text(encoding='utf-8')
        manifest=json.loads((ROOT/f'extensions/{name}/manifest.json').read_text())
        assert "fetch(SERVER+'/health'" in bg
        assert "checkDesktopHealth()" in bg
        assert manifest['version']=='1.0.0'
        assert 'downloads' in manifest['permissions']
