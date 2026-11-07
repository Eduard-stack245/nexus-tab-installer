"""Explicit per-mod delivery after a finished download.

Library never modifies games. MO2 gets every file in its Downloads with the Nexus details; a simple
mod is also installed for the selected profile if MO2 is closed. Vortex installs through its own
command line. Direct Skyrim Data installation uses the application's reversible manifest and
checksum-based backup protection.

A Nexus file need not be an archive. Both managers only take archives, so a plain file reaches
them inside a small zip; the file in the session folder stays as it was downloaded.
"""
from __future__ import annotations
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from nexus_tab_installer.core.display import clean_mod_title
from nexus_tab_installer.core.file_kinds import is_archive_name, is_program
from nexus_tab_installer.downloads.archives import stage_archive
from nexus_tab_installer.downloads.export import export_archive
from nexus_tab_installer.downloads.transfer import DownloadError
from nexus_tab_installer.integrations import vortex
from nexus_tab_installer.integrations.mo2_install import (MO2InstallError, check_game, hand_over, install_simple,
                                                          mark_installed, mo2_is_running)
from nexus_tab_installer.integrations.managed_install import deploy
from nexus_tab_installer.core.install_plan import validate_target

PROGRAM_NOTE = 'Это программа, а не мод: файл сохранён в папке сессии, запустите его сами, если доверяете автору.'
WRAPPER_DAYS = 2


def _reject_complex(stage):
    stage = Path(stage)
    if any(p.is_symlink() for p in stage.rglob('*')):
        raise ValueError('Мод содержит символическую ссылку')
    if any(p.is_file() and p.suffix.casefold() in ('.exe', '.bat', '.cmd', '.ps1')
           for p in stage.rglob('*')):
        raise ValueError('Исполняемый установщик требует ручной проверки')
    if any(p.name.casefold() == 'moduleconfig.xml' and p.parent.name.casefold() == 'fomod'
           for p in stage.rglob('*')):
        raise ValueError('Для FOMOD требуется установщик MO2/Vortex')
    if any(p.is_dir() and p.name.casefold() == 'data' for p in stage.iterdir()):
        raise ValueError('Архив содержит вложенную Data; определите правильный корень файлов в менеджере')
    known = {'meshes', 'textures', 'scripts', 'interface', 'sound', 'music', 'skse',
             'seq', 'strings', 'video', 'grass', 'lodsettings', 'source'}
    entries = list(stage.iterdir())
    if not any(p.name.casefold() in known and p.is_dir() for p in entries) and not any(
           p.is_file() and p.suffix.casefold() in ('.esp', '.esm', '.esl', '.bsa', '.ba2') for p in entries):
        raise ValueError('Структура файлов не похожа на корень Skyrim Data; безопаснее установить через MO2')


