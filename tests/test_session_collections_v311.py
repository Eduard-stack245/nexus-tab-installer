from pathlib import Path


def test_no_nested_ui_automation_when_extension_has_fresh_tabs():
    path = Path(__file__).resolve().parents[1]/'src/nexus_tab_installer/ui/flet_app.py'
    content=path.read_text('utf-8')
    # The fresh authenticated browser snapshot should be applied before the
    # fallback Windows worker is dispatched.
    assert content.index('if snapshots.sources(max_age=86400):') < content.index('background(discover_tabs)')
    assert 'snapshots.request_refresh()' in content


def test_tab_content_script_asks_for_active_job():
    root=Path(__file__).resolve().parents[1]
    for browser in ['chromium', 'firefox']:
        content=(root/f'extensions/{browser}/auto_free_content.js').read_text('utf-8')
        auto=(root/f'extensions/{browser}/auto_download.js').read_text('utf-8')
        assert "type: 'auto-free-ready'" in content
        assert "msg.type === 'auto-free-ready'" in auto
        assert 'tabId === st.tabId' in auto


def test_page_hang_diagnostic_does_not_issue_duplicate_job():
    from nexus_tab_installer.browser.auto_free import AutoFreeBatch
    clock = [100.0]
    batch = AutoFreeBatch(clock=lambda: clock[0])
    url='https://www.nexusmods.com/skyrimspecialedition/mods/197526?tab=files&file_id=829609'
    assert batch.start([url]) == 1
    item = batch.claim('edge-profile')
    assert item and batch.stalled_page(30) is False
    clock[0] += 31
    assert batch.stalled_page(30) is True
    assert batch.claim('other-profile') is None
    assert len(batch.state()['jobs']) == 1
