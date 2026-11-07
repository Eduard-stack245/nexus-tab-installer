"""Opt-in simple MO2 installer and hand-over. Never bypass complex FOMOD installer.

Only touches the chosen MO2 instance's downloads and mods folders and an explicitly selected
profile's modlist.txt. Installing needs MO2 closed; a file in Downloads does not. This is NOT
a replacement for MO2's installer plugins or load-order management.
"""
from __future__ import annotations
import configparser
import os
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from .destinations import DestinationError, _expand, mo2_downloads
from nexus_tab_installer.downloads.export import free_target, put_file

class MO2InstallError(ValueError): pass


_install_lock = threading.RLock()


# What MO2 calls the game inside its notes about a download.
SHORT_GAME_NAMES = {'skyrimspecialedition': 'SkyrimSE', 'skyrim': 'Skyrim', 'fallout4': 'Fallout4', 'fallout3': 'Fallout3',
                    'newvegas': 'FalloutNV', 'oblivion': 'Oblivion', 'morrowind': 'Morrowind', 'starfield': 'Starfield',
                    'enderal': 'Enderal', 'enderalspecialedition': 'EnderalSE'}
# Which Nexus games an instance can take, for games where a wrong one can be told for certain.
INSTANCE_GAMES = {'skyrimspecialedition': {'skyrimspecialedition'}, 'skyrimvr': {'skyrimspecialedition'},
                  'skyrim': {'skyrim'}, 'fallout4': {'fallout4'}, 'fallout4vr': {'fallout4'}, 'fallout3': {'fallout3'},
                  'newvegas': {'newvegas'}, 'oblivion': {'oblivion'}, 'morrowind': {'morrowind'}, 'starfield': {'starfield'},
                  'enderal': {'enderal', 'skyrim'}, 'enderalspecialedition': {'enderalspecialedition', 'skyrimspecialedition'}}


def _read_ini(ini):
    cfg=configparser.ConfigParser(interpolation=None,strict=False)
    cfg.read(ini,encoding='utf-8-sig')
    return cfg


def _plain_value(value):
    """MO2 keeps some values as @ByteArray(...)."""
    value=str(value or '').strip()
    match=re.fullmatch(r'@ByteArray\((.*)\)',value)
    return match[1] if match else value


def instance_info(ini_file):
    """Game, version, chosen profile and program of an instance, for the settings screen."""
    ini=Path(ini_file).expanduser().resolve()
    if not ini.is_file() or ini.name.casefold()!='modorganizer.ini':
        raise MO2InstallError('Выберите ModOrganizer.ini выбранного экземпляра MO2')
    cfg=_read_ini(ini)
    general=cfg['General'] if cfg.has_section('General') else {}
    exe=ini.parent/'ModOrganizer.exe'
    return {'game':_plain_value(general.get('gamename','')),'version':_plain_value(general.get('version','')),
            'profile':_plain_value(general.get('selected_profile','')),'exe':exe if exe.is_file() else None}


def check_game(ini_file, domain):
    """A mod for another game must not reach this instance. An unfamiliar game is not judged."""
    game=instance_info(ini_file)['game']
    allowed=INSTANCE_GAMES.get(re.sub(r'[^a-z0-9]','',game.casefold()))
    if allowed is not None and domain not in allowed:
        raise MO2InstallError(f'Этот экземпляр MO2 настроен на игру «{game}», а мод от игры {domain}. Выберите другой экземпляр или назначение.')


def _qt_text(value):
    """A text value for an ini file Qt reads back: a bare comma would turn it into a list."""
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'


def _write_download_note(note, details):
    content=['[General]',f"gameName={SHORT_GAME_NAMES.get(details['game'],details['game'])}",
             f"modID={details['mod_id']}",f"fileID={details['file_id']}",f"name={_qt_text(details['file_name'])}",
             f"modName={_qt_text(details['title'])}",'repository=Nexus','installed=false','uninstalled=false',
             'paused=false','removed=false','']
    # All at once: MO2 acts on what it sees in the folder at any moment.
    temporary=note.with_name(f'.nexus-note-{uuid.uuid4().hex}')
    temporary.write_text('\n'.join(content),encoding='utf-8')
    temporary.replace(note)


def hand_over(source, ini_file, details):
    """Put a file into MO2's Downloads with its Nexus details; MO2 notices it even while running.

    The note goes second: a running MO2 deletes a note whose file is not there yet, and the note
    appearing makes it read the list again. On the same disk the file is a hard link, so the
    session folder and MO2 share one copy.
    """
    source=Path(source).resolve()
    try:
        downloads=mo2_downloads(ini_file)
    except DestinationError as exc:
        raise MO2InstallError(str(exc)) from exc
    downloads.mkdir(parents=True,exist_ok=True)
    target,already=free_target(source,downloads)
    if not already:
        put_file(source,target,link=True)
    note=downloads/(target.name+'.meta')
    if not note.is_file():
        _write_download_note(note,{**details,'file_name':target.name})
    return target


