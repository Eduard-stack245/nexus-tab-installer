"""What a mod needs according to its author, and what of it the person already has.

The list on Nexus is written by hand. It may be out of date, and a part of it may matter only for
some of the people. So the program can only tell what the author wrote and what is already here:
it never insists, and never takes this as a reason not to download something.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MISSING, LISTED, DOWNLOADED, INSTALLED, ELSEWHERE, GAME_DLC = 'missing', 'listed', 'downloaded', 'installed', 'elsewhere', 'dlc'
# What authors write next to something that is not needed by everyone. Seen on real mods:
# "Optional: ...", "Only for VR users", "Only for game versions older than 1.6.1130".
_NOT_FOR_EVERYONE = re.compile(r'\b(optional|optionally|only for|only if|only when|only needed|only required|not required|'
                               r'not needed|recommended|if you|for vr|vr users?|vr only|if using|when using)\b|'
                               r'необязательн|опционально|только для|только если|если вы', re.I)
SURE_LIMIT = 200  # far more than any list; a safety net for lists that keep growing by themselves


def looks_optional(note):
    return bool(_NOT_FOR_EVERYONE.search(note or ''))


@dataclass
class Present:
    """What is already here. installed: (game, mod_id) -> the name of the manager."""
    listed: set = field(default_factory=set)
    downloaded: set = field(default_factory=set)
    installed: dict = field(default_factory=dict)


@dataclass
class Need:
    name: str
    note: str = ''
    game: str = ''
    mod_id: int = 0
    url: str = ''
    state: str = MISSING
    optional: bool = False
    where: str = ''

    @property
    def key(self):
        return self.game, self.mod_id

    @property
    def page(self):
        if self.game and self.mod_id:
            return f'https://www.nexusmods.com/{self.game}/mods/{self.mod_id}'
        return self.url


def assess(info, present):
    """Everything this mod needs, the missing first and what is not for everyone after it."""
    needs = []
    for requirement in info.requirements:
        need = Need(requirement.name, requirement.note, requirement.game, requirement.mod_id, requirement.url,
                    optional=looks_optional(requirement.note))
        if requirement.dlc:
            need.state = GAME_DLC
        elif requirement.external or not (requirement.game and requirement.mod_id):
            need.state = ELSEWHERE
        elif need.key in present.listed:
            need.state = LISTED
        elif need.key in present.installed:
            need.state, need.where = INSTALLED, present.installed[need.key]
        elif need.key in present.downloaded:
            need.state = DOWNLOADED
        needs.append(need)
    rank = {MISSING: 0, ELSEWHERE: 1, GAME_DLC: 2, LISTED: 3, DOWNLOADED: 4, INSTALLED: 5}
    return sorted(needs, key=lambda need: (rank[need.state], need.optional, need.name.casefold()))


@dataclass
class Overview:
    """All of the list at once: what is missing, without repeating a mod that many mods need."""
    required: list = field(default_factory=list)
    optional: list = field(default_factory=list)
    elsewhere: list = field(default_factory=list)

    @property
    def signature(self):
        return frozenset(need.key for need in self.required)


def overview(infos, present):
    result = Overview()
    seen = set()
    needs = [need for info in infos for need in assess(info, present)]
    # The same mod may be optional for one and required for another: then it is required.
    for need in sorted(needs, key=lambda need: need.optional):
        if need.state not in (MISSING, ELSEWHERE):
            continue
        key = need.key if need.mod_id else need.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        if need.state == ELSEWHERE:
            result.elsewhere.append(need)
        else:
            (result.optional if need.optional else result.required).append(need)
    return result


def as_item(need, target_title=None):
    """A requirement as an entry of the list of mods."""
    return {'url': f'https://www.nexusmods.com/{need.game}/mods/{need.mod_id}', 'title': target_title or need.name}
