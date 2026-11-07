"""Regression checks for the real extension-to-desktop automation handshake."""
import queue
import threading
from urllib.request import Request, urlopen
from pathlib import Path

from nexus_tab_installer.browser.auto_free import AutoFreeBatch
from nexus_tab_installer.browser.import_server import ImportServer, server_factory

ROOT=Path(__file__).resolve().parents[1]


def test_extension_only_counts_as_live_after_authenticated_poll():
    now=[20.0]
    engine=AutoFreeBatch(clock=lambda:now[0])
    assert not engine.has_live_helper()
    # A connected snapshot alone must not be treated as an active click-helper.
    assert engine.claim('profile-alpha') is None
    assert engine.has_live_helper()
    assert engine.live_helpers() == {'profile-alpha'}
    now[0]+=91
    assert not engine.has_live_helper()


def test_real_cors_preflight_supports_auto_source_header():
    server=ImportServer(('127.0.0.1',0),server_factory('t',queue.Queue(),auto_free=AutoFreeBatch()))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}/auto/next'
        req=Request(url,method='OPTIONS',headers={
            'Origin':'chrome-extension://unit-test',
            'Access-Control-Request-Method':'GET',
            'Access-Control-Request-Headers':'x-nexus-token, x-nexus-source'})
        with urlopen(req,timeout=3) as resp:
            assert resp.status==204
            assert 'X-Nexus-Source' in resp.headers['Access-Control-Allow-Headers']
            assert 'X-Nexus-Token' in resp.headers['Access-Control-Allow-Headers']
    finally:
        server.shutdown();server.server_close()


def test_auto_mode_does_not_falsely_start_manual_browser_fallback():
    text=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    block=text.split('def open_free(specs):',1)[1].split('def next_free(',1)[0]
    auto, manual = block.split('        else:\n            waiting_for_helper',1)
    # Auto mode queues the jobs; they wait until the extension asks for work.
    assert 'auto_free.start(specs)' in auto
    assert 'auto_free.has_live_helper()' in auto
    assert 'next_free()' not in auto
    # Manual mode opens the page for the user instead.
    assert 'next_free()' in manual


def test_browser_profiles_mixed_with_windows_fallback():
    ui=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    assert 'snapshots.request_refresh()' in ui
    assert "('windows_scanned', scan, waiting, snapshot_mods)" in ui
    # No short circuit on the first connected browser profile.
    section=ui.split('def discover_tabs():',1)[1].split('background(discover_tabs)',1)[0]
    assert 'extension_scanned' not in section
