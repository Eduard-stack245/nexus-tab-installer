"""Windows UI Automation fallback for Nexus mod pages in *all* browser windows.

Windows UI Automation cannot reliably disclose URLs for arbitrary background tabs.
We enumerate all top-level browser windows, inspect each active tab and inactive
accessible tabs whose title explicitly signals Nexus, and restore the user's
original tab/window. The optional WebExtension path is authoritative for every
background URL, with no tab switching.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
import time
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from nexus_tab_installer.core.links import normalize_entries, parse_mod

BROWSERS = {
    'chrome.exe': 'Chrome', 'msedge.exe': 'Edge',
    'opera.exe': 'Opera GX / Opera', 'launcher.exe': 'Opera GX / Opera',
    'firefox.exe': 'Firefox',
}
MAX_CANDIDATES_PER_WINDOW = 100


@dataclass
class TabScan:
    mods: list[dict] = field(default_factory=list)
    browsers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    examined: int = 0
    skipped: int = 0
    windows_found: int = 0
    windows_scanned: int = 0
    pages: dict[str, int] = field(default_factory=dict)
    window_details: list[dict] = field(default_factory=list)


def save_scan_diagnostics(scan: TabScan, path):
    """Save only counters to portable data/logs, NEVER browsing URLs/titles."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'browsers': scan.browsers,
        'windows_found': scan.windows_found,
        'windows_scanned': scan.windows_scanned,
        'tabs_examined': scan.examined,
        'tabs_skipped': scan.skipped,
        'mod_count': len(scan.mods),
        'page_types': scan.pages,
        'windows': [
            {key: row[key] for key in ('browser', 'checked', 'mods', 'skipped', 'status') if key in row}
            for row in scan.window_details
        ],
        'warnings': scan.warnings,
    }
    tmp = dest.with_suffix(dest.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(dest)
    return dest


def only_mod_pages(urls):
    return normalize_entries([{'url': url} for url in urls if parse_mod(url)])


def classify_nexus_page(url):
    """Classify by the actual URL, not the tab title or visible site content."""
    try:
        parts = urlsplit(str(url).strip())
        if parts.scheme != 'https' or (parts.hostname or '').lower() not in ('nexusmods.com', 'www.nexusmods.com'):
            return 'other'
        mod = parse_mod(url)
        if not mod:
            return 'nexus-other'
        segments = parts.path.strip('/').split('/')
        extra = segments[4:] if segments[0] == 'games' else segments[3:]
        query = parse_qs(parts.query)
        if 'download' in extra or query.get('tab', [''])[0].lower() == 'download':
            return 'mod-download'
        if 'files' in extra or query.get('tab', [''])[0].lower() == 'files' or 'file_id' in query:
            return 'mod-files'
        return 'mod-page'
    except (ValueError, AttributeError, TypeError):
        return 'other'


def _active(tab):
    try:
        return bool(tab.iface_selection_item.CurrentIsSelected)
    except Exception:
        try:
            return bool(tab.is_selected())
        except Exception:
            return False


def _tab_title(tab):
    try:
        title = tab.window_text()
        if title:
            return str(title)
    except Exception:
        pass
    try:
        return str(tab.element_info.name or '')
    except Exception:
        return ''


def _candidate(tab):
    """Don't focus unrelated background tabs just to discover their URL."""
    title = _tab_title(tab).lower()
    return any(tag in title for tag in (
        'nexusmods.com', 'nexus mods', 'nexus - mods', 'nexus – mods',
        'nexus -', 'nexus –', 'nexusmods',
    ))


def _select(tab):
    try:
        tab.iface_selection_item.Select()
    except Exception:
        tab.click_input()


def _read_browser_address(win, keyboard):
    """Read only Nexus URLs; never touch clipboard or browser page contents."""
    def read():
        try:
            fields = win.descendants(control_type='Edit')
        except Exception:
            return None
        for field in fields:
            values = []
            try:
                values.append(field.get_value())
            except Exception:
                pass
            try:
                values.append(field.iface_value.CurrentValue)
            except Exception:
                pass
            for raw in values:
                val = str(raw or '').strip()
                if val.startswith(('www.nexusmods.com/', 'nexusmods.com/')):
                    val = 'https://' + val
                if classify_nexus_page(val) != 'other':
                    return val
        return None

    url = read()
    if url:
        return url
    # The address edit isn't always exposed until focused. Do not use Ctrl+C.
    keyboard.send_keys('^l')
    try:
        time.sleep(.08)
        return read()
    finally:
        keyboard.send_keys('{ESC}')


def _browser_name(proc, name, window_title=''):
    if name not in BROWSERS:
        return None
    if name != 'launcher.exe':
        return BROWSERS[name]
    # Opera GX uses launcher.exe, but other programs do too. Check the actual
    # process path first, which remains valid when the window title is a page.
    try:
        if 'opera' in str(proc.exe()).lower():
            return BROWSERS[name]
    except Exception:
        pass
    if 'opera' in str(window_title).lower():
        return BROWSERS[name]
    return None


def _discover_windows(desktop, win32gui):
    """Enumerate native top-level HWNDs; UIA Desktop.windows may omit siblings."""
    found, seen = [], set()
    enum = getattr(win32gui, 'EnumWindows', None)
    if callable(enum):
        handles = []
        try:
            enum(lambda hwnd, _: handles.append(hwnd), None)
        except Exception:
            handles = []
        for hwnd in handles:
            if hwnd in seen:
                continue
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    continue
                _, pid = win32gui.GetWindowThreadProcessId(hwnd)
                win = desktop.window(handle=hwnd)
                # A handle still must be matched to a real window wrapper.
                if not win or not win.is_visible():
                    continue
                found.append((win, pid, hwnd))
                seen.add(hwnd)
            except Exception:
                continue
    # Some accessibility backends expose browser windows not represented in
    # native enumeration. Merge, rather than using only the first discovery.
    try:
        for win in desktop.windows():
            try:
                hwnd = getattr(win, 'handle', None) or getattr(win.element_info, 'handle', None)
                if hwnd and hwnd in seen:
                    continue
                pid = win.element_info.process_id
                if not win.is_visible():
                    continue
                found.append((win, pid, hwnd))
                if hwnd:
                    seen.add(hwnd)
            except Exception:
                continue
    except Exception:
        pass
    return found


def scan_open_windows(*, cancelled: Callable[[], bool] = lambda: False, skip_browsers=None) -> TabScan:
    result = TabScan()
    skip_browsers = {str(name).lower() for name in (skip_browsers or ())}
    if sys.platform != 'win32':
        result.warnings.append('Сканирование через Windows UIA доступно только в Windows.')
        return result
    try:
        import psutil
        import pythoncom
        from pywinauto import Desktop, keyboard
        import win32gui
    except ImportError:
        result.warnings.append('Не установлен pywinauto или pywin32; проверьте зависимости.')
        return result

    pythoncom.CoInitialize()
    prior_window = None
    try:
        prior_window = win32gui.GetForegroundWindow()
        try:
            all_windows = _discover_windows(Desktop(backend='uia'), win32gui)
        except Exception as exc:
            result.warnings.append(f'Не удалось перечислить окна Windows: {type(exc).__name__}')
            return result

        for win, pid, hwnd in all_windows:
            if cancelled():
                result.warnings.append('Сканирование отменено пользователем.')
                break
            try:
                proc = psutil.Process(pid)
                name = proc.name().lower()
                try:
                    title = win.window_text()
                except Exception:
                    title = ''
                browser = _browser_name(proc, name, title)
                if not browser:
                    continue
                if browser.lower().split(' / ')[0] in skip_browsers or browser.lower() in skip_browsers:
                    continue
            except Exception:
                continue

            result.windows_found += 1
            if browser not in result.browsers:
                result.browsers.append(browser)
            detail = {'browser': browser, 'window': int(hwnd or 0), 'checked': 0,
                      'mods': 0, 'skipped': 0, 'status': 'ok'}
            result.window_details.append(detail)
            try:
                tabs = [tab for tab in win.descendants(control_type='TabItem') if tab.is_visible()]
            except Exception:
                tabs = []
            # On a browser without accessible tab items, still read the *active*
            # page of this window. Never skip the entire second window.
            selected = next((tab for tab in tabs if _active(tab)), None)
            candidates = [tab for tab in tabs if tab is selected or _candidate(tab)]
            detail['skipped'] = max(len(tabs) - len(candidates), 0)
            result.skipped += detail['skipped']
            result.windows_scanned += 1
            try:
                win.set_focus()
                if not tabs:
                    detail['status'] = 'active-only'
                    if cancelled():
                        continue
                    result.examined += 1
                    detail['checked'] += 1
                    url = _read_browser_address(win, keyboard)
                    kind = classify_nexus_page(url)
                    if kind != 'other':
                        result.pages[kind] = result.pages.get(kind, 0) + 1
                    if parse_mod(url or ''):
                        # Only store titles for an actual mod URL; never include
                        # unrelated browsing information in the collection.
                        title = str(win.window_text() or '').strip()[:160]
                        result.mods.append({'url': url, 'title': title})
                        detail['mods'] += 1
                    continue

                for tab in candidates[:MAX_CANDIDATES_PER_WINDOW]:
                    if cancelled():
                        break
                    try:
                        if tab is not selected:
                            _select(tab)
                            time.sleep(.14)
                        result.examined += 1
                        detail['checked'] += 1
                        url = _read_browser_address(win, keyboard)
                        kind = classify_nexus_page(url)
                        if kind != 'other':
                            result.pages[kind] = result.pages.get(kind, 0) + 1
                        if parse_mod(url or ''):
                            result.mods.append({'url': url, 'title': _tab_title(tab)[:160]})
                            detail['mods'] += 1
                    except Exception:
                        continue
            except Exception as exc:
                detail['status'] = f'failed:{type(exc).__name__}'
            finally:
                if selected is not None:
                    try:
                        if not _active(selected):
                            _select(selected)
                    except Exception:
                        pass
                try:
                    keyboard.send_keys('{ESC}')
                except Exception:
                    pass

        result.mods = normalize_entries(result.mods)
        if not result.browsers:
            result.warnings.append('Откройте браузер с вкладками модов: окна Chrome, Edge, Opera GX или Firefox не найдены.')
        if result.skipped:
            result.warnings.append(
                f'Фоновых вкладок без признаков Nexus пропущено: {result.skipped}. '
                'Windows не гарантирует доступ к URL всех фоновых вкладок; '
                'полный охват без переключений возможен через WebExtension.'
            )
        if not result.mods:
            result.warnings.append('На доступных вкладках не найдено URL конкретных модов Nexus Mods.')
        return result
    finally:
        if prior_window:
            try:
                win32gui.SetForegroundWindow(prior_window)
            except Exception:
                pass
        pythoncom.CoUninitialize()
