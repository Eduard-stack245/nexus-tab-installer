"""Open extension settings in the *selected* browser, never a random default.

The browser itself remains responsible for installation and user consent.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

from nexus_tab_installer.browser.browser_setup import get_browser_setup


_EXECUTABLES = {
    'edge': ('msedge.exe', 'msedge'),
    'chrome': ('chrome.exe', 'chrome'),
    'opera-gx': ('opera.exe', 'opera'),
    'firefox': ('firefox.exe', 'firefox'),
}


def candidate_executables(key, env=None):
    """Return candidate absolute paths or PATH executables for one browser."""
    environment = os.environ if env is None else env
    candidates = []
    for alias in _EXECUTABLES[key]:
        resolved = shutil.which(alias)
        if resolved:
            candidates.append(resolved)
    if sys.platform == 'win32':
        roots = [environment.get('PROGRAMFILES'), environment.get('PROGRAMFILES(X86)'),
                 environment.get('LOCALAPPDATA')]
        rel = {
            'edge': ('Microsoft/Edge/Application/msedge.exe',),
            'chrome': ('Google/Chrome/Application/chrome.exe',),
            'opera-gx': ('Programs/Opera GX/launcher.exe', 'Programs/Opera GX/opera.exe'),
            'firefox': ('Mozilla Firefox/firefox.exe', 'Programs/Mozilla Firefox/firefox.exe'),
        }
        for r in roots:
            if r:
                for relative in rel[key]:
                    candidate = Path(r) / relative
                    if candidate.is_file():
                        candidates.append(str(candidate))
    return list(dict.fromkeys(candidates))


def open_extension_manager(key, *, launcher=subprocess.Popen):
    setup = get_browser_setup(key)
    found = candidate_executables(key)
    if not found:
        raise FileNotFoundError(
            f'{setup.name} не найден автоматически. Откройте {setup.management_url} в этом браузере вручную.')
    # Launch the selected browser, not the system default.
    return launcher([found[0], setup.management_url])
