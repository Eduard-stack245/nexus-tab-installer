"""Regression: never confuse new tab in Edge with a new download window."""
from nexus_tab_installer.platform.browser_window import open_nexus_window


def test_edge_uses_new_window_argument():
    args = []
    ok, _ = open_nexus_window(
        'https://www.nexusmods.com/skyrimspecialedition/mods/197526?tab=files&file_id=829609',
        browser_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        launcher=lambda command: args.append(command), platform='win32'
    )
    assert ok and len(args) == 1
    assert args[0][1] == '--new-window'
    assert args[0][2].startswith('https://www.nexusmods.com/')


def test_firefox_uses_its_new_window_flag():
    args = []
    ok, _ = open_nexus_window('https://www.nexusmods.com/skyrimspecialedition/mods/197526',
        browser_path=r'D:\Apps\Firefox\firefox.exe', launcher=args.append, platform='win32')
    assert ok and args[0][1] == '-new-window'


def test_invalid_sites_are_rejected_before_launch():
    commands = []
    ok, _ = open_nexus_window('https://example.org/mods/12',
        browser_path='msedge.exe', launcher=commands.append, platform='win32')
    assert not ok and commands == []


def test_unknown_default_browser_is_not_reported_as_new_window():
    ok, message = open_nexus_window('https://www.nexusmods.com/skyrimspecialedition/mods/197526',
        browser_path=r'C:\something\unknown-browser.exe', platform='win32')
    assert not ok and 'браузер' in message.lower()


def test_no_old_open_new_fallback():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'src/nexus_tab_installer/ui/flet_app.py').read_text('utf-8')
    function = source.split('    def next_free(e=None):')[1].split('    def persist_config(', 1)[0]
    assert 'webbrowser.open_new(url)' not in function
    assert 'open_nexus_window(url)' in function
