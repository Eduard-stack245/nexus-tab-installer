"""Regression checks for beginner onboarding and truthful browser connection status."""
from pathlib import Path
from unittest.mock import patch

from nexus_tab_installer.browser.browser_setup import BROWSERS, get_browser_setup
from nexus_tab_installer.platform.browser_setup import open_extension_manager

ROOT=Path(__file__).resolve().parents[1]


def test_all_four_browsers_have_independent_correct_guides():
    assert set(BROWSERS) == {'chrome','edge','opera-gx','firefox'}
    assert {x.extension_dir for x in BROWSERS.values()} == {'chromium','firefox'}
    for key, guide in BROWSERS.items():
        assert len(guide.steps)>=4
        assert (ROOT/'extensions'/guide.extension_dir/'manifest.json').exists()
        assert '://' in guide.management_url or guide.management_url.startswith('about:debugging')
        assert guide.key==key
    assert get_browser_setup('firefox').extension_dir=='firefox'


def test_open_extensions_only_in_selected_browser():
    called=[]
    with patch('nexus_tab_installer.platform.browser_setup.candidate_executables',return_value=['C:/Browser/msedge.exe']):
        open_extension_manager('edge',launcher=lambda cmd: called.append(cmd))
    assert called==[['C:/Browser/msedge.exe','edge://extensions']]


def test_missing_browser_reports_actionable_url():
    with patch('nexus_tab_installer.platform.browser_setup.candidate_executables',return_value=[]):
        try: open_extension_manager('opera-gx')
        except FileNotFoundError as e: assert 'opera://extensions' in str(e)
        else: assert False, 'Should give user an actionable fallback'


def test_guide_and_popup_are_included():
    assert (ROOT/'docs/BROWSER_SETUP_GUIDE.html').exists()
    for kind in ('firefox','chromium'):
        popup=(ROOT/'extensions'/kind/'popup.js').read_text(encoding='utf-8')
        bg=(ROOT/'extensions'/kind/'background.js').read_text(encoding='utf-8')
        assert "send('sync-tabs')" in popup and "setInterval" in popup
        assert "connection-status" in bg and "lastSyncError" in bg
        assert 'const all=await api.tabs.query({})' in bg or 'const all = await api.tabs.query({})' in bg
    app=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    for phrase in ('Подключение браузера — один раз', 'Показать папку', 'Проверить связь', 'Наглядная инструкция'):
        assert phrase in app


def test_browser_setup_refreshes_after_successful_snapshot():
    app=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    after=app.split("elif typ == 'browser_sync':",1)[1].split("elif typ ==",1)[0]
    assert 'update_browser_status()' in after
    assert 'check_browser_connection()' in after
