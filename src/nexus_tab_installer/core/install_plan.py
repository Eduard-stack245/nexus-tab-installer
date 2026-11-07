"""Per-mod selection and install destination, independent of Flet.

Scans merge all discovered browser windows. Only checked mods are prepared.
The destination is explicitly chosen per mod and never guessed from a URL.
"""
from dataclasses import dataclass
from pathlib import Path

from .links import normalize_entries

TARGETS = ('library', 'mo2', 'vortex', 'game')


def mod_key(item):
    return str(item['game']), int(item['mod_id'])


@dataclass
class PlanChoice:
    selected: bool = True
    target: str = 'library'


class ModPlan:
    def __init__(self):
        self.items = []
        self.choices = {}
        self.default_target = 'library'

    def replace(self, items):
        """New import replaces old list but preserves choices for shared mods."""
        self.items = normalize_entries(items)
        old = self.choices
        self.choices = {mod_key(x): old.get(mod_key(x), PlanChoice(target=self.default_target)) for x in self.items}
        return self.items

    def append(self, items, targets=None):
        """Add to the list as it is: nothing in it changes, and neither does what is being downloaded."""
        fresh = [item for item in normalize_entries(items) if mod_key(item) not in self.choices]
        for item in fresh:
            self.choices[mod_key(item)] = PlanChoice(target=(targets or {}).get(mod_key(item), self.default_target))
        self.items.extend(fresh)
        return fresh

    def merge(self, *sources):
        """Aggregate all windows/sources and remove duplicate mod identities."""
        return self.replace([item for source in sources for item in source])

    def update(self, item, *, selected=None, target=None):
        key = mod_key(item)
        if key not in self.choices:
            raise KeyError('Мод отсутствует в текущем сканировании')
        choice = self.choices[key]
        if selected is not None:
            choice.selected = bool(selected)
        if target is not None:
            if target not in TARGETS:
                raise ValueError('Неизвестное назначение')
            choice.target = target

    def chosen(self):
        return [(item, self.choices[mod_key(item)].target) for item in self.items
                if self.choices[mod_key(item)].selected]

    def set_all_targets(self, target):
        """The same destination for the whole list and for what gets added later."""
        if target not in TARGETS:
            raise ValueError('Неизвестное назначение')
        self.default_target = target
        for choice in self.choices.values():
            choice.target = target

    def set_all(self, selected):
        for choice in self.choices.values():
            choice.selected = bool(selected)


def validate_target(target, *, mo2_ini='', mo2_profile='', vortex_folder='', game_folder='', vortex_exe=''):
    """Validate before downloading, preventing surprise writes to game/managers."""
    if target not in TARGETS:
        raise ValueError('Неизвестное назначение')
    if target == 'library':
        return
    if target == 'mo2':
        from nexus_tab_installer.integrations.mo2_install import available_profiles
        if not mo2_ini or not mo2_profile or mo2_profile not in available_profiles(mo2_ini):
            raise ValueError('Для MO2 выберите существующий экземпляр и профиль в Настройках')
    if target == 'vortex':
        if vortex_exe and Path(vortex_exe).expanduser().is_file():
            return
        if not vortex_folder or not Path(vortex_folder).expanduser().is_dir():
            raise ValueError('Vortex не найден. Нажмите «Настроить MO2 и Vortex» и укажите Vortex.exe')
    if target == 'game':
        folder = Path(game_folder).expanduser()
        if not game_folder or not folder.is_dir():
            raise ValueError('Для установки в игру укажите существующую папку Data в Настройках')
        if folder.name.casefold() != 'data':
            raise ValueError('Для прямой установки выберите именно папку Data игры, не её корневую папку')