def mark_installed(archive):
    """MO2 shows a tick in Downloads for what is installed."""
    note=Path(str(archive)+'.meta')
    if note.is_file():
        note.write_text(note.read_text(encoding='utf-8').replace('installed=false','installed=true',1),encoding='utf-8')


def paths_from_ini(ini_file):
    ini=Path(ini_file).expanduser().resolve()
    if not ini.is_file() or ini.name.casefold()!='modorganizer.ini':
        raise MO2InstallError('Выберите ModOrganizer.ini выбранного экземпляра MO2')
    cfg=_read_ini(ini)
    settings=cfg['Settings'] if cfg.has_section('Settings') else {}
    base=_expand(settings.get('base_directory','%BASE_DIR%'),ini.parent,ini.parent)
    def resolve(key,default):
        return _expand(settings.get(key,default),base,ini.parent)
    return resolve('mod_directory','%BASE_DIR%/mods'),resolve('profiles_directory','%BASE_DIR%/profiles')


def available_profiles(ini_file):
    _,profiles=paths_from_ini(ini_file)
    if not profiles.is_dir(): return []
    return sorted([p.name for p in profiles.iterdir() if p.is_dir() and (p/'modlist.txt').is_file()], key=str.casefold)


def _safe_name(name):
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',str(name)).strip(' .')
    if not name or name.casefold() in ('overwrite','data','.','..'): raise MO2InstallError('Недопустимое имя мода')
    return name[:110]


def _is_complex(stage):
    return any(p.is_file() and p.name.lower() == 'moduleconfig.xml' and p.parent.name.lower() == 'fomod' for p in stage.rglob('*')) or any(p.is_file() and p.suffix.lower() in ('.exe','.bat','.cmd','.ps1') for p in stage.rglob('*'))


def mo2_is_running():
    if os.name != 'nt': return False
    import subprocess
    try:
        result=subprocess.run(['tasklist','/FI','IMAGENAME eq ModOrganizer.exe'],capture_output=True,timeout=5,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError,subprocess.SubprocessError):
        return False
    # Bytes: the rest of the answer is in the console code page, the name of the program is not.
    return b'modorganizer.exe' in result.stdout.lower()


def _ensure_closed():
    if mo2_is_running():
        raise MO2InstallError('Закройте MO2 перед установкой: профиль нельзя менять при запущенном менеджере')


def _read_mod_note(folder):
    cfg=configparser.ConfigParser(interpolation=None,strict=False)
    cfg.optionxform=str
    try:
        cfg.read(folder/'meta.ini',encoding='utf-8-sig')
    except (OSError,configparser.Error):
        pass
    return cfg


def _installed_file_ids(folder):
    cfg=_read_mod_note(folder)
    if not cfg.has_section('installedFiles'): return set()
    return {value for key,value in cfg['installedFiles'].items() if key.endswith('\\fileid')}


def _mod_id_of(folder):
    cfg=_read_mod_note(folder)
    return cfg['General'].get('modid','') if cfg.has_section('General') else ''


def _write_mod_note(folder, nexus, file_ids=None):
    """What MO2 reads to link a mod folder with Nexus: the mod, its files, the file installed from."""
    ids=sorted({*(file_ids or ()),str(nexus['file_id'])})
    content=['[General]',f"gameName={SHORT_GAME_NAMES.get(nexus['game'],nexus['game'])}",f"modid={nexus['mod_id']}",
             f"installationFile={_qt_text(nexus['file_name'])}",'repository=Nexus','','[installedFiles]']
    for number,file_id in enumerate(ids,1):
        content+=[f"{number}\\modid={nexus['mod_id']}",f"{number}\\fileid={file_id}"]
    content+=[f'size={len(ids)}','']
    (folder/'meta.ini').write_text('\n'.join(content),encoding='utf-8')


def _add_to_mod(stage, target, nexus):
    """Several plain files of one mod belong in one mod folder. Nothing different gets overwritten."""
    from nexus_tab_installer.downloads.export import _sha
    files=[p for p in stage.rglob('*') if p.is_file()]
    for path in files:
        existing=target/path.relative_to(stage)
        if existing.exists() and (not existing.is_file() or _sha(existing)!=_sha(path)):
            raise MO2InstallError(f'В моде уже есть другой файл с именем {path.name}')
    for path in files:
        existing=target/path.relative_to(stage)
        existing.parent.mkdir(parents=True,exist_ok=True)
        if not existing.exists(): shutil.copy2(path,existing)
    _write_mod_note(target,nexus,_installed_file_ids(target))


