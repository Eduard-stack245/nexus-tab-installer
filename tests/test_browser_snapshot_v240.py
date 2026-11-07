import http.client
import json
import queue
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
from nexus_tab_installer.browser.import_server import server_factory
from nexus_tab_installer.integrations.destinations import find_mo2_instances


def test_tab_snapshots_filter_dedupe_and_expire():
    clock = [0]
    snapshots = TabSnapshots(clock=lambda: clock[0])
    snapshots.update('edge', [
        {'url':'https://www.nexusmods.com/skyrimspecialedition/mods/196600', 'title':'Example'},
        {'url':'https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files'},
        {'url':'https://example.com/other', 'title':'Should never be sent'}])
    assert len(snapshots.collect()) == 1
    assert len(snapshots.sources()) == 1
    clock[0] = 601
    assert snapshots.collect() == []
    assert snapshots.sources() == {}


def test_import_server_snapshot_token_and_regular_import():
    incoming = queue.Queue()
    snapshots = TabSnapshots()
    server = ThreadingHTTPServer(('127.0.0.1', 0), server_factory('valid-code', incoming, snapshots))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        payload = json.dumps({'browser':'opera gx', 'tabs':[{
            'url':'https://www.nexusmods.com/skyrimspecialedition/mods/196600', 'title':'Test'}]})
        def post(path, token):
            con = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            con.request('POST', path, payload, {'Content-Type':'application/json','X-Nexus-Token':token})
            response = con.getresponse()
            status, body = response.status, json.loads(response.read())
            con.close()
            return status, body
        assert post('/snapshot', 'wrong')[0] == 403
        assert post('/snapshot', 'valid-code')[0] == 200
        assert len(snapshots.collect()) == 1
        assert incoming.get_nowait()[0] == 'browser_sync'
        assert post('/import', 'valid-code')[0] == 200
        assert isinstance(incoming.get_nowait(), list)
    finally:
        server.shutdown()
        server.server_close()


def test_find_portable_mo2_from_folder_or_executable():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)/'Mod Organizer 2'
        folder.mkdir()
        ini = folder/'ModOrganizer.ini'
        ini.write_text('[Settings]\n', encoding='utf-8')
        exe = folder/'ModOrganizer.exe'
        exe.write_bytes(b'test')
        assert find_mo2_instances([folder]) == [ini.resolve()]
        assert find_mo2_instances([exe]) == [ini.resolve()]


def test_one_click_flow_and_stable_browser_token_source():
    app = (Path(__file__).resolve().parents[1]/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    assert "def start_workflow(e=None)" in app
    assert "def scan_tabs(e=None)" in app
    assert "token = config.get('browser_token') or secrets.token_urlsafe(24)" in app
    assert "start_button.on_click = start_workflow" in app
    assert "change_tab('start')" in app


def test_browser_background_sync_and_manager_manual_selection():
    root = Path(__file__).resolve().parents[1]
    for browser in ('chromium','firefox'):
        script = (root/'extensions'/browser/'background.js').read_text(encoding='utf-8')
        assert "'/snapshot'" in (root/'src/nexus_tab_installer/browser/import_server.py').read_text(encoding='utf-8')
        assert "SERVER+'/snapshot'" in script and "127.0.0.1:38471" in script
        assert "nexusmods" in script
    app = (root/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    assert "Если MO2 портативный" in app
    assert "shadow=ft.BoxShadow" not in app.split('def mod_row(')[1].split('def retry_job(')[0]
