"""Which mods MO2 and Vortex already have, so that nobody is told to get what is already there.

Only reads, and only small files and names. Being wrong here costs little: at worst the program
says that something is missing, or does not mention it, and the person decides anyway.
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path

from nexus_tab_installer.integrations.mo2_install import INSTANCE_GAMES, _plain_value, _read_ini, instance_info, paths_from_ini
from nexus_tab_installer.integrations.vortex import GAME_IDS, default_downloads

# Names Nexus gives its files: "SkyUI-12604-6-11-1778020881.zip" and "Mod Name 34705 0.5.25 2026-08-29T20-20Z Xx.zip".
_OLD_NAME = re.compile(r'-(\d{1,8})-[\w.-]*\.\w+$')
_NEW_NAME = re.compile(r'\s(\d{1,8})\s\S+\s\d{4}-\d{2}-\d{2}T')
_VORTEX_DOMAINS = {vortex: domain for domain, vortex in GAME_IDS.items()}


def mod_id_from_name(name):
    match = _NEW_NAME.search(name) or _OLD_NAME.search(name)
    return int(match[1]) if match else 0


def mo2_mods(ini):
    """{(game, mod_id): 'MO2'} for the mods of one MO2 instance."""
    if not ini:
        return {}
    try:
        game = instance_info(ini)['game']
        mods = paths_from_ini(ini)[0]
    except (OSError, ValueError, configparser.Error):
        return {}
    domains = INSTANCE_GAMES.get(re.sub(r'[^a-z0-9]', '', game.casefold()), ())
    found = {}
    for note in Path(mods).glob('*/meta.ini') if domains else ():
        try:
            mod_id = int(_plain_value(_read_ini(note)['General'].get('modid', '0')) or 0)
        except (OSError, ValueError, KeyError, configparser.Error):
            continue
        if mod_id > 0:
            found.update({(domain, mod_id): 'MO2' for domain in domains})
    return found


def vortex_mods(downloads_root=''):
    """{(game, mod_id): 'Vortex'} from the names of the files in its downloads folder."""
    root = Path(downloads_root).expanduser() if str(downloads_root).strip() else default_downloads()
    if root is None or not root.is_dir():
        return {}
    found = {}
    for game_folder in root.iterdir():
        if not game_folder.is_dir():
            continue
        domain = _VORTEX_DOMAINS.get(game_folder.name, game_folder.name)
        for path in game_folder.iterdir():
            mod_id = mod_id_from_name(path.name)
            if mod_id:
                found[(domain, mod_id)] = 'Vortex'
    return found


def installed_mods(ini='', vortex_root=''):
    return {**vortex_mods(vortex_root), **mo2_mods(ini)}
