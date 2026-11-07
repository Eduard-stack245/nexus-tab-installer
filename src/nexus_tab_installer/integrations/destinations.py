"""Safe destination selection. Archives only; never writes to a game's Data/mods directory."""
from __future__ import annotations
import configparser
import os
from pathlib import Path


class DestinationError(ValueError):
    pass


def mo2_downloads(ini_path: str | Path) -> Path:
    ini = Path(ini_path).expanduser().resolve()
    if not ini.is_file() or ini.name.lower() != 'modorganizer.ini':
        raise DestinationError('Выберите существующий файл ModOrganizer.ini вашего экземпляра MO2.')
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str.lower
    try:
        with ini.open('r', encoding='utf-8-sig') as stream:
            config.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise DestinationError(f'Не удалось прочитать настройки MO2: {exc}') from exc
    settings = config['Settings'] if config.has_section('Settings') else {}
    base_raw = settings.get('base_directory', '').strip().strip('"')
    base = _expand(base_raw, ini.parent, ini.parent) if base_raw else ini.parent
    raw = settings.get('download_directory', '').strip().strip('"') or '%BASE_DIR%/downloads'
    return _expand(raw, base, ini.parent)


def _expand(raw: str, base: Path, ini_dir: Path) -> Path:
    s = raw.replace('%BASE_DIR%', str(base)).replace('%base_dir%', str(base))
    s = os.path.expandvars(os.path.expanduser(s))
    candidate = Path(s)
    if not candidate.is_absolute():
        candidate = ini_dir / candidate
    return candidate.resolve()


def resolve_destination(mode: str, path: str = '', mo2_ini: str = '') -> Path:
    if mode == 'mo2':
        if not mo2_ini:
            raise DestinationError('Укажите ModOrganizer.ini из нужного экземпляра MO2.')
        dest = mo2_downloads(mo2_ini)
    elif mode in ('folder', 'vortex'):
        if not path.strip():
            raise DestinationError('Укажите папку для архивов.')
        dest = Path(path).expanduser().resolve()
    else:
        raise DestinationError('Неизвестный режим назначения.')
    if not dest.is_dir():
        raise DestinationError(f'Папка не существует: {dest}. Создайте её или выберите другую.')
    if not os.access(dest, os.W_OK):
        raise DestinationError(f'Нет прав записи в папку: {dest}')
    return dest


def _windows_install_roots(env):
    """Small, bounded set of common portable installation locations."""
    roots = []
    for var in ('LOCALAPPDATA', 'APPDATA', 'ProgramFiles', 'ProgramFiles(x86)', 'PROGRAMDATA'):
        if env.get(var):
            base = Path(env[var])
            roots.extend((base / 'ModOrganizer', base / 'Mod Organizer 2', base / 'MO2'))
    for var in ('USERPROFILE',):
        if env.get(var):
            home = Path(env[var]); roots.extend((home / 'Modding', home / 'Games', home / 'MO2'))
    # Many portable MO2 installs live outside C:; don't crawl whole drives.
    if os.name == 'nt':
        for letter in 'CDEFGHI':
            drive = Path(f'{letter}:/')
            if drive.exists():
                roots.extend((drive / 'MO2', drive / 'Mod Organizer 2', drive / 'Modding',
                              drive / 'Games', drive / 'Tools'))
    return roots


def _registry_install_roots():
    """Read-only Windows registry hints; never required to run the application."""
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    result = []
    keys = (r'Software\Mod Organizer Team\Mod Organizer',
            r'Software\Microsoft\Windows\CurrentVersion\Uninstall')
    for key_name in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as reg:
                if key_name.endswith('Uninstall'):
                    subkeys = []
                    for idx in range(min(winreg.QueryInfoKey(reg)[0], 250)):
                        subkeys.append(winreg.EnumKey(reg, idx))
                    for sub in subkeys:
                        try:
                            with winreg.OpenKey(reg, sub) as child:
                                name = str(winreg.QueryValueEx(child, 'DisplayName')[0]).lower()
                                if 'mod organizer' in name:
                                    result.append(Path(winreg.QueryValueEx(child, 'InstallLocation')[0]))
                        except (OSError, ValueError):
                            continue
                else:
                    for idx in range(min(winreg.QueryInfoKey(reg)[1], 100)):
                        name, value, _ = winreg.EnumValue(reg, idx)
                        if isinstance(value, str) and any(t in name.lower() for t in ('path', 'directory', 'instance', 'install')):
                            result.append(Path(value))
        except OSError:
            continue
    return result


def find_mo2_instances(roots=None):
    """Find global and portable MO2 instances (bounded, no recursive drive scan).

    Accepts a path to a folder, executable or ini as a manual search location.
    """
    if roots is None:
        home = Path.home()
        candidates = _windows_install_roots(os.environ)
        candidates.extend(_registry_install_roots())
        candidates.extend((home / 'Modding' / 'MO2', home / 'MO2'))
    else:
        candidates = [Path(x).expanduser() for x in roots]
    found = set()
    for base in candidates:
        if base.is_file():
            if base.name.casefold() == 'modorganizer.ini':
                found.add(base.resolve())
                continue
            if base.name.casefold() in ('modorganizer.exe', 'modorganizer'):
                base = base.parent
            else:
                continue
        if not base.is_dir():
            continue
        direct = base / 'ModOrganizer.ini'
        if direct.is_file():
            found.add(direct.resolve())
        try:
            children = list(base.iterdir())[:250]
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                ini = child / 'ModOrganizer.ini'
                if ini.is_file():
                    found.add(ini.resolve())
    return sorted(found, key=lambda x: str(x).casefold())
