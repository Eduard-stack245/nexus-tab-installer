"""Open a selected Nexus download in a separate Windows browser window.

`webbrowser.open_new()` is not reliable: Edge/Chrome often open a tab in the
existing research window. This adapter chooses the registered default browser
and requests a new *normal window* via its supported command-line argument.
If the executable cannot be resolved, it reports failure rather than claiming
that a window was created. The WebExtension uses windows.create() separately.
"""
from __future__ import annotations

import os
import ntpath
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

# Do not pass arbitrary command-line URLs supplied by the user.
_SUPPORTED = {'msedge.exe', 'chrome.exe', 'opera.exe', 'firefox.exe', 'brave.exe',
              'vivaldi.exe', 'chromium.exe', 'launcher.exe'}


def _windows_default_browser():
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        # Windows default handler for https, including portable browsers if
        # registered with Windows as the default handler.
        assoc = ctypes.windll.shlwapi.AssocQueryStringW
        assoc.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
                          wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        assoc.restype = ctypes.c_long  # HRESULT is a 32-bit signed Windows LONG
        chars = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(chars.value)
        if assoc(0, 2, 'https', None, buf, ctypes.byref(chars)) == 0:
            path = Path(buf.value)
            if path.is_file() and path.name.lower() in _SUPPORTED:
                return str(path)
    except (OSError, ValueError, AttributeError):
        pass
    # The default-handler lookup is not always present on managed Windows PCs.
    # Check well-known App Paths (no arbitrary scanning of program directories).
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for exe in ('msedge.exe', 'chrome.exe', 'firefox.exe', 'opera.exe'):
                try:
                    with winreg.OpenKey(hive, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}') as key:
                        candidate = Path(os.path.expandvars(winreg.QueryValueEx(key, '')[0].strip('"')))
                        if candidate.is_file():
                            return str(candidate)
                except OSError:
                    continue
    except ImportError:
        pass
    return None


def open_nexus_window(url: str, *, browser_path=None, launcher=None, platform=None):
    """Open URL in a distinct browser window. Return (success, reason).

    Accepts only Nexus HTTPS URLs and an explicitly resolved, recognized
    browser executable. `launcher`/`browser_path` injection is for tests.
    """
    parsed = urlsplit(str(url))
    if parsed.scheme != 'https' or parsed.hostname not in ('nexusmods.com', 'www.nexusmods.com'):
        return False, 'Нельзя открыть неподтверждённую ссылку: требуется HTTPS Nexus Mods.'
    platform = sys.platform if platform is None else platform
    if platform != 'win32':
        return False, 'Отдельное окно без расширения поддерживается пока только в Windows.'
    executable = browser_path or _windows_default_browser()
    if not executable or ntpath.basename(str(executable)).lower() not in _SUPPORTED:
        return False, 'Windows не сообщил путь к браузеру. Подключите расширение либо выберите браузер по умолчанию.'
    flag = '-new-window' if ntpath.basename(str(executable)).lower() == 'firefox.exe' else '--new-window'
    try:
        (launcher or subprocess.Popen)([str(executable), flag, url])
    except OSError as exc:
        return False, f'Не удалось открыть отдельное окно браузера: {exc}'
    return True, 'Страница загрузки открыта в отдельном окне браузера.'
