"""The public GraphQL API of Nexus: what is known about many mods, in one request and without a key.

Names, versions, dates, sizes and requirements of a whole list cost one request, which is much
lighter on the site than asking for every mod. Nothing about the person is sent, only numbers of
mods. All of this is a convenience: when Nexus does not answer, downloads work as before.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nexus_tab_installer.nexus.api import APP_VERSION, NexusError

URL = 'https://api.nexusmods.com/v2/graphql'
HEADERS = {'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': f'NexusTabInstaller/{APP_VERSION}',
           'Application-Name': 'NexusTabInstaller', 'Application-Version': APP_VERSION}
CHUNK = 50  # mods in one request
MIN_GAP = 1.0  # seconds between two requests of this module
TIMEOUT = 30

_MODS = '''query($ids: [CompositeDomainWithIdInput!]!, $count: Int) {
  legacyModsByDomain(ids: $ids, count: $count) {
    nodes {
      modId name version updatedAt fileSize status
      game { id domainName name }
      modRequirements {
        nexusRequirements { nodes { modId gameId modName notes url externalRequirement } }
        dlcRequirements { gameExpansion { name } notes }
      }
    }
  }
}'''
_FILES = '''query($modId: ID!, $gameId: ID!) {
  modFiles(modId: $modId, gameId: $gameId) { fileId name version date category sizeInBytes }
}'''

_turn_lock = threading.Lock()
_next_turn = [0.0]


@dataclass
class Requirement:
    name: str
    note: str = ''
    mod_id: int = 0
    game: str = ''  # the domain of its game on Nexus; empty for what lies outside Nexus
    url: str = ''
    external: bool = False
    dlc: bool = False


@dataclass
class ModInfo:
    game: str
    mod_id: int
    name: str = ''
    version: str = ''
    updated_at: float = 0.0
    size_kb: int = 0  # of the main file
    game_number: int = 0
    game_name: str = ''
    status: str = ''
    requirements: list[Requirement] = field(default_factory=list)


def _wait_for_turn():
    with _turn_lock:
        now = time.monotonic()
        turn = max(now, _next_turn[0])
        _next_turn[0] = turn + MIN_GAP
    if turn > now:
        time.sleep(turn - now)


def _ask(query, variables=None):
    body = json.dumps({'query': query, 'variables': variables or {}}).encode()
    _wait_for_turn()
    try:
        with urlopen(Request(URL, data=body, headers=HEADERS), timeout=TIMEOUT) as reply:
            answer = json.loads(reply.read().decode('utf-8'))
    except HTTPError as exc:
        raise NexusError(f'Nexus ответил ошибкой HTTP {exc.code}') from exc
    except (URLError, OSError, ValueError) as exc:
        raise NexusError(f'Nexus не ответил: {exc}') from exc
    # Errors next to data mean that a part is missing, for example a mod that was hidden.
    data = answer.get('data') if isinstance(answer, dict) else None
    if not data:
        raise NexusError('Nexus не вернул данных')
    return data


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _requirement(raw, own_game, own_number):
    external = bool(raw.get('externalRequirement'))
    number = _number(raw.get('gameId'))
    return Requirement(name=str(raw.get('modName') or '').strip(), note=str(raw.get('notes') or '').strip(),
                       mod_id=0 if external else _number(raw.get('modId')),
                       game=own_game if not external and number == own_number else '',
                       url=str(raw.get('url') or '').strip(), external=external), number


def _other_game_domains(numbers):
    """A requirement may be a mod of another game, and only the number of that game is known."""
    if not numbers:
        return {}
    wanted = list(numbers.items())
    query = '{' + ' '.join(f'q{index}: mod(modId: "{int(mod_id)}", gameId: "{int(game)}") {{ game {{ id domainName }} }}'
                           for index, (game, mod_id) in enumerate(wanted)) + '}'
    try:
        data = _ask(query)
    except NexusError:
        return {}
    found = {}
    for index, (game, _) in enumerate(wanted):
        node = data.get(f'q{index}') or {}
        found[game] = str((node.get('game') or {}).get('domainName') or '')
    return found


def _parse_mod(node):
    game = node.get('game') or {}
    domain, number = str(game.get('domainName') or ''), _number(game.get('id'))
    info = ModInfo(game=domain, mod_id=_number(node.get('modId')), name=str(node.get('name') or '').strip(),
                   version=str(node.get('version') or '').strip(), updated_at=_time(node.get('updatedAt')),
                   size_kb=_number(node.get('fileSize')), game_number=number, game_name=str(game.get('name') or ''),
                   status=str(node.get('status') or ''))
    needs = node.get('modRequirements') or {}
    others = {}
    for raw in (needs.get('nexusRequirements') or {}).get('nodes') or []:
        requirement, requirement_game = _requirement(raw, domain, number)
        if requirement.name and requirement.mod_id != info.mod_id or requirement.external:
            info.requirements.append(requirement)
            if not requirement.external and not requirement.game and requirement.mod_id:
                others[requirement_game] = requirement.mod_id
                requirement.url = str(requirement_game)  # replaced by the caller, see mod_infos
    for raw in needs.get('dlcRequirements') or []:
        name = str((raw.get('gameExpansion') or {}).get('name') or '').strip()
        if name:
            info.requirements.append(Requirement(name=name, note=str(raw.get('notes') or '').strip(), dlc=True))
    return info, others


def mod_infos(ids):
    """{(game, mod_id): ModInfo} for those of the mods that Nexus knows and shows."""
    ids = list(dict.fromkeys((str(game), int(mod_id)) for game, mod_id in ids))
    found, others = {}, {}
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        data = _ask(_MODS, {'ids': [{'gameDomain': game, 'modId': mod_id} for game, mod_id in chunk], 'count': len(chunk)})
        for node in (data.get('legacyModsByDomain') or {}).get('nodes') or []:
            info, other = _parse_mod(node)
            found[(info.game, info.mod_id)] = info
            others.update(other)
    domains = _other_game_domains(others)
    for info in found.values():
        for requirement in info.requirements:
            if not (requirement.external or requirement.dlc or requirement.game):
                # Without its game there is no way to add it, but the person still sees its name.
                requirement.game, requirement.url = domains.get(_number(requirement.url), ''), ''
    return found


def mod_files(mod_id, game_number):
    """Every file of one mod: id, name, version, date, category and size."""
    data = _ask(_FILES, {'modId': str(int(mod_id)), 'gameId': str(int(game_number))})
    return [{'file_id': _number(row.get('fileId')), 'name': str(row.get('name') or ''), 'version': str(row.get('version') or ''),
             'date': _number(row.get('date')), 'category': str(row.get('category') or '').upper(),
             'size': _number(row.get('sizeInBytes'))} for row in data.get('modFiles') or []]