def stage_download(path, scratch):
    """A folder with the contents of the download: an unpacked archive, or the plain file itself."""
    path = Path(path)
    if is_archive_name(path.name):
        return stage_archive(path, scratch)[0]
    stage = Path(scratch) / ('file-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    shutil.copy2(path, stage / path.name)
    return stage


def wrap_plain_file(path, folder):
    """A zip with the file at its root: how a plain file gets past a manager that only takes archives."""
    path, folder = Path(path), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    wrapper = folder / f'{path.stem or path.name}.zip'
    with zipfile.ZipFile(wrapper, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(path, path.name)
    return wrapper


def _mod_title(item, path):
    title = clean_mod_title(item.get('title'), item['game'], item['mod_id'])
    return Path(path).stem if title == f"Мод #{item['mod_id']}" else title


def _nexus_details(item, path):
    return {'game': item['game'], 'mod_id': int(item['mod_id']), 'file_id': int(item.get('download_file_id') or 0),
            'file_name': Path(path).name, 'title': _mod_title(item, path)}


def _to_mo2(path, item, scratch, mo2_ini, mo2_profile):
    check_game(mo2_ini, item['game'])
    plain = not is_archive_name(path.name)
    details = _nexus_details(item, path)
    wrapper = wrap_plain_file(path, Path(scratch) / 'wrapper') if plain else path
    saved = hand_over(wrapper, mo2_ini, details)
    if mo2_is_running():
        return 'Требует MO2', (f'Файл «{saved.name}» во вкладке Downloads открытого MO2: установите его двойным щелчком. '
                               'Сама программа ставит моды, только когда MO2 закрыт.')
    try:
        stage = stage_download(path, Path(scratch) / 'stage')
        result = install_simple(stage, mo2_ini, mo2_profile, details['title'],
                                nexus={**details, 'merge': plain, 'file_name': saved.name})
    except (MO2InstallError, DownloadError) as exc:
        return 'Требует MO2', f'Файл «{saved.name}» во вкладке Downloads MO2, установите его двойным щелчком. {exc}'
    mark_installed(saved)
    if result.get('already'):
        return 'Установлен MO2', f"Этот файл уже установлен в моде «{result['mod']}»"
    note = ' Плагин включите на вкладке Plugins.' if any(
        p.suffix.casefold() in ('.esp', '.esm', '.esl') for p in Path(result['folder']).iterdir()) else ''
    return 'Установлен MO2', f"Мод «{result['mod']}» включён в профиле {result['profile']}.{note}"


def _to_vortex(path, item, staging_root, vortex_exe, vortex_folder):
    if not vortex_exe:
        copied = export_archive(path, vortex_folder)
        return 'Передано Vortex', f'Файл в {copied}. Установку и включение выполните в Vortex.'
    wrappers = Path(staging_root) / 'for-vortex'
    # Vortex takes the file some time later: the zip waits here, not in a scratch folder.
    if wrappers.is_dir():
        for old in wrappers.glob('*/*'):
            if time.time() - old.stat().st_mtime > WRAPPER_DAYS * 86400:
                old.unlink(missing_ok=True)
    send = path if is_archive_name(path.name) else wrap_plain_file(path, wrappers / uuid.uuid4().hex)
    result = vortex.send(send, item['game'], vortex_exe, vortex_folder)
    if result == 'installing':
        return 'Передано Vortex', 'Vortex устанавливает файл. Если он что-то спросит, ответьте в его окне.'
    return 'Передано Vortex', (f'В Vortex сейчас другая игра, или программа не смогла это проверить, поэтому файл положен в загрузки '
                               f'нужной игры: {result}. Откройте её в Vortex и нажмите Install.')


def deliver(archive, item, target, *, staging_root, journal, mo2_ini='', mo2_profile='', vortex_folder='', game_folder='', vortex_exe=''):
    """Return a user-facing state and detail, or raise a meaningful error.

    All downloaded files remain in the portable library. Staging is isolated
    and is removed after an attempted install; managed-install backups are kept.
    """
    archive = Path(archive).resolve()
    if not archive.is_file():
        raise ValueError('Файл не найден во внутренней библиотеке')
    validate_target(target, mo2_ini=mo2_ini, mo2_profile=mo2_profile,
                    vortex_folder=vortex_folder, game_folder=game_folder, vortex_exe=vortex_exe)
    if target == 'library':
        return 'Готово', 'Файл сохранён в библиотеке программы'
    if target == 'game' and item['game'] != 'skyrimspecialedition':
        raise ValueError('Прямая установка доступна только для Skyrim Special Edition')
    if is_program(archive.name):
        return 'Готово', PROGRAM_NOTE
    if target == 'vortex':
        return _to_vortex(archive, item, staging_root, vortex_exe, vortex_folder)
    scratch = Path(staging_root) / ('install-' + uuid.uuid4().hex)
    scratch.mkdir(parents=True)
    try:
        if target == 'mo2':
            return _to_mo2(archive, item, scratch, mo2_ini, mo2_profile)
        if target == 'game':
            stage = stage_download(archive, scratch)
            _reject_complex(stage)
            mid = deploy(stage, game_folder, journal,
                         f"{item['game']} #{item['mod_id']}")
            return 'Установлен в игру', f'Резервная копия и журнал: {mid}'
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    raise ValueError('Неизвестное назначение')