def install_simple(stage_path, ini_file, profile, mod_name, nexus=None):
    """One at a time: files finish in parallel, and the profile and a mod folder are shared."""
    with _install_lock:
        return _install_simple(stage_path, ini_file, profile, mod_name, nexus)


def _install_simple(stage_path, ini_file, profile, mod_name, nexus):
    """Install a simple staged mod and enable its left-pane entry in one profile.

    Fails closed for complex installers and existing mod folders. On errors,
    rolls back its own mod folder and profile text. The profile must already exist.
    With `nexus` details the mod folder remembers its Nexus file: the same file is not
    installed twice, and an existing mod folder is added to or kept apart, not refused.
    """
    _ensure_closed()
    stage=Path(stage_path).resolve()
    if not stage.is_dir() or stage.is_symlink(): raise MO2InstallError('Выберите существующую подготовленную папку')
    if _is_complex(stage): raise MO2InstallError('FOMOD/исполняемый установщик: требуется установка через интерфейс MO2')
    if (stage/'Data').is_dir() or (stage/'data').is_dir():
        raise MO2InstallError('Архив содержит вложенную Data: нужен выбор корня данных через установщик MO2')
    entries=list(stage.rglob('*'))
    if not any(p.is_file() for p in entries): raise MO2InstallError('Нет файлов для установки')
    if any(p.is_symlink() for p in entries): raise MO2InstallError('Символьные ссылки в моде запрещены')
    mods,profiles=paths_from_ini(ini_file)
    name=_safe_name(mod_name)
    if profile not in available_profiles(ini_file): raise MO2InstallError('Профиль не найден в выбранном экземпляре MO2')
    target=mods/name
    if nexus is not None and mods.is_dir():
        if target.is_dir() and str(nexus['file_id']) in _installed_file_ids(target):
            return {'mod':name,'profile':profile,'folder':str(target),'enabled':True,'already':True}
        if target.is_dir() and nexus.get('merge') and _mod_id_of(target)==str(nexus['mod_id']):
            _add_to_mod(stage,target,nexus)
            return {'mod':name,'profile':profile,'folder':str(target),'enabled':True}
        if target.exists():
            name=_safe_name(f"{mod_name} ({nexus['file_id']})")
            target=mods/name
            if target.is_dir() and str(nexus['file_id']) in _installed_file_ids(target):
                return {'mod':name,'profile':profile,'folder':str(target),'enabled':True,'already':True}
    if not mods.is_dir() or target.exists(): raise MO2InstallError('Папка mods отсутствует или мод с таким именем уже существует')
    modlist=profiles/profile/'modlist.txt'
    original=modlist.read_bytes()
    text=original.decode('utf-8-sig')
    lines=text.splitlines(keepends=True)
    if any(s.lstrip('+-').strip().casefold()==name.casefold() for s in text.splitlines()):
        raise MO2InstallError('Мод уже указан в профиле')
    newline='\r\n' if b'\r\n' in original else '\n'
    tmp=mods/('.nxi-'+uuid.uuid4().hex)
    committed=False
    try:
        shutil.copytree(stage,tmp,symlinks=False)
        if nexus is not None and not (tmp/'meta.ini').exists():
            _write_mod_note(tmp,nexus)
        # Avoid clobbering an existing managed mod; MO2 may be started externally.
        if target.exists(): raise MO2InstallError('Мод уже появился в MO2')
        tmp.rename(target)
        committed=True
        # The top of modlist.txt is the highest priority: where MO2 itself puts a new mod.
        position=0
        for line in lines:
            if not line.startswith('#'): break
            position+=1
        content=''.join(lines[:position])+'+'+name+newline+''.join(lines[position:])
        fd,backup=tempfile.mkstemp(dir=modlist.parent,prefix='.nxi-backup-',suffix='.tmp')
        os.close(fd)
        try:
            Path(backup).write_bytes(original)
            modlist.write_bytes(('\ufeff' if original.startswith(b'\xef\xbb\xbf') else '').encode('utf-8') + content.encode('utf-8'))
        except Exception:
            modlist.write_bytes(original)
            raise
        finally: Path(backup).unlink(missing_ok=True)
        return {'mod':name,'profile':profile,'folder':str(target),'enabled':True}
    except Exception:
        if committed and target.is_dir(): shutil.rmtree(target)
        if tmp.exists(): shutil.rmtree(tmp)
        raise
