"""Multi-window scanner regressions with deterministic Windows UIA fakes."""
import sys
import types
import pytest
from nexus_tab_installer.browser import windows_tabs as scanner


@pytest.mark.parametrize(('url', 'kind'), [
    ('https://www.nexusmods.com/', 'nexus-other'),
    ('https://www.nexusmods.com/skyrimspecialedition', 'nexus-other'),
    ('https://www.nexusmods.com/skyrimspecialedition/mods/196600', 'mod-page'),
    ('https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files&file_id=1', 'mod-files'),
    ('https://www.nexusmods.com/games/skyrimspecialedition/mods/196600?tab=files', 'mod-files'),
    ('https://www.nexusmods.com/games/fallout4/mods/123/download', 'mod-download'),
    ('https://www.nexusmods.com/games/skyrimspecialedition/mods/196600', 'mod-page'),
    ('https://www.nexusmods.com/games/skyrimspecialedition', 'nexus-other'),
    ('https://example.com/mods/123', 'other'),
])
def test_page_type_from_actual_url(url, kind):
    assert scanner.classify_nexus_page(url) == kind


class Tab:
    def __init__(self, win, index, title):
        self.win = win
        self.index = index
        self.title = title
        self.iface_selection_item = self
    def window_text(self):
        return self.title
    def is_visible(self):
        return True
    @property
    def CurrentIsSelected(self):
        return self.win.active == self.index
    def Select(self):
        self.win.active = self.index
        self.win.switches.append(self.index)


class Field:
    def __init__(self, win):
        self.win = win
    def get_value(self):
        return self.win.urls[self.win.active]


class Win:
    def __init__(self, handle, pid, urls, titles, active=0, expose_tabs=True):
        self.handle = handle
        self.element_info = types.SimpleNamespace(handle=handle, process_id=pid)
        self.urls = urls
        self.active = active
        self.tabs = [Tab(self, i, title) for i, title in enumerate(titles)] if expose_tabs else []
        self.switches = []
        self.focused = False
    def window_text(self):
        return 'Browser window'
    def is_visible(self):
        return True
    def descendants(self, control_type):
        if control_type == 'TabItem':
            return self.tabs
        if control_type == 'Edit':
            return [Field(self)]
        return []
    def set_focus(self):
        self.focused = True


def test_scans_all_browser_windows_not_only_first(monkeypatch):
    # Two Edge windows, Firefox, portable Opera GX (launcher.exe) and non-browser.
    # The UIA fallback intentionally returns ONLY the first window to reproduce
    # the exact old bug; EnumWindows must enumerate the others.
    windows = {
        101: Win(101, 1, ['https://nexusmods.com/',
                          'https://www.nexusmods.com/skyrimspecialedition/mods/196600'],
                 ['Nexus Mods', 'Whiterun Fix - Skyrim Nexus - Mods and Community']),
        102: Win(102, 2, ['https://www.nexusmods.com/fallout4/mods/123?tab=files',
                          'https://example.org/account'],
                 ['Fallout 4 Nexus Mods', 'Private email']),
        103: Win(103, 3, ['https://www.nexusmods.com/games/skyrimspecialedition/mods/196600'],
                 ['Nexus main mod page'], expose_tabs=False),
        104: Win(104, 4, ['https://www.nexusmods.com/cyberpunk2077/mods/999'],
                 ['Nexus Mods: Cyberpunk']),
        105: Win(105, 5, ['https://example.com'], ['Other site']),
    }
    class Process:
        def __init__(self, pid):
            self.pid = pid
        def name(self):
            return {1:'msedge.exe', 2:'msedge.exe', 3:'launcher.exe',
                    4:'firefox.exe', 5:'other.exe'}[self.pid]
        def exe(self):
            return r'D:\Apps\Opera GX\launcher.exe' if self.pid == 3 else ''
    class Desktop:
        def window(self, *, handle):
            return windows[handle]
        def windows(self):
            return [windows[101]]
    keys = []
    foreground = []
    native = types.SimpleNamespace(
        EnumWindows=lambda callback, _: [callback(hwnd, None) for hwnd in windows],
        IsWindowVisible=lambda hwnd: True,
        GetWindowThreadProcessId=lambda hwnd: (0, windows[hwnd].element_info.process_id),
        GetForegroundWindow=lambda: 42,
        SetForegroundWindow=foreground.append,
    )
    modules = {
        'pywinauto':types.SimpleNamespace(Desktop=lambda backend: Desktop(), keyboard=types.SimpleNamespace(send_keys=keys.append)),
        'psutil':types.SimpleNamespace(Process=Process),
        'pythoncom':types.SimpleNamespace(CoInitialize=lambda:None, CoUninitialize=lambda:None),
        'win32gui':native,
    }
    for name, obj in modules.items():
        monkeypatch.setitem(sys.modules, name, obj)
    monkeypatch.setattr(scanner.sys, 'platform', 'win32')
    monkeypatch.setattr(scanner.time, 'sleep', lambda _: None)
    result = scanner.scan_open_windows()
    assert result.windows_found == 4
    assert result.windows_scanned == 4
    assert len(result.mods) == 3  # duplicate Skyrim across two windows
    assert {m['mod_id'] for m in result.mods} == {196600, 123, 999}
    assert result.pages['nexus-other'] == 1
    assert result.pages['mod-files'] == 1
    assert result.pages['mod-page'] >= 2
    assert result.browsers == ['Edge', 'Opera GX / Opera', 'Firefox']
    assert windows[101].active == 0
    assert windows[102].active == 0
    assert windows[104].active == 0
    assert 1 not in windows[102].switches  # private background tab untouched
    assert foreground == [42]
    assert '^c' not in keys  # no clipboard reading


def test_scanner_title_detection_never_uses_random_background_tab():
    class Unrelated:
        def window_text(self):return 'Gmail inbox'
    assert not scanner._candidate(Unrelated())
    class Nexus:
        def window_text(self):return 'Whiterun - Skyrim Nexus - Mods and Community'
    assert scanner._candidate(Nexus())


def test_scan_diagnostics_never_saves_browsing_urls(tmp_path):
    result = scanner.TabScan(mods=[{'url':'https://www.nexusmods.com/fallout4/mods/123'}],
                             browsers=['Edge'], windows_found=2, windows_scanned=2,
                             pages={'mod-page':1},
                             window_details=[{'browser':'Edge', 'window': 123, 'checked':1,
                                              'mods':1,'skipped':0,'status':'ok'}])
    saved = scanner.save_scan_diagnostics(result, tmp_path/'data'/'logs'/'browser-scan-last.json')
    data = saved.read_text(encoding='utf-8')
    assert 'nexusmods.com' not in data
    assert 'window"' not in data
    assert '"windows_found": 2' in data
    assert '"mod_count": 1' in data


def test_extensions_read_every_window_via_tabs_query():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for browser in ['chromium','firefox']:
        source=(root/'extensions'/browser/'background.js').read_text(encoding='utf-8')
        assert 'api.tabs.query({})' in source
        assert 'currentWindow:true' not in source
