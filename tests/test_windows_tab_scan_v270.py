"""Deterministic UI Automation test using fake windows and controls.
A real Windows-browser integration test is still required before release claims.
"""
import sys
import types
from nexus_tab_installer.browser import windows_tabs as scanner


def test_url_filter_only_nexus_and_dedup():
    urls = [
        'https://example.com/payments',
        'https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files',
        'https://www.nexusmods.com/skyrimspecialedition/mods/196600',
        'https://www.nexusmods.com/fallout4/mods/123',
    ]
    mods = scanner.only_mod_pages(urls)
    assert [(m['game'], m['mod_id']) for m in mods] == [
        ('skyrimspecialedition', 196600), ('fallout4', 123)
    ]
    assert 'example.com' not in str(mods)


def test_unsupported_system_has_clear_warning(monkeypatch):
    monkeypatch.setattr(scanner.sys, 'platform', 'linux')
    result = scanner.scan_open_windows()
    assert result.mods == []
    assert 'Windows' in result.warnings[0]


def test_windows_scanner_restores_original_tab_and_never_reads_clipboard(monkeypatch):
    urls = [
        'https://example.com/private-page',
        'https://www.nexusmods.com/skyrimspecialedition/mods/196600',
        'https://www.nexusmods.com/fallout4/mods/123',
    ]
    class FakeTab:
        def __init__(self, window, index):
            self.window = window
            self.index = index
            self.iface_selection_item = self
        def window_text(self):
            return ['Private search', 'Skyrim Special Edition - Nexus Mods', 'Fallout 4 - Nexus Mods'][self.index]
        @property
        def CurrentIsSelected(self):
            return self.window.active == self.index
        def Select(self):
            self.window.active = self.index
        def is_visible(self):
            return True

    class Field:
        def __init__(self, win):
            self.win = win
        def get_value(self):
            return self.win.urls[self.win.active]

    class FakeWindow:
        def __init__(self):
            self.urls = urls
            self.active = 0
            self.element_info = types.SimpleNamespace(process_id=5)
            self.tabs = [FakeTab(self, i) for i in range(len(urls))]
        def is_visible(self):
            return True
        def descendants(self, control_type):
            if control_type == 'TabItem':
                return self.tabs
            if control_type == 'Edit':
                return [Field(self)]
            return []
        def set_focus(self):
            pass

    win = FakeWindow()
    def desktop(*, backend):
        assert backend == 'uia'
        return types.SimpleNamespace(windows=lambda: [win])
    keys = []
    pythoncom = types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
    pywinauto = types.SimpleNamespace(Desktop=desktop, keyboard=types.SimpleNamespace(send_keys=keys.append))
    psutil = types.SimpleNamespace(Process=lambda pid: types.SimpleNamespace(name=lambda: 'chrome.exe'))
    win32gui = types.SimpleNamespace(GetForegroundWindow=lambda: 42, SetForegroundWindow=lambda hwnd: None)
    monkeypatch.setattr(scanner.sys, 'platform', 'win32')
    for name, value in [('pythoncom', pythoncom), ('pywinauto', pywinauto), ('psutil', psutil), ('win32gui', win32gui)]:
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(scanner.time, 'sleep', lambda _: None)
    result = scanner.scan_open_windows()
    assert result.examined == 3  # also inspect the active page without switching
    assert result.skipped == 0
    assert win.active == 0
    assert len(result.mods) == 2
    assert {m['mod_id'] for m in result.mods} == {196600, 123}
    assert result.browsers == ['Chrome']
    assert keys.count('^l') <= 1  # only an active non-Nexus page may need address focus
    assert '^c' not in keys


def test_scanner_respects_cancel(monkeypatch):
    monkeypatch.setattr(scanner.sys, 'platform', 'linux')
    assert scanner.scan_open_windows(cancelled=lambda: True).mods == []
