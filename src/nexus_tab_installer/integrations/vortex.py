"""Hands files to Vortex through its own command line.

`--install-archive` puts a file among the downloads of the ACTIVE game and starts installing it.
That is why the game matters, a Skyrim mod must not land in another game. And it must be active already:
on the real Vortex 2.0.2, `--game skyrimse --install-archive F` installed F into the previous game,
because the switch finishes seconds after the install has begun. So:

- Vortex is closed: its state can be read (`--get`), and it starts with `--game` alone. The file
  follows only when its log has said that this game became active.
- Vortex is open on this game, by its log: the file is installed right away.
- Vortex is open on another game, or the log says nothing: the file only goes into the downloads
  folder of its own game, and the person presses «Install» in Vortex.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from nexus_tab_installer.downloads.export import export_archive

# Where a Vortex game id differs from the domain of the game on Nexus.
GAME_IDS = {'skyrimspecialedition': 'skyrimse', 'newvegas': 'falloutnv'}
STARTUP_SECONDS = 120
SETTLE_SECONDS = 3
QUESTION_SECONDS = 45


class VortexError(ValueError):
    pass


_lock = threading.RLock()
_ACTIVATED = re.compile(r'gamemode activated "([^"]+)"')
LOG_TAIL = 4 * 1024 * 1024


def game_id(domain):
    return GAME_IDS.get(str(domain), str(domain))


def _candidate_folders(env):
    folders = []
    for name in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
        if env.get(name):
            folders.append(Path(env[name]) / 'Black Tree Gaming Ltd' / 'Vortex')
    if env.get('LOCALAPPDATA'):
        folders.append(Path(env['LOCALAPPDATA']) / 'Programs' / 'vortex')
    if os.name == 'nt':
        for letter in 'CDEFGHI':
            drive = Path(f'{letter}:/')
            if drive.exists():
                folders += [drive / 'Vortex', drive / 'Games' / 'Vortex', drive / 'Program Files' / 'Vortex']
    return folders


def _registry_folders():
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    key_name = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as uninstall:
                for index in range(min(winreg.QueryInfoKey(uninstall)[0], 400)):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as entry:
                            if str(winreg.QueryValueEx(entry, 'DisplayName')[0]).casefold().startswith('vortex'):
                                found.append(Path(str(winreg.QueryValueEx(entry, 'InstallLocation')[0]).strip('"')))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def find_vortex(env=None, folders=None):
    """Vortex.exe from the usual places. Only looks at a short list, never searches whole drives."""
    env = os.environ if env is None else env
    candidates = list(folders) if folders is not None else [*_registry_folders(), *_candidate_folders(env)]
    for folder in candidates:
        exe = Path(folder) / 'Vortex.exe'
        if exe.is_file():
            return exe
    return None


def _no_window():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def is_running():
    if os.name != 'nt':
        return False
    try:
        listed = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Vortex.exe', '/NH'], capture_output=True,
                                timeout=15, creationflags=_no_window()).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # Bytes: the rest of the answer is in the console code page, the name of the program is not.
    return b'vortex.exe' in listed.lower()


def read_state(exe, key, timeout=QUESTION_SECONDS):
    """A value from the state of a CLOSED Vortex, or None. Reading changes nothing in it."""
    try:
        done = subprocess.run([str(exe), '--get', key], capture_output=True, timeout=timeout, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    output = (done.stdout + done.stderr).decode('utf-8', 'replace')
    match = re.search(rf'^{re.escape(key)}(?:\.\S+)? = (.+?)\s*$', output, flags=re.M)
    if not match:
        return None
    try:
        return json.loads(match[1])
    except ValueError:
        return match[1]


def describe(exe):
    """What the settings screen tells about Vortex."""
    if is_running():
        return {'running': True, 'version': '', 'downloads': ''}
    return {'running': False, 'version': read_state(exe, 'app.appVersion') or '',
            'downloads': read_state(exe, 'settings.downloads.path') or ''}


def default_downloads(env=None):
    env = os.environ if env is None else env
    return Path(env['APPDATA']) / 'Vortex' / 'downloads' if env.get('APPDATA') else None


def downloads_of_game(root, game):
    """Vortex keeps one folder per game below its downloads folder."""
    root = Path(root)
    return root if root.name.casefold() == game.casefold() else root / game


def _launch(arguments):
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags)


def log_path(env=None):
    env = os.environ if env is None else env
    return Path(env['APPDATA']) / 'Vortex' / 'vortex.log' if env.get('APPDATA') else None


def _log_size(log):
    try:
        return log.stat().st_size if log else 0
    except OSError:
        return 0


def _activated_games(log, offset=0):
    """Games that became active, oldest first, in the part of the log after `offset`."""
    if not log:
        return []
    try:
        size = log.stat().st_size
        with log.open('rb') as stream:
            stream.seek(max(offset if offset <= size else 0, size - LOG_TAIL))
            return _ACTIVATED.findall(stream.read().decode('utf-8', 'replace'))
    except OSError:
        return []


def active_game(log=None):
    """The game an open Vortex says is active, or None if its log does not tell."""
    games = _activated_games(log or log_path())
    return games[-1] if games else None


def wait_for_game(game, offset=0, seconds=STARTUP_SECONDS, log=None):
    """True once the log says, after `offset`, that this game became active."""
    log = log or log_path()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        games = _activated_games(log, offset)
        if games and games[-1] == game:
            time.sleep(SETTLE_SECONDS)  # the rest of the switch is logged right after this line
            return True
        time.sleep(1)
    return False


def send(path, domain, exe, downloads_root=''):
    """Give the file to Vortex. Returns 'installing' or the folder it was left in for the person."""
    path, game = Path(path).resolve(), game_id(domain)
    with _lock:
        if not is_running():
            if read_state(exe, f'settings.gameMode.discovered.{game}.path') is None:
                raise VortexError(f'В Vortex не настроена игра «{game}». Откройте её в Vortex на вкладке «Игры» и повторите.')
            offset = _log_size(log_path())
            _launch([str(exe), '--game', game])
            ready = wait_for_game(game, offset)
        else:
            ready = active_game() == game
        if ready:
            _launch([str(exe), '--install-archive', str(path)])
            return 'installing'
    root = Path(downloads_root).expanduser() if str(downloads_root).strip() else default_downloads()
    if root is None:
        raise VortexError('Укажите папку загрузок Vortex в настройках.')
    folder = downloads_of_game(root, game)
    folder.mkdir(parents=True, exist_ok=True)
    return export_archive(path, folder, link=True).parent
