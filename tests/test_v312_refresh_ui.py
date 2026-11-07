from pathlib import Path
import http.client
import json
import queue
import threading
from http.server import ThreadingHTTPServer

from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
from nexus_tab_installer.browser.import_server import server_factory
from nexus_tab_installer.browser.auto_free import AutoFreeBatch

ROOT = Path(__file__).resolve().parents[1]

def test_refresh_protocol_waits_for_new_snapshot():
    snap = TabSnapshots()
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/197526'}])
    revision = snap.request_refresh()
    assert not snap.refresh_acknowledged(revision)
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/197528'}])
    assert snap.refresh_acknowledged(revision)
    assert {x['mod_id'] for x in snap.collect()} == {197528}


def test_job_channel_requests_fresh_tab_scan_with_auth():
    snap = TabSnapshots()
    batch = AutoFreeBatch()
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server_factory('secret', queue.Queue(), snap, None, batch))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        scan_rev = snap.request_refresh()
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=3)
        conn.request('GET','/auto/next',headers={'X-Nexus-Token':'secret','X-Nexus-Source':'a'*32})
        reply = conn.getresponse()
        data = json.loads(reply.read())
        assert reply.status == 200 and data['scan_marker'].endswith(':'+str(scan_rev))
        conn.close()
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=3)
        conn.request('GET','/auto/next',headers={'X-Nexus-Token':'incorrect'})
        reply = conn.getresponse()
        assert reply.status == 403
        reply.read(); conn.close()
    finally:
        srv.shutdown(); srv.server_close()


def test_silent_extension_gets_a_fresh_request():
    app = (ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text('utf8')
    assert "('extension_timeout', waiting, revision, snapshots.refresh_progress(revision))" in app
    assert "snapshots.request_refresh()" in app


def test_all_extensions_support_scans_on_job_poll():
    for browser in ('chromium', 'firefox'):
        js = (ROOT/f'extensions/{browser}/auto_download.js').read_text('utf8')
        assert 'payload.scan_marker' in js
        assert 'syncTabs()' in js


def test_two_profiles_must_both_acknowledge_a_scan():
    snap = TabSnapshots()
    a,b='a'*32, 'b'*32
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/111'}], source_id=a)
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/222'}], source_id=b)
    rev=snap.request_refresh()
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/333'}], source_id=a)
    assert snap.refresh_progress(rev)==(1,2)
    assert not snap.refresh_acknowledged(rev)
    snap.update('edge', [{'url':'https://www.nexusmods.com/skyrimspecialedition/mods/444'}], source_id=b)
    assert snap.refresh_acknowledged(rev)
    assert {x['mod_id'] for x in snap.collect()} == {333,444}
    assert {x['mod_id'] for x in snap.collect_refreshed(rev)} == {333,444}


def test_refresh_marker_changes_for_a_new_app_instance():
    a=TabSnapshots()
    b=TabSnapshots()
    a.request_refresh(); b.request_refresh()
    assert a.refresh_marker()!=b.refresh_marker()
