from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
VIEWS = (ROOT/'src/nexus_tab_installer/ui/views.py').read_text(encoding='utf-8')


def test_file_catalog_names_are_registered_with_native_browser_watcher():
    assert "browser_download_watcher.register(spec['game'], spec['mod_id'], spec['file_id']," in APP
    assert "('browser_archive', game, mid, fid, path)" in APP
    assert 'free_queue.remove_file(game, mid, fid)' in APP
    assert "'task_delivered'" in APP


def test_browser_download_folder_configured_and_saved():
    assert 'browser_download_folder.value or' in APP
    assert 'browser_download_folder=browser_download_folder' in APP
    assert 'browser_download_folder' in VIEWS


def test_isolated_browser_windows_for_assistant():
    for folder in ('chromium', 'firefox'):
        js = (ROOT/'extensions'/folder/'auto_download.js').read_text(encoding='utf-8')
        assert 'api.windows.create' in js
        assert 'autoIdleWindow' in js
        assert "'/auto/status?job_id='" in js
        # Downloads started by the site are intercepted, never re-requested.
        assert 'api.downloads.onCreated.addListener' in js
        assert 'discardBrowserCopy(item.id)' in js


def test_extension_builds_share_identical_automation_scripts():
    for name in ('auto_download.js', 'auto_free_content.js'):
        chromium = (ROOT/'extensions/chromium'/name).read_text(encoding='utf-8')
        firefox = (ROOT/'extensions/firefox'/name).read_text(encoding='utf-8')
        assert chromium == firefox, name
    assert "importScripts('auto_download.js')" in (ROOT/'extensions/chromium/background.js').read_text(encoding='utf-8')
    import json
    manifest = json.loads((ROOT/'extensions/firefox/manifest.json').read_text(encoding='utf-8'))
    assert manifest['background']['scripts'] == ['background.js', 'auto_download.js']


def test_content_script_searches_shadow_dom_and_respects_site_flow():
    js = (ROOT/'extensions/chromium/auto_free_content.js').read_text(encoding='utf-8')
    # Nexus renders the download panel inside <mod-file-download>'s shadow root.
    assert 'el.shadowRoot' in js and "deepQueryAll('mod-file-download')" in js
    assert '/^slow download$/i' in js and '/^start download$/i' in js
    assert '/^standard download$/i' in js
    assert 'your download will start in' in js
    # The site's own link after the countdown backs up the download interception.
    assert '/^start download manually$/i' in js
    assert "type: 'auto-free-link'" in js
    auto = (ROOT/'extensions/chromium/auto_download.js').read_text(encoding='utf-8')
    assert "msg.type === 'auto-free-link'" in auto
    # CDN directories are hex bytes (.../d7/97/69/...): digits there must not be
    # read as a game or mod ID. This wrongly rejected a real file.
    assert 'cdn[2]' not in auto and 'function rejectDownload' in auto
    assert 'st.ignored' in auto
    # Nexus keeps <style> blocks inside the shadow root; they are not page text.
    assert "child.tagName !== 'STYLE'" in js
    # No CAPTCHA solving: verification is shown to the user.
    assert "step('verify')" in js
    assert 'captcha' not in js.split('function challengePresent')[0].lower()
