"""Nexus Tab Installer / Flet desktop interface, using the existing tested back end.
Includes optional opt-in Free browser click assistance; no stealth or CAPTCHA bypass.
"""
import json
import os
import queue
import secrets
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from time import time as time_now
from http.server import ThreadingHTTPServer

import flet as ft
from nexus_tab_installer.ui.views import (ACCENT, GOOD, MUTED, BAD, WARN, RETRYABLE_STATES, downloads_view, job_row, notice, plural,
                                          requirement_chip, requirement_panel, session_card, settings_view, size_text,
                                          updates_card, PREMIUM_WARNING)
from nexus_tab_installer.ui.i18n import ScreenLanguage, source_text, system_language
from nexus_tab_installer.core.paths import ensure_layout, storage_paths, application_root, portable_path, saved_path
from nexus_tab_installer.core.settings import load_settings, save_settings
from nexus_tab_installer.core.trace import TraceLog, describe_event, classify_event, diagnose_trace, is_main_event, local_clock
from nexus_tab_installer.core.links import normalize_entries, extract_mod_links, save_pack, load_pack
from nexus_tab_installer.core.display import clean_mod_title
from nexus_tab_installer.nexus.api import NexusClient, NexusError, NexusRateLimited, nxm_parts
from nexus_tab_installer.core.file_catalog import FileChoice, catalog, CATEGORY_LABELS
from nexus_tab_installer.core.workflow_session import WorkflowSession, identity
from nexus_tab_installer.core.session_library import (ArchiveIndex, ModCollection, list_sessions, place_archive, record_archive,
                                                      remove_archive, remove_empty_sessions, remove_session)
from nexus_tab_installer.core.requirements import SURE_LIMIT, Present, as_item, assess, overview
from nexus_tab_installer.core.updates import find_updates, load_found, save_found
from nexus_tab_installer.core.disk_space import shortages
from nexus_tab_installer.core.session_migration import migrate_old_layout
from nexus_tab_installer.core.install_plan import ModPlan, TARGETS, validate_target
from nexus_tab_installer.downloads.transfer import download, DownloadCancelled, DownloadError, LinkRejected, RateLimited, safe_name
from nexus_tab_installer.downloads.export import export_archive
from nexus_tab_installer.downloads.library import file_directory
from nexus_tab_installer.downloads.browser_watcher import BrowserDownloadWatcher, browser_download_folders
from nexus_tab_installer.downloads.archives import inspect_archive, stage_archive
from nexus_tab_installer.downloads.queue import DownloadQueue
from nexus_tab_installer.browser.import_server import server_factory, ImportServer, PORT
from nexus_tab_installer.browser.auto_free import AutoFreeBatch, file_page_url, REQUIRED_EXTENSION
from nexus_tab_installer.browser.pacing import CUSTOM, CUSTOM_DEFAULTS, custom_preset, DownloadPacer, PRESETS, DEFAULT_PRESET, WAIT_TEXT, clock_text, duration_text
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
from nexus_tab_installer.browser.pairing import PairingRequests
from nexus_tab_installer.browser.browser_setup import BROWSERS, get_browser_setup
from nexus_tab_installer.platform.browser_setup import open_extension_manager
from nexus_tab_installer.browser.windows_tabs import scan_open_windows, save_scan_diagnostics
from nexus_tab_installer.nexus.nxm_inbox import pending_nxm, acknowledge_nxm
from nexus_tab_installer.nexus.free_workflow import FreeQueue
from nexus_tab_installer.platform.nxm_windows import register_nxm
from nexus_tab_installer.platform.browser_window import open_nexus_window
from nexus_tab_installer.platform.keep_awake import set_keep_awake
from nexus_tab_installer.integrations.destinations import resolve_destination, find_mo2_instances, DestinationError
from nexus_tab_installer.integrations.mo2_install import instance_info, available_profiles, install_simple, MO2InstallError
from nexus_tab_installer.integrations.discovery import discover_mo2, discover_vortex
from nexus_tab_installer.integrations import vortex as vortex_link
from nexus_tab_installer.core.file_kinds import is_plain_file
from nexus_tab_installer.integrations.managed_install import InstallError, deploy, uninstall
from nexus_tab_installer.integrations.delivery import deliver
from nexus_tab_installer.integrations.installed import installed_mods
from nexus_tab_installer.nexus.graphql import mod_files, mod_infos
from nexus_tab_installer.platform.notify import notify
from nexus_tab_installer.platform.recycle import to_recycle_bin

PATHS = storage_paths()
STATE_DIR = PATHS['base']
PACK_FILE = PATHS['pack']
CONFIG_FILE = PATHS['config']
FIRST_SNAPSHOT_WAIT = 20.0  # seconds to wait for a known extension before scanning Windows
RECENT_ARCHIVE_SECONDS = 7 * 86400  # a re-run reuses a mod's archive this fresh without asking the site
AUTO_SCAN = True  # show the mods of open tabs as soon as a browser connects
UPDATE_CHECK_SECONDS = 86400  # at most one automatic check for new versions a day
DISK_WATCH_TICKS = 100  # a look at the free room during a download, about every half a minute
DONE_STAGES = ('Готово', 'Передано Vortex', 'Установлен MO2', 'Требует MO2', 'Установлен в игру', 'Уже скачан ранее')


def load_config():
    return load_settings(CONFIG_FILE)


def store_browser_file(source, folder):
    """Move a file the browser saved into the folder of its session."""
    source = Path(source)
    if not source.is_file() or source.is_symlink() or source.stat().st_size <= 0:
        raise ValueError('Браузер сообщил о файле, которого нет на диске')
    if PATHS['base'].resolve() in source.resolve().parents:
        return source.resolve()
    return place_archive(source, folder)


def transfer_folder(game, mod_id, file_id):
    """An unfinished file waits here: a session folder only ever shows complete archives."""
    if file_id:
        return file_directory(PATHS['transfers'], game, mod_id, file_id)
    return file_directory(PATHS['transfers'], game, mod_id, 1).parent / 'unknown'


def main(page: ft.Page):
    page.title = 'Nexus Tab Installer · v1.0.0'
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = '#10121B'
    page.padding = 0
    page.window.width = 1160
    page.window.height = 780
    page.window.min_width = 850
    page.window.min_height = 580
    page.theme = ft.Theme(color_scheme_seed='#8267F7')

    ensure_layout()
    trace = TraceLog(STATE_DIR / 'logs')
    auto_scan = {'done': False}
    moved_archives = 0
    try:
        moved_archives = migrate_old_layout(PATHS['sessions'], PATHS['old_downloads'], PATHS['retired_sessions'])
        remove_empty_sessions(PATHS['sessions'])
        if moved_archives:
            trace.emit('library.migrated', count=moved_archives)
    except Exception as exc:
        trace.emit_exception(exc, area='startup')
    trace.emit('app.started')
    # Python UI and worker exceptions: store safe types and code locations,
    # never the exception message (which might contain tokens or local paths).
    import sys
    def _trace_uncaught(t, exc, tb):
        try: trace.emit_exception(exc, area='ui')
        except Exception: pass
        sys.__excepthook__(t, exc, tb)
    sys.excepthook = _trace_uncaught
    previous_thread_hook = threading.excepthook
    def _trace_thread(args):
        try: trace.emit_exception(args.exc_value, area='thread', event='app.thread_exception')
        except Exception: pass
        previous_thread_hook(args)
    threading.excepthook = _trace_thread
    first_start = not CONFIG_FILE.is_file()
    config = load_config()
    # Whoever has used the program so far has read it in Russian: only a new person gets the language of Windows.
    screen = ScreenLanguage(config.get('language') or (system_language() if first_start else 'ru'))
    screen_roots = []

    def redraw():
        """All text reaches the window through here, and so in the chosen language."""
        screen.apply(*screen_roots)
        page.update()

    token = config.get('browser_token') or secrets.token_urlsafe(24)
    # Browser code must not change each launch: otherwise connected extensions
    # silently stop working after a restart. Persist it inside portable data/.
    if config.get('browser_token') != token:
        save_settings(CONFIG_FILE, {**config, 'browser_token': token, 'language': screen.language})
    incoming = queue.Queue()
    snapshots = TabSnapshots()
    pairing = PairingRequests(token=token)
    entries = []
    plan = ModPlan()
    mod_info = {}  # (game, mod_id) -> what Nexus says about a mod: name, version, size, requirements
    info_state = {'error': '', 'hidden': None, 'asked': set(), 'downloaded': set(), 'installed': {},
                  # What lies outside Nexus cannot be checked: once the person has hidden a reminder, it does not come back.
                  'known_elsewhere': {name for name in str(config.get('known_elsewhere') or '').split('\n') if name}}
    requirement_open = set()  # mods whose list of requirements is unfolded
    row_memory = {}  # (game, mod_id) -> (what a row shows, its controls): a long list redraws only what changed
    file_preferences = {}  # (game, mod_id) -> FileChoice, kept across re-imports
    file_catalogs = {}  # API responses held in memory, never API credentials
    file_expanded = set()
    file_loading = set()
    file_errors = {}
    job_routes = {}
    installing_jobs = set()
    settled_jobs = set()  # download jobs whose final state was already handled
    premium = {'value': None}
    active = {'tab': 'start'}
    free_queue = FreeQueue(PATHS['free'])
    auto_free = AutoFreeBatch(trace=trace)
    plan.default_target = config.get('default_target') if config.get('default_target') in TARGETS else 'library'
    saved_pace = config.get('download_pace')
    if saved_pace != CUSTOM and saved_pace not in PRESETS:
        saved_pace = DEFAULT_PRESET

    def pace_field(label, name, default):
        return ft.TextField(label=label, value=str(config.get(f'pace_{name}', default)), width=240,
                            input_filter=ft.NumbersOnlyInputFilter(), keyboard_type=ft.KeyboardType.NUMBER)

    pace_gap = pace_field('Пауза между файлами, секунд', 'gap', CUSTOM_DEFAULTS['gap'])
    pace_batch = pace_field('Файлов до длинной паузы', 'batch', CUSTOM_DEFAULTS['batch'])
    pace_rest = pace_field('Длинная пауза, минут', 'rest', CUSTOM_DEFAULTS['rest_minutes'])
    pace_daily = pace_field('Файлов за сутки, не больше', 'daily', CUSTOM_DEFAULTS['daily'])
    pace_choice = ft.Dropdown(
        label='Темп скачивания через браузер',
        value=saved_pace,
        options=[*(ft.dropdown.Option(name, f'{preset.label}: файл раз в {preset.gap} секунд')
                   for name, preset in PRESETS.items()),
                 ft.dropdown.Option(CUSTOM, 'Свой: время выбираю сам')])

    def chosen_pace():
        if pace_choice.value == CUSTOM:
            return custom_preset(pace_gap.value, pace_batch.value, pace_rest.value, pace_daily.value)
        return pace_choice.value

    pacer = DownloadPacer(PATHS['pace'], preset=chosen_pace())
    auto_free.pacer = pacer
    pace_custom = ft.Column([ft.Row([pace_gap, pace_batch], spacing=10, wrap=True),
                             ft.Row([pace_rest, pace_daily], spacing=10, wrap=True),
                             ft.Text('Ноль выключает паузу или предел.', size=12, color='#AAB2C7')],
                            spacing=10, visible=saved_pace == CUSTOM)
    pace_warning = ft.Text('Свой темп вы выбираете на свой страх и риск: чем короче паузы, тем выше риск, что Nexus ограничит '
                           'скачивание или заблокирует аккаунт. Если сайт сам попросит подождать, программа подождёт и при таком темпе.',
                           size=12, color='#F2B86B', visible=saved_pace == CUSTOM)
    pace_hint = ft.Text('', size=12, color='#B5BBD1')
    language_choice = ft.Dropdown(label='Язык / Language', value=screen.language, width=260,
                                  options=[ft.dropdown.Option('ru', 'Русский'), ft.dropdown.Option('en', 'English')])
    notify_opt = ft.Checkbox(label='Уведомлять через Windows: загрузка закончилась или нужно ваше действие',
                             value=bool(config.get('notify_enabled', True)))
    auto_scan_opt = ft.Checkbox(label='Сразу показывать моды из открытых вкладок, когда браузер подключён',
                                value=bool(config.get('auto_scan', AUTO_SCAN)))
    updates_auto = ft.Checkbox(label='Проверять новые версии скачанных модов раз в день',
                               value=bool(config.get('updates_auto', True)))
    requirements_mode = ft.Dropdown(label='Требования модов', value=config.get('requirements_mode') or 'ask', width=420,
                                    options=[ft.dropdown.Option('ask', 'Показывать и спрашивать'),
                                             ft.dropdown.Option('auto', 'Сразу добавлять нужные моды в список'),
                                             ft.dropdown.Option('off', 'Не показывать')])
    awake = {'value': False}
    waiting_for_helper = {'value': False}
    browser_paired = {'value': bool(config.get('browser_paired'))}
    stall_warning = {'value': False}
    browser_download_folder = ft.TextField(
        label='Папка загрузок браузера (необязательно)',
        value=config.get('browser_download_folder', ''),
        hint_text='Оставьте пустой для стандартной папки Downloads', expand=True)
    browser_download_watcher = BrowserDownloadWatcher(
        browser_download_folders(browser_download_folder.value))
    browser_download_spec = {}  # (game, mod_id, file_id) -> verified API filename

    free_count = ft.Text(f'Ожидают подтверждения: {len(free_queue)}', size=13, color='#B9ADEB')
    status = ft.Text('Готово к поиску модов.', size=13, color='#9CA3B8')
    key = ft.TextField(label='Nexus Personal API Key', password=True, can_reveal_password=True,
                       hint_text='Вставьте ключ через Ctrl+V', value=config.get('api_key', '') if config.get('remember_api_key') else '', expand=True)
    remember_key = ft.Checkbox(label='Сохранять API-ключ в папке программы (нешифрованный файл)', value=bool(config.get('remember_api_key', False)))
    auto_free_opt = ft.Checkbox(label='Скачивать автоматически через браузер (Free-аккаунт или без API-ключа)',
                                value=bool(config.get('auto_free_enabled', True)))
    saved_folder = saved_path(config.get('folder'))
    if saved_folder and Path(saved_folder) == PATHS['old_downloads']:
        saved_folder = None  # the place of older versions, which is gone now
    folder = ft.TextField(label='Папка для скачанных модов',
                          value=saved_folder or str(PATHS['downloads']),
                          expand=True)
    destination_mode = ft.Dropdown(label='Назначение архивов', value=config.get('destination_mode', 'folder'),
        options=[ft.dropdown.Option('folder', 'Обычная папка'),
                 ft.dropdown.Option('mo2', 'Mod Organizer 2 — Downloads'),
                 ft.dropdown.Option('vortex', 'Vortex — выбранная папка загрузок')])
    mo2_ini = ft.TextField(label='Папка MO2 или путь к ModOrganizer.ini', value=config.get('mo2_ini', ''),
        hint_text=r'D:\Games\MO2 (или ModOrganizer.ini)', expand=True)
    destination_hint = ft.Text('Для MO2 выберите ModOrganizer.ini нужного экземпляра; файлы попадут в его Downloads. Установка внутри MO2 выполняется отдельно.', size=12, color='#B5BBD1')
    detected_managers = ft.Column(spacing=5)
    # A person with one MO2 and one Vortex should not have to type their paths.
    if not mo2_ini.value:
        try:
            instances = find_mo2_instances()
            if len(instances) == 1:
                mo2_ini.value = str(instances[0])
        except OSError:
            pass
    try:
        initial_profiles = available_profiles(mo2_ini.value) if mo2_ini.value else []
    except (OSError, ValueError):
        initial_profiles = []
    remembered_profile = config.get('mo2_profile') or ''
    if remembered_profile not in initial_profiles and mo2_ini.value:
        try:
            remembered_profile = instance_info(mo2_ini.value)['profile']
        except (OSError, ValueError):
            pass
    mo2_profile = ft.Dropdown(label='Профиль MO2',
        options=[ft.dropdown.Option(p) for p in initial_profiles],
        value=remembered_profile if remembered_profile in initial_profiles else (initial_profiles[0] if len(initial_profiles) == 1 else None))
    mo2_name = ft.TextField(label='Название мода в MO2', value='Новый мод')
    install_stage = ft.TextField(label='Подготовленная папка staging', expand=True)
    install_stage_custom = ft.TextField(label='Папка staging для ручной установки', expand=True)
    install_target = ft.TextField(label='Отдельная папка назначения для установки (вручную)', expand=True)
    install_id_field = ft.TextField(label='ID установки из журнала', expand=True)
    install_log = PATHS['managed']
    managed_list = ft.Column(spacing=6)
    managed_status = ft.Text('Установка доступна только для явно выбранной папки. MO2/Vortex управляются отдельно.', size=12, color='#B5BBD1')
    pack_path = ft.TextField(label='Путь к сборке JSON', value=saved_path(config.get('pack_path')) or str(PACK_FILE), expand=True)
    link_field = ft.TextField(hint_text='Вставьте ссылки Nexus Mods — Ctrl+V',
                              multiline=True, min_lines=2, max_lines=4, expand=True,
                              border_radius=12, bgcolor='#151A2A',
                              border_color='#353A53', focused_border_color='#9D86FF',
                              cursor_color='#AD9CFF', content_padding=16)
    nxm_field = ft.TextField(label='NXM-ссылка от Mod Manager Download',
                            hint_text='nxm://... (ссылка с сайта Nexus)', expand=True)
    library_summary = ft.Text('', size=13, color='#9CA3B8')
    active_jobs = ft.Column(spacing=6)
    batch_text = ft.Text('', size=13, color='#E4E1F5', expand=True)
    batch_bar = ft.ProgressBar(value=0, color='#8B70FF', bgcolor='#323752')
    history_area = ft.Column(spacing=6, visible=False)
    main_area = ft.Column(expand=True, spacing=14)
    browser_status = ft.Text('Ожидаем расширение браузера…', color='#B9ADEB', size=12)
    browser_guide_status = ft.Text('Подключение ещё не проверено.', color='#C3B7E9', size=13)
    helper_status = ft.Text('Автозагрузка: проверка подключения…', color='#EDBC81', size=12)
    helper_state = {'value': ''}
    side_browser_dot = ft.Icon(ft.Icons.CIRCLE, size=11, color=MUTED)
    side_browser_text = ft.Text('', size=12, color='#C9CEDF')
    browser_pair_requests = ft.Column(spacing=8)
    # Pairing must be actionable even when the onboarding panel is collapsed
    # or the user is looking at a different screen.
    pairing_banner_content = ft.Column(spacing=8)
    pairing_banner = ft.Container(visible=False, padding=14, border_radius=13,
        bgcolor='#302B48', content=pairing_banner_content)
    server_notice = ft.Container(visible=False, padding=12, border_radius=12,
        bgcolor='#4A2B31', content=ft.Text('Сервер подключения браузера недоступен.', color='#FFE1D9'))
    jobs = None
    workflow = {'current': None, 'running': False}
    collection = {'loose': None}  # for files that arrive outside any download run
    workflow_rows = ft.Column(spacing=8)
    workflow_summary = ft.Text('Откройте вкладки с модами в браузере и нажмите «Найти моды» или вставьте ссылки.',
                               color='#B3BBD0', size=13)
    batch_progress = ft.ProgressBar(value=0, visible=False, color='#8B70FF', bgcolor='#323752')
    setup_opened = {'value': False}  # the person opened the browser setup panel themselves
    workflow_count = ft.Text('0 найденных модов', size=12, color='#A5ABC5')
    start_button = ft.Button('Скачать моды из открытых вкладок', icon=ft.Icons.DOWNLOAD)
    cancel_button = ft.Button('Отмена', icon=ft.Icons.CLOSE, disabled=True, visible=False)
    download_selected_button = ft.Button('Скачать выбранные', icon=ft.Icons.DOWNLOAD, disabled=True, bgcolor='#7C63F0', color='#FFFFFF')
    resume_button = ft.Button('Продолжить загрузку', icon=ft.Icons.PLAY_ARROW, visible=False)
    # What the person has to do in the browser: login or CAPTCHA are never done by the program.
    attention_text = ft.Text('', size=14, weight=ft.FontWeight.W_600, color='#FFE6B0')
    attention_banner = ft.Container(visible=False, bgcolor='#4A3A1C', border_radius=12, padding=14,
        border=ft.Border.all(1, '#8A6A2C'),
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color='#FFC766'),
                    ft.Container(expand=True, content=attention_text)], spacing=12),
            resume_button,
        ], spacing=10))
    premium_notice = ft.Container(visible=False, bgcolor='#2E2A1C', border_radius=12, padding=12,
        border=ft.Border.all(1, '#6B5A2C'),
        content=ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color='#F3C98B', size=20),
                        ft.Container(expand=True, content=ft.Text(PREMIUM_WARNING, size=13, color='#F3DDB4'))], spacing=10))
    disk_text = ft.Text('', size=13, color='#F3DDB4')
    disk_state = {'told': False}

    def hide_disk_notice(e=None):
        disk_notice.visible = False
        redraw()

    disk_notice = notice(disk_text, ft.TextButton('Скрыть', on_click=hide_disk_notice), visible=False)
    requirements_text = ft.Text('', size=13, color='#F3DDB4')
    requirements_add = ft.Button('Добавить в список', icon=ft.Icons.ADD)
    requirements_fetch = ft.Button('Докачать сейчас', icon=ft.Icons.DOWNLOAD)
    requirements_retry = ft.TextButton('Повторить', icon=ft.Icons.REFRESH, visible=False)
    requirements_hide = ft.TextButton('Скрыть')
    requirements_banner = notice(requirements_text, requirements_add, requirements_fetch, requirements_retry, requirements_hide, visible=False)
    select_all_button = ft.TextButton('Выбрать все')
    clear_all_button = ft.TextButton('Снять выбор')
    game_folder = ft.TextField(label='Папка Data игры (для простой прямой установки)',
                               value=config.get('game_folder',''), expand=True)
    vortex_exe = ft.TextField(label='Vortex.exe', value=config.get('vortex_exe') or str(vortex_link.find_vortex() or ''),
                              hint_text=r'C:\Program Files\Black Tree Gaming Ltd\Vortex\Vortex.exe', expand=True)
    manager_status = ft.Text('', size=12, color='#B7C2D9')
    vortex_folder = ft.TextField(label='Папка загрузок Vortex (если пусто, стандартная)',
                                 value=config.get('vortex_folder') or (config.get('folder','') if config.get('destination_mode') == 'vortex' else ''), expand=True)
    destination_note = ft.Text('MO2: установка простых модов в выбранный профиль, MO2 должен быть закрыт. '
                               'Vortex: архив передаётся в загрузки, не активируется автоматически. '
                               'В игру: только простые архивы, с резервными копиями.',
                               size=12, color='#B2B9D2')


    def target_options():
        return [ft.dropdown.Option('library', 'Только скачать'),
                ft.dropdown.Option('mo2', 'В Mod Organizer 2'),
                ft.dropdown.Option('vortex', 'В Vortex'),
                ft.dropdown.Option('game', 'В папку Data игры (с копией)')]

    def open_setup_if_needed(target):
        """The paths are asked for only when something is really missing."""
        try:
            validate_target(target, mo2_ini=mo2_ini.value or '', mo2_profile=mo2_profile.value or '',
                            vortex_folder=vortex_folder.value or '', game_folder=game_folder.value or '',
                            vortex_exe=vortex_exe.value or '')
        except (ValueError, OSError):
            destination_options_panel.visible = True
            describe_managers()

    def set_target_for_all(e=None):
        plan.set_all_targets(target_for_all.value)
        save_choices()
        persist_config(silent=True)
        open_setup_if_needed(target_for_all.value)
        refresh_workflow()

    target_for_all = ft.Dropdown(label='Куда для всех модов', width=250, dense=True, text_size=13,
                                 content_padding=ft.Padding.symmetric(horizontal=10, vertical=2), options=target_options(),
                                 value=plan.default_target, on_select=set_target_for_all)

    def update_workflow_controls():
        start_button.disabled = workflow['running']
        cancel_button.disabled = not workflow['running']
        cancel_button.visible = workflow['running']
        download_selected_button.disabled = workflow['running'] or not bool(plan.chosen())
        # A colour of its own would hide that the button is off.
        download_selected_button.bgcolor = None if download_selected_button.disabled else '#7C63F0'
        download_selected_button.color = None if download_selected_button.disabled else '#FFFFFF'
        select_all_button.disabled = workflow['running']
        clear_all_button.disabled = workflow['running']
        resume_button.visible = workflow['running'] and auto_free.state()['paused']

    def present_now():
        return Present({identity(item) for item in plan.items}, info_state['downloaded'], info_state['installed'])

    def needs_of(item, here):
        info = mod_info.get(identity(item))
        return assess(info, here) if info and requirements_mode.value != 'off' else []

    def requirement_overview():
        return overview([mod_info[identity(item)] for item, _ in plan.chosen() if identity(item) in mod_info], present_now())

    def names_of(needs):
        names = ', '.join(need.name for need in needs[:4])
        return names + (f' и ещё {len(needs) - 4}' if len(needs) > 4 else '')

    def refresh_requirements():
        '''One warning for the whole list. It offers and never insists: downloads go on without an answer.'''
        problem = info_state['error'] and requirements_mode.value != 'off'
        found = requirement_overview() if requirements_mode.value != 'off' else None
        missing = found.required if found and info_state['hidden'] != found.signature else []
        outside = [need for need in found.elsewhere if need.name.casefold() not in info_state['known_elsewhere']] if found else []
        worth_telling = bool(missing or outside)
        requirements_banner.visible = bool(problem or worth_telling)
        requirements_retry.visible = bool(problem)
        requirements_add.visible = requirements_fetch.visible = bool(missing)
        requirements_fetch.disabled = workflow['running']
        lines = []
        if problem:
            lines.append(f"Не удалось узнать требования модов: {info_state['error']}.")
            lines.append('Они указаны на странице каждого мода. Скачивать можно и так.')
        if worth_telling:
            if missing:
                lines.append(f'Моды из списка требуют других модов, которых у вас нет ({len(missing)}): {names_of(missing)}.')
            if outside:
                lines.append(f'Вне Nexus, проверьте сами: {names_of(outside)}.')
            lines.append('Это подсказка: скачать можно и без них.')
        requirements_text.value = '\n'.join(lines)

    def stage_color(stage):
        if 'Ошибка' in stage:
            return BAD
        return GOOD if stage in DONE_STAGES else ACCENT

    def file_panel(item, pref):
        ident = identity(item)
        panel = []
        if ident in file_loading:
            panel.append(ft.Text('Получаем список файлов Nexus…', color='#B9ADEB', size=12))
        elif ident in file_errors:
            panel.append(ft.Text(file_errors[ident], color='#F1B57E', size=12))
        elif ident in file_catalogs:
            records = file_catalogs[ident]
            try:
                chosen_ids = {f['file_id'] for f in pref.resolve(records)} if pref.mode == 'auto' else set(pref.file_ids)
            except ValueError as exc:
                chosen_ids = set(pref.file_ids)
                panel.append(ft.Text(str(exc), size=11, color='#F1B57E'))
            panel.append(ft.Row([
                ft.Text('Файлы и версии', size=14, weight=ft.FontWeight.BOLD),
                ft.TextButton('Сбросить: последняя MAIN', on_click=lambda e, ref=item: reset_files(ref)),
                ft.TextButton('Обновить список', on_click=lambda e, ref=item: reload_files(ref)),
            ], wrap=True))
            if not records:
                panel.append(ft.Text('Nexus не вернул файлов для этого мода.', color='#AAB2C7'))
            by_category = {}
            for record in records:
                by_category.setdefault(record['category_name'], []).append(record)
            for cat, group in by_category.items():
                panel.append(ft.Text(f'{CATEGORY_LABELS.get(cat, cat)} · {len(group)}',
                                     size=12, color='#C5B7F4', weight=ft.FontWeight.W_600))
                for record in group:
                    fid = record['file_id']
                    def choose_file(e, ref=item, selected_file=fid):
                        choice_file = file_preferences.setdefault(identity(ref), FileChoice())
                        try:
                            choice_file.toggle(selected_file, bool(e.control.value), file_catalogs[identity(ref)])
                            save_choices()
                        except ValueError as exc:
                            tell(str(exc))
                        refresh_workflow()
                    mb = (record['size_kb'] / 1024) if record['size_kb'] else 0
                    detail_file = f"Версия {record['version']} · #{fid}" + (f' · {mb:.1f} МБ' if mb else '')
                    panel.append(ft.Container(
                        bgcolor='#192032', padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                        border_radius=9,
                        content=ft.Row([
                            ft.Checkbox(value=fid in chosen_ids, disabled=workflow['running'] or not record['available'],
                                        on_change=choose_file, active_color='#967AFF'),
                            ft.Column([
                                ft.Text(record['name'], size=12, color='#F0EEFF', max_lines=2),
                                ft.Text(record['file_name'], color='#AAB1C8', size=11, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(detail_file + (' · архивная/удалённая версия: Nexus не разрешает скачивать' if not record['available'] else ''),
                                        color='#9CA6BE', size=11)
                            ], expand=True, spacing=3),
                        ], spacing=5)))
            main_count = sum(1 for record in records if record['file_id'] in chosen_ids and
                             record['category_name'] in ('MAIN', 'OLD_VERSION'))
            if main_count > 1:
                panel.append(ft.Text('Внимание: выбрано несколько основных вариантов. '
                                     'Проверьте их совместимость перед установкой.',
                                     color='#F1B57E', size=11))
            panel.append(ft.Text('Отмеченные файлы скачиваются отдельно. '
                                 'Изменения сохраняются после перезапуска.',
                                 size=11, color='#9DA9C0'))
        return panel

    def open_files(ref):
        if workflow['running']:
            tell('Изменить файлы можно перед началом следующей загрузки.')
            return
        ref_id = identity(ref)
        if ref_id in file_expanded:
            file_expanded.remove(ref_id)
            refresh_workflow()
            return
        file_expanded.add(ref_id)
        if ref_id in file_catalogs:
            refresh_workflow()
            return
        if not (key.value or '').strip():
            file_errors[ref_id] = 'Для выбора версий сначала введите Nexus API Key в Настройках.'
            refresh_workflow()
            return
        if ref_id in file_loading:
            return
        file_loading.add(ref_id)
        refresh_workflow()
        def load_files(ref=ref):
            try:
                records = NexusClient(key.value.strip()).files(ref['game'], ref['mod_id'])
                return ('file_catalog', ref, catalog(records), '')
            except Exception as exc:
                return ('file_catalog', ref, [], str(exc))
        background(load_files)

    def reload_files(ref):
        if workflow['running']:
            return
        file_catalogs.pop(identity(ref), None)
        file_errors.pop(identity(ref), None)
        file_expanded.discard(identity(ref))
        open_files(ref)

    def reset_files(ref):
        file_preferences.setdefault(identity(ref), FileChoice()).reset()
        save_choices()
        refresh_workflow()

    def toggle_requirements(ident):
        requirement_open.symmetric_difference_update({ident})
        refresh_workflow()

    def mod_row(item, states, job_ids, file_jobs, here):
        ident = identity(item)
        choice = plan.choices[ident]
        stage, detail = states.get(ident, ('Не выбран' if not choice.selected else 'Готов к загрузке', ''))
        if ident in job_ids and jobs is not None:
            job = jobs.items.get(job_ids[ident])
            if job:
                jobstage = {'queued':'В очереди','downloading':'Скачивается','finished':'Архив скачан',
                         'cancelled':'Отменено','failed':'Ошибка','interrupted':'Прервано'}.get(job.state,stage)
                if stage not in ('Готово', 'Передано Vortex', 'Установлен MO2', 'Требует MO2', 'Установлен в игру', 'Ошибка установки', 'Устанавливается'):
                    stage = jobstage
                if stage not in ('Готово', 'Передано Vortex', 'Установлен MO2', 'Требует MO2', 'Установлен в игру', 'Ошибка установки', 'Устанавливается'):
                    if job.total: detail = f'{job.progress * 100 // job.total} % · {job.filename}'
                    elif job.error: detail = job.error[:160]
        job_ids_for_mod = [jid for (g, mid, _fid), jid in file_jobs.items() if (g, mid) == ident]
        if len(job_ids_for_mod) > 1:
            associated = [jobs.items.get(jid) for jid in job_ids_for_mod]
            done = sum(1 for j in associated if j and j.state == 'finished')
            failed = sum(1 for j in associated if j and j.state in ('failed', 'cancelled'))
            stage = f'Файлы: {done}/{len(associated)} скачано'
            if failed:
                stage += f' · ошибок: {failed}'
            active_file = next((j.filename for j in associated if j and j.state == 'downloading'), '')
            detail = active_file or detail
        pref = file_preferences.setdefault(ident, FileChoice())
        explicit = item.get('file_ids') or []
        if pref.mode == 'manual':
            file_label = f'Выбрано файлов: {len(pref.file_ids)}'
        elif explicit:
            file_label = 'Файл из вкладки/ссылки: ' + ', '.join(f'#{fid}' for fid in explicit)
        else:
            file_label = 'Основной файл, последняя версия'
        info = mod_info.get(ident)
        facts = [info.game_name if info and info.game_name else item['game'], f"#{item['mod_id']}"]
        if info and info.version:
            facts.append(f'версия {info.version}')
        if info and info.size_kb:
            facts.append(size_text(info.size_kb * 1024))
        needs = needs_of(item, here)
        files_open, needs_open = ident in file_expanded, ident in requirement_open and bool(needs)
        shown = (id(item), item.get('title'), choice.selected, choice.target, stage, detail, file_label, workflow['running'], tuple(facts),
                 tuple((need.name, need.state, need.optional, need.where) for need in needs))
        remembered = row_memory.get(ident)
        if remembered and remembered[0] == shown and not files_open and not needs_open:
            return remembered[1]
        def toggle(e, ref=item):
            plan.update(ref, selected=bool(e.control.value))
            save_choices()
            refresh_workflow()
        def set_destination(e, ref=item):
            plan.update(ref, target=e.control.value)
            save_choices()
            open_setup_if_needed(e.control.value)
            refresh_workflow()
        badge = requirement_chip(needs, lambda e, chosen=ident: toggle_requirements(chosen))
        extra = []
        if files_open:
            extra += file_panel(item, pref)
        if needs_open:
            extra.append(requirement_panel(needs, on_add=lambda need: add_requirements([need]), on_page=webbrowser.open))
        row = ft.Container(
            bgcolor='#1B2233' if choice.selected else '#171B29', padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border=ft.Border.all(1, '#514577' if choice.selected else '#272D42'),
            border_radius=12,
            content=ft.Column([
                ft.Row([
                    ft.Checkbox(value=choice.selected, on_change=toggle, active_color='#9B82FF'),
                    ft.Column([
                        ft.Text(clean_mod_title(item.get('title'), item['game'], item['mod_id']),
                                size=14, color='#F0EEFF', weight=ft.FontWeight.W_600, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(' · '.join(facts), size=11, color='#A6AEC2', max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], expand=True, spacing=1),
                    *([badge] if badge else []),
                    ft.IconButton(ft.Icons.OPEN_IN_NEW, icon_size=18, icon_color='#B7B5D7', tooltip='Открыть страницу мода',
                                  visual_density=ft.VisualDensity.COMPACT,
                                  on_click=lambda e, url=item['url']: webbrowser.open(url)),
                    ft.Dropdown(label='Куда установить', value=choice.target, width=215, dense=True, text_size=12,
                                content_padding=ft.Padding.symmetric(horizontal=10, vertical=2),
                                options=target_options(), on_select=set_destination),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.TextButton('Файлы / версии', icon=ft.Icons.FOLDER_OPEN, disabled=workflow['running'],
                                  style=ft.ButtonStyle(visual_density=ft.VisualDensity.COMPACT, padding=ft.Padding.symmetric(horizontal=6, vertical=0)),
                                  on_click=lambda e, ref=item: open_files(ref)),
                    ft.Text(file_label, size=12, color='#B9ACDD'),
                    ft.Text(stage, size=12, color=stage_color(stage)),
                    ft.Text(detail, size=11, color='#9AA4BB', max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                *extra,
            ], spacing=0))
        row_memory[ident] = (shown, row)
        return row

    def refresh_workflow():
        session = workflow['current']
        workflow_count.value = f'{len(plan.items)} найденных модов · {len(plan.chosen())} выбрано'
        refresh_requirements()
        if not plan.items:
            workflow_rows.controls[:] = [ft.Container(
                bgcolor='#171D2B', padding=20, border_radius=14,
                content=ft.Column([
                    ft.Text('Нажмите «Найти моды», затем выберите нужные и назначение для каждого.', color='#A9B2C9'),
                    ft.Text('Найти можно моды из открытых вкладок браузера. А ещё можно вставить ссылки на них, сразу много.', color='#8F99B2', size=12),
                ], spacing=6))]
        else:
            states, job_ids = session.snapshot() if session else ({}, {})
            file_jobs = session.file_jobs_snapshot() if session else {}
            here = present_now()
            workflow_rows.controls[:] = [mod_row(item, states, job_ids, file_jobs, here) for item in plan.items]
        update_workflow_controls()
        redraw()

    def export_diagnostics(e=None):
        trace.emit('app.export_requested')
        try:
            archive = trace.export()
            import os, subprocess, sys
            if sys.platform == 'win32':
                os.startfile(str(archive.parent))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(archive.parent)])
            else:
                subprocess.Popen(['xdg-open', str(archive.parent)])
            tell('Журнал сохранён: data/logs/diagnostics-for-support.zip. Пришлите этот ZIP для анализа.')
        except Exception as exc:
            trace.emit_exception(exc, area='export')
            tell('Не удалось создать или открыть отчёт. Откройте раздел «Журнал» для подробностей.')

    def tell(message):
        status.value = str(message)[:650]
        redraw()

    def announce(title, message):
        """For the person who is not looking at the window just now."""
        if notify_opt.value:
            notify(screen.text(title), screen.text(str(message)), page.title)

    def persist():
        PACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_pack(PACK_FILE, entries)

    # Each launch starts with an empty working list. Earlier downloads stay on the «Загрузки» tab.
    # Rows of the list are built only from what plan holds: a new list starts from nothing.
    plan.replace(entries)
    choice_file = STATE_DIR / 'config' / 'mod-choices.json'
    def save_choices():
        try:
            saved_choices = json.loads(choice_file.read_text(encoding='utf-8'))
            content = saved_choices if isinstance(saved_choices, dict) else {}
        except (OSError, ValueError):
            content = {}
        # Keep settings for mods not included in the current browser scan.
        content.update({f'{g}:{mid}': {'selected': choice.selected, 'target': choice.target,
                                      'files': file_preferences.get((g, mid), FileChoice()).as_dict()}
                        for (g, mid), choice in plan.choices.items()})
        choice_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = choice_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(choice_file)

    def restore_choices():
        try:
            saved = json.loads(choice_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        for item in plan.items:
            choice = saved.get(f"{item['game']}:{item['mod_id']}", {})
            if isinstance(choice, dict):
                try:
                    # A destination chosen for the whole list outweighs the one remembered for a mod.
                    plan.update(item, selected=choice.get('selected'),
                                target=choice.get('target') if plan.default_target == 'library' else None)
                    file_preferences[identity(item)] = FileChoice.from_dict(choice.get('files'))
                except ValueError:
                    continue
    restore_choices()

    def add(items):
        """Each import replaces both UI lists, without touching download history."""
        if workflow['running']:
            cancel_workflow()
        entries[:] = plan.replace(items)
        trace.emit('ui.scan_results', count=len(entries))
        restore_choices()
        save_choices()
        row_memory.clear()
        persist()
        look_up_mods(plan.items)
        fold_setup_for_list()
        workflow['current'] = WorkflowSession(entries)
        workflow['running'] = False
        workflow_summary.value = f'Добавлено модов: {len(entries)}. Снимите лишние галочки и нажмите «Скачать выбранные».'
        refresh_workflow()
        tell(f'Новый список: {len(entries)} модов. История и скачанные файлы сохранены.')

    def add_text(e=None):
        links = extract_mod_links(link_field.value or '')
        if not links:
            tell('Не найдены страницы модов Nexus. Скопируйте URL из адресной строки браузера.')
            return
        add([{'url': u} for u in links])
        link_field.value = ''
        import_options.visible = False  # the list below is what matters now
        redraw()

    def fill_titles(items):
        """A pasted link has no mod name. With an API key, ask Nexus for the names in the background."""
        apikey = (key.value or '').strip()
        missing = [item for item in items
                   if clean_mod_title(item.get('title'), item['game'], item['mod_id']) == f"Мод #{item['mod_id']}"]
        if not apikey or not missing:
            return
        def work():
            try:
                client = NexusClient(apikey)
                for item in missing:
                    if not any(item is current for current in plan.items):
                        break  # the list was replaced meanwhile
                    item['title'] = str(client.mod(item['game'], item['mod_id']).get('name') or '')[:160]
                    incoming.put(('workflow_refresh',))
            except Exception:
                pass  # only a nicer name: the number stays and nothing else depends on it
            return ('titles_filled',)
        background(work)

    def fold_setup_for_list():
        if plan.items and not setup_opened['value']:
            browser_setup_panel.visible = False

    def look_up_mods(items):
        '''Names, sizes and requirements of the whole list in one request, no key needed. Only a convenience.'''
        wanted = [identity(item) for item in items if identity(item) not in mod_info and identity(item) not in info_state['asked']]
        if not wanted:
            return
        info_state['asked'].update(wanted)
        def work():
            try:
                return ('mod_info', mod_infos(wanted), '', wanted)
            except Exception as exc:  # whatever happens here, downloads do not depend on it
                return ('mod_info', {}, str(exc)[:160], wanted)
        background(work)

    def retry_look_up(e=None):
        info_state['error'] = ''
        look_up_mods(plan.items)
        refresh_workflow()

    def refresh_present():
        '''What is already here, off the window's thread: it reads folders of other programs.'''
        ini, vortex_root = mo2_ini.value or '', vortex_folder.value or ''
        def work():
            downloaded = {(archive.game, archive.mod_id) for session in list_sessions(PATHS['sessions'])
                          for archive in session.archives if archive.game}
            return ('present', downloaded, installed_mods(ini, vortex_root))
        background(work)

    def requirement_targets():
        '''A requirement goes where the mod that needs it goes.'''
        targets = {}
        here = present_now()
        for item, target in plan.chosen():
            info = mod_info.get(identity(item))
            for need in (assess(info, here) if info else ()):
                targets.setdefault(need.key, target)
        return targets

    def add_requirements(needs):
        '''Into the list as it stands: nothing else in it changes, and neither does a running download.'''
        fresh = plan.append([as_item(need) for need in needs], requirement_targets())
        if fresh:
            entries[:] = plan.items
            persist()
            save_choices()
            look_up_mods(fresh)
            tell(f"Добавлено в список: {plural(len(fresh), 'мод', 'мода', 'модов')}.")
        refresh_workflow()
        return fresh

    def add_missing_requirements(e=None):
        add_requirements(requirement_overview().required)

    def run_outside_list(items, targets):
        '''A download of just these mods. Where a destination is not set up, the file simply stays in its session.'''
        valid, choices = [], {}
        for item in items:
            target = targets.get(identity(item), plan.default_target)
            try:
                validate_target(target, mo2_ini=mo2_ini.value or '', mo2_profile=mo2_profile.value or '',
                                vortex_folder=vortex_folder.value or '', game_folder=game_folder.value or '',
                                vortex_exe=vortex_exe.value or '')
            except (ValueError, OSError):
                target = 'library'
            valid.append(item)
            choices[identity(item)] = target
        persist_config(silent=True)
        launch_batch(valid, choices, pinned=True)

    def fetch_requirements(e=None):
        '''"Докачка": what the list is missing, as a download of its own.'''
        if workflow['running']:
            tell('Сейчас идёт загрузка: докачать можно, когда она закончится.')
            return
        needs = requirement_overview().required
        if not needs or not ready_to_download():
            return
        targets = requirement_targets()
        add_requirements(needs)
        wanted = {need.key for need in needs}
        trace.emit('ui.action', step='requirements')
        run_outside_list([item for item in plan.items if identity(item) in wanted], targets)

    def hide_requirements(e=None):
        found = requirement_overview()
        info_state['hidden'] = found.signature
        info_state['known_elsewhere'].update(need.name.casefold() for need in found.elsewhere)
        persist_config(silent=True)
        refresh_workflow()

    requirements_add.on_click = add_missing_requirements
    requirements_fetch.on_click = fetch_requirements
    requirements_retry.on_click = retry_look_up
    requirements_hide.on_click = hide_requirements

    def retry_job(jid):
        """A failed file goes through the same way as any download: a website link is short-lived."""
        old = jobs.items.get(jid)
        if old is None:
            return
        if workflow['running']:
            tell('Сейчас идёт другая загрузка. Повторите этот файл, когда она закончится.')
            return
        if not ready_to_download():
            return
        ident = (old.game, old.mod_id)
        listed = next((item for item in plan.items if identity(item) == ident), None)
        item = {**(listed or {'game': old.game, 'mod_id': old.mod_id,
                              'url': f'https://www.nexusmods.com/{old.game}/mods/{old.mod_id}'}),
                'file_ids': [old.file_id] if old.file_id else []}
        target = plan.choices[ident].target if listed else 'library'
        trace.emit('ui.action', step='retry')
        persist_config(silent=True)
        launch_batch([item], {ident: target}, pinned=True)
        refresh_jobs()

    def refresh_jobs():
        """What is happening now goes first; everything else waits folded in the history."""
        items = list(jobs.items.values()) if jobs else []
        newest = {}
        for job in items:
            newest[(job.game, job.mod_id, job.file_id)] = job
        # A failure of this launch stays in sight until the file is tried again.
        now = [job for job in items if job.state in ('queued', 'downloading')]
        now += [job for job in newest.values() if job.state in RETRYABLE_STATES and job.id >= first_new_job]
        active_jobs.controls[:] = [job_row(job, on_retry=retry_job, on_cancel=cancel_job) for job in now]
        batch_card.visible = bool(workflow['running'])
        if workflow['running']:
            batch_text.value = source_text(workflow_summary)
            batch_bar.visible = batch_progress.visible
            batch_bar.value = batch_progress.value
        now_area.visible = bool(now) or batch_card.visible
        history_button.content = f'История загрузок ({len(items)})'
        history_area.controls[:] = [job_row(job, on_retry=retry_job, on_cancel=cancel_job) for job in reversed(items[-100:])]
        if not items:
            history_area.controls.append(ft.Text('Загрузок пока нет.', color='#9CA3B8'))
        redraw()

    def toggle_history(e=None):
        history_area.visible = not history_area.visible
        redraw()

    def cancel_job(jid):
        jobs.cancel(jid)
        refresh_jobs()

    batch_card = ft.Container(visible=False, bgcolor='#211F35', border_radius=10,
                              padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                              content=ft.Column([
                                  ft.Row([batch_text,
                                          ft.TextButton('Отменить загрузку', icon=ft.Icons.CLOSE,
                                                        on_click=lambda e: cancel_workflow())],
                                         spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                  batch_bar], spacing=6))
    now_area = ft.Column([batch_card, active_jobs], spacing=6, visible=False)
    history_button = ft.TextButton('История загрузок', icon=ft.Icons.HISTORY, on_click=toggle_history)

    def delivery_options():
        return {'staging_root': PATHS['staging'], 'journal': PATHS['managed'],
                'mo2_ini': mo2_ini.value or '', 'mo2_profile': mo2_profile.value or '',
                'vortex_folder': vortex_folder.value or '', 'game_folder': game_folder.value or '',
                'vortex_exe': vortex_exe.value or ''}

    def perform_download(job, progress):
        trace.emit('ui.transfer_started', mod_id=job.mod_id, file_id=job.file_id)
        # All remote API calls happen in the worker thread, never in the Flet UI.
        if job.url:
            # Signed CDN link that the website issued to the user's browser.
            url = job.url
        else:
            try:
                delay = pacer.api_delay()
                if delay and job.cancel.wait(delay):
                    raise DownloadCancelled('Загрузка отменена')
                client = NexusClient(job.apikey)
                if job.filename.endswith('.download'):
                    records = client.files(job.game, job.mod_id)
                    job.filename = next((f['file_name'] for f in records
                        if int(f.get('file_id', -1)) == job.file_id and f.get('file_name')), '')
                    if not job.filename:
                        raise NexusError('Не удалось получить имя выбранного файла Nexus. Загрузка отменена, чтобы не сохранить неправильный архив.')
                url = client.download_links(job.game, job.mod_id, job.file_id,
                                            job.key, job.expires, job.user_id)
            except NexusError:
                # The API refused: the website may still offer the file to the browser.
                if auto_free_opt.value:
                    job.fallback = 'browser'
                raise
        trace.emit('ui.transfer_link_acquired', mod_id=job.mod_id, file_id=job.file_id)
        try:
            saved = download(url, transfer_folder(job.game, job.mod_id, job.file_id), job.filename or None,
                             on_progress=progress, cancel=job.cancel,
                             file_identity=f'{job.game}-{job.mod_id}-{job.file_id}')
        except RateLimited:
            # The server keeps asking to slow down: hold the whole queue, then let
            # the browser fetch this file with a fresh link.
            pacer.cool_down()
            trace.emit('ui.rate_limited', mod_id=job.mod_id, file_id=job.file_id)
            if job.url:
                job.fallback = 'browser'
            raise
        except LinkRejected:
            if job.url:
                job.fallback = 'browser'
            raise
        finally:
            job.url = None  # short-lived link: never kept after the attempt
        transfer = saved.parent
        saved = place_archive(saved, job.directory)
        job.filename = saved.name
        try:
            transfer.rmdir()
            transfer.parent.rmdir()
        except OSError:
            pass
        trace.emit('ui.transfer_finished', mod_id=job.mod_id, file_id=job.file_id)
        if job.export_directory:
            export_archive(saved, job.export_directory)

    jobs = DownloadQueue(perform_download, lambda j: incoming.put(('job', j.id)),
                         history_path=PATHS['history'])
    first_new_job = max(jobs.items, default=0) + 1
    auto_free.gate = lambda: not jobs.busy()

    def background(fn):
        def work():
            try:
                result = fn()
                incoming.put(('result', result))
            except Exception as exc:
                incoming.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def check_key(e=None):
        if not key.value.strip():
            tell('Введите Personal API Key во вкладке «Настройки».')
            return
        tell('Проверяем аккаунт Nexus…')
        def task():
            user = NexusClient(key.value).validate()
            premium['value'] = bool(user.get('is_premium'))
            return ('account', 'Premium' if premium['value'] else 'Free')
        background(task)

    def destination_folder():
        if destination_mode.value == 'mo2' and mo2_ini.value:
            path = Path(mo2_ini.value.strip().strip('"')).expanduser()
            if path.is_dir() or path.name.lower() == 'modorganizer.exe':
                matches = find_mo2_instances([path])
                if len(matches) == 1:
                    mo2_ini.value = str(matches[0])
                elif len(matches) > 1:
                    raise DestinationError('Найдено несколько экземпляров MO2. Выберите нужный ModOrganizer.ini.')
        return str(resolve_destination(destination_mode.value, folder.value, mo2_ini.value))

    def external_destination():
        # The canonical copy is ALWAYS kept in the portable library.
        if destination_mode.value == 'folder' and Path(folder.value).expanduser().resolve() == PATHS['downloads'].resolve():
            return ''
        return destination_folder()

    def validate_destination(e=None):
        try:
            result = destination_folder()
            tell(f'Папка назначения проверена: {result}')
        except DestinationError as exc:
            tell(f'Ошибка назначения: {exc}')

    def selected_browser_setup():
        return get_browser_setup(browser_guide_browser.value or 'edge')

    def open_selected_browser_extensions(e=None):
        setup = selected_browser_setup()
        try:
            open_extension_manager(setup.key)
            tell(f'Открываем расширения {setup.name}. Если страница не появилась, введите в адресной строке браузера: {setup.management_url}')
        except (OSError, ValueError) as exc:
            tell(str(exc))

    def show_selected_extension_folder(e=None):
        import os
        import subprocess
        import sys
        setup = selected_browser_setup()
        folder_path = application_root() / 'extensions' / setup.extension_dir
        if not folder_path.is_dir():
            tell(f'Не найдена папка расширения: {folder_path}')
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(folder_path))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder_path)])
            else:
                subprocess.Popen(['xdg-open', str(folder_path)])
            tell(f'Папка расширения {setup.name} открыта. Укажите её в браузере.')
        except OSError as exc:
            tell(f'Не удалось открыть папку: {exc}')

    def open_browser_visual_guide(e=None):
        guide = application_root() / 'docs' / ('BROWSER_SETUP_GUIDE_EN.html' if screen.language == 'en' else 'BROWSER_SETUP_GUIDE.html')
        if not guide.is_file():
            tell('Наглядная инструкция не найдена в папке docs.')
            return
        webbrowser.open(guide.as_uri())

    def check_browser_connection(e=None):
        # Only recently received, approved snapshots count as connected.
        profiles = snapshots.profile_counts(max_age=120)
        summary = snapshots.summary(max_age=120)
        setup = selected_browser_setup()
        current = profiles.get(setup.key, 0)
        if current:
            browser_guide_status.value = (
                f'✓ {setup.name}: подключено профилей {current}, модов найдено {summary.get(setup.key, 0)}. '
                'Все окна подключённого профиля проверяются без переключений.')
            browser_guide_status.color = '#8DDEBB'
        elif any(item['browser']==setup.key for item in pairing.pending()):
            browser_guide_status.value = 'Запрос от расширения получен. Нажмите «Разрешить браузер» вверху окна.'
            browser_guide_status.color = '#F1C181'
        else:
            browser_guide_status.value = (
                f'{setup.name} пока не подключён. Если расширение уже установлено, откройте браузер: '
                'запрос «Разрешить браузер» появится вверху этого окна.')
            browser_guide_status.color = '#F1C181'
        redraw()

    def render_browser_steps(e=None):
        setup = selected_browser_setup()
        browser_guide_steps.controls.clear()
        for index, text in enumerate(setup.steps, 1):
            browser_guide_steps.controls.append(
                ft.Row([
                    ft.Container(width=26, height=26, bgcolor='#34305B', border_radius=13,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text(str(index), color='#D8CFFF', weight=ft.FontWeight.BOLD)),
                    ft.Text(text, color='#D4D8E8', size=13, expand=True),
                ], vertical_alignment=ft.CrossAxisAlignment.START, spacing=12)
            )
        browser_guide_note.value = setup.note
        browser_guide_note.visible = bool(setup.note)
        browser_guide_path.value = f'Папка расширения: {application_root() / "extensions" / setup.extension_dir}'
        check_browser_connection()

    def open_browser_setup(e=None):
        browser_setup_panel.visible = setup_opened['value'] = True
        change_tab('start')
        render_browser_steps()

    def toggle_browser_setup(e=None):
        browser_setup_panel.visible = not browser_setup_panel.visible
        setup_opened['value'] = browser_setup_panel.visible
        if browser_setup_panel.visible:
            render_browser_steps()
        redraw()

    def update_pairing_requests():
        requests = pairing.pending()
        browser_pair_requests.controls.clear()
        pairing_banner_content.controls.clear()
        pairing_banner.visible = bool(requests)

        def request_actions(request):
            rid = request['request_id']
            name = BROWSERS[request['browser']].name if request['browser'] in BROWSERS else request['browser']
            def approve(e, request_id=rid):
                if pairing.approve(request_id):
                    browser_guide_status.value = 'Разрешение принято. Браузер подключится в течение нескольких секунд.'
                    browser_guide_status.color = '#8DDEBB'
                    tell('Разрешение выдано. Браузер подключится в течение нескольких секунд.')
                update_pairing_requests()
            def deny(e, request_id=rid):
                pairing.deny(request_id)
                update_pairing_requests()
            return ft.Column([
                ft.Text(f'Подключить {name}? Разрешить доступ к открытым модам Nexus Mods?',
                        color='#F2EDFF', weight=ft.FontWeight.BOLD),
                ft.Text('Расширение передаст только ссылки на моды, а не остальные страницы браузера.',
                        size=12, color='#D3CBE8'),
                ft.Row([ft.Button('Разрешить браузер', icon=ft.Icons.CHECK, on_click=approve),
                        ft.TextButton('Отклонить', on_click=deny)], wrap=True)
            ], spacing=7)

        for request in requests:
            browser_pair_requests.controls.append(ft.Container(
                bgcolor='#27233D', border_radius=12, padding=12,
                content=request_actions(request)))
            pairing_banner_content.controls.append(request_actions(request))
        redraw()

    def show_extension_folder(e=None):
        # The extensions ship inside the portable app. Browsers still require
        # explicit user confirmation to install them.
        import os
        import subprocess
        import sys
        folder_path = application_root() / 'extensions'
        try:
            if sys.platform == 'win32':
                os.startfile(folder_path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder_path)])
            else:
                subprocess.Popen(['xdg-open', str(folder_path)])
            tell('Дополнительный способ для браузеров с недоступным системным API: chromium — Chrome/Edge/Opera GX; firefox — Firefox.')
        except (OSError, FileNotFoundError) as exc:
            tell(f'Не удалось открыть папку: {folder_path} ({exc})')

    def update_helper_status():
        old_extension = auto_free.outdated_helper()
        if old_extension:
            helper_status.value = (f'Расширение браузера устарело: в браузере {old_extension}, нужна версия {REQUIRED_EXTENSION}. '
                                   'Откройте страницу расширений браузера и на карточке Nexus Tab Collector '
                                   'нажмите круглую стрелку или «Перезагрузить».')
            helper_status.color = '#F1C181'
            state = ('Расширение устарело', WARN)
        elif auto_free.has_live_helper():
            helper_status.value = 'Браузер подключён: файлы будут скачиваться автоматически.'
            helper_status.color = '#8DDEBB'
            state = ('Браузер подключён', GOOD)
        else:
            helper_status.value = ('Расширение браузера пока не отвечает. Откройте браузер с установленным '
                                   f'Nexus Tab Collector (версия {REQUIRED_EXTENSION}) — подключение проверяется автоматически.')
            helper_status.color = '#F1C181'
            state = ('Браузер не отвечает', MUTED)
        side_browser_text.value, side_browser_dot.color = state
        # In the way only while something is wrong: the side bar always shows how things stand.
        browser_line.visible = state[1] != GOOD
        changed = helper_state['value'] != source_text(helper_status)
        helper_state['value'] = source_text(helper_status)
        return changed

    def update_browser_status():
        update_helper_status()
        sources = snapshots.summary(max_age=120)
        if sources:
            profiles = snapshots.profile_counts(max_age=120)
            browser_status.value = 'Подключено: ' + ', '.join(
                f'{name} — {profiles.get(name, 1)} проф., {number} модов' for name, number in sorted(sources.items()))
        else:
            browser_status.value = 'Режим Windows: часть фоновых вкладок может быть недоступна.'
        redraw()

    def scan_tabs(e=None):
        items = snapshots.collect()
        if not snapshots.sources():
            update_browser_status()
            tell('Нет связи с расширением. Установите его один раз и разрешите подключение на главном экране.')
            return 0
        if not items:
            update_browser_status()
            tell('Браузер подключён, но открытых страниц модов Nexus Mods не найдено.')
            return 0
        add(items)
        update_browser_status()
        return len(items)

    def begin_workflow_items(items, waiting_session=None):
        if waiting_session and (waiting_session is not workflow['current'] or
                                waiting_session.cancelled.is_set()):
            return
        entries[:] = plan.replace(items)
        restore_choices()
        save_choices()
        row_memory.clear()
        persist()
        look_up_mods(plan.items)
        fold_setup_for_list()
        workflow['current'] = WorkflowSession(entries)
        workflow['running'] = False
        workflow_summary.value = (f'Найдено {len(entries)} модов во всех доступных окнах. '
                                  'Отметьте нужные и выберите назначение, затем нажмите «Скачать выбранные».')
        refresh_workflow()

    def start_workflow(e=None):
        trace.emit('ui.action', step='scan')
        trace.emit('ui.scan_clicked')
        """Scan every browser source, aggregate windows and wait for explicit selection."""
        if workflow['running']:
            tell('Поиск или загрузка уже выполняется. Для остановки нажмите «Отмена».')
            return
        workflow['previous'] = workflow['current']
        workflow['running'] = True
        # Scanning is read-only: never discard previous Free download confirmations.
        waiting = WorkflowSession([])
        workflow['current'] = waiting
        workflow_summary.value = 'Сканируем все доступные окна и объединяем найденные моды…'
        refresh_workflow()
        # Extension URLs are authoritative for every tab in its browser profile.
        # Request a FRESH snapshot for each new scan. The previous code fell
        # back to Windows immediately after the 120s TTL expired, even though
        # a paired extension might still be installed and could read every tab.
        snapshot_mods = snapshots.collect(max_age=120)
        import time
        if snapshots.sources(max_age=86400):
            revision = snapshots.request_refresh()
            workflow_summary.value = 'Запрашиваем свежий список всех вкладок у подключённого расширения…'
            refresh_workflow()
            def await_extension():
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline and not waiting.cancelled.is_set():
                    if snapshots.refresh_acknowledged(revision):
                        # Allow other independently connected profiles to reply.
                        time.sleep(0.6)
                        return ('extension_scanned', waiting, snapshot_mods)
                    time.sleep(0.2)
                return ('extension_timeout', waiting, revision, snapshots.refresh_progress(revision))
            background(await_extension)
            return
        if browser_paired['value']:
            # Connected in an earlier run but silent since this start. It usually
            # answers within seconds and, unlike Windows, sees every background tab.
            snapshots.request_refresh()
            workflow_summary.value = 'Ждём ответ расширения браузера, оно видит все вкладки…'
            refresh_workflow()
            def await_first_snapshot():
                deadline = time.monotonic() + FIRST_SNAPSHOT_WAIT
                while time.monotonic() < deadline and not waiting.cancelled.is_set():
                    if snapshots.sources(max_age=86400):
                        # Allow other independently connected profiles to reply.
                        time.sleep(0.6)
                        return ('extension_scanned', waiting, snapshot_mods)
                    time.sleep(0.2)
                scan = scan_open_windows(cancelled=waiting.cancelled.is_set)
                return ('windows_scanned', scan, waiting, snapshot_mods)
            background(await_first_snapshot)
            return
        def discover_tabs():
            # No extension has provided a snapshot recently; Windows provides
            # an explicitly incomplete fallback, not a full-tab scan.
            scan = scan_open_windows(cancelled=waiting.cancelled.is_set)
            return ('windows_scanned', scan, waiting, snapshot_mods)
        background(discover_tabs)

    def ready_to_download():
        if (key.value and key.value.strip()) or auto_free_opt.value:
            return True
        api_notice.visible = True
        api_notice_title.value = 'Невозможно скачать: нет API-ключа и выключено скачивание через браузер'
        workflow_summary.value = ('Введите Nexus API-ключ в настройках или включите «Скачивать автоматически '
                                  'через браузер». Список модов сохранён.')
        tell('Нужен Nexus API-ключ или включённое скачивание через браузер.')
        refresh_workflow()
        # Open the actual setup screen immediately instead of leaving the
        # user staring at a disabled operation and a status message.
        open_key_settings()
        return False

    def download_selected(e=None):
        trace.emit('ui.action', step='download')
        trace.emit('ui.download_clicked', selected=len(plan.chosen()))
        if workflow['running']:
            return
        chosen = plan.chosen()
        if not chosen:
            tell('Поставьте галочки у модов, которые хотите скачать.')
            return
        if not ready_to_download():
            return
        # Preserve paths and opt-in API credential before leaving this workflow.
        persist_config(silent=True)
        valid = []
        choices = {}
        errors = []
        for item, target in chosen:
            try:
                validate_target(target, mo2_ini=mo2_ini.value or '',
                                mo2_profile=mo2_profile.value or '',
                                vortex_folder=vortex_folder.value or '',
                                game_folder=game_folder.value or '',
                                vortex_exe=vortex_exe.value or '')
                if target == 'game' and item['game'] != 'skyrimspecialedition':
                    raise ValueError('Автоустановка в Data поддерживается пока только для Skyrim SE')
                valid.append(item)
                choices[identity(item)] = target
            except (ValueError, OSError) as exc:
                errors.append(f"#{item['mod_id']}: {exc}")
        if not valid:
            destination_options_panel.visible = True
            workflow_summary.value = 'Не настроен выбранный способ установки: ' + '; '.join(errors[:2])
            refresh_workflow()
            return
        launch_batch(valid, choices, errors)

    def launch_batch(valid, choices, errors=(), pinned=False):
        """Start one download run. Everything it downloads goes into one session folder."""
        # Explicitly pin the chosen IDs for this batch; later UI edits cannot
        # silently change which archive gets downloaded.
        chosen_file_choices = {identity(item): FileChoice() if pinned else FileChoice.from_dict(
            file_preferences.get(identity(item), FileChoice()).as_dict()) for item in valid}
        listed = {identity(item) for item in plan.items}
        earlier = workflow['current'].snapshot()[0] if workflow['current'] else {}
        run = WorkflowSession([*plan.items, *(item for item in valid if identity(item) not in listed)])
        for item in plan.items:
            if identity(item) not in choices:
                if pinned:  # a retry, a requirement or an update does not concern the rest of the list
                    run.set_status(item, *earlier.get(identity(item), ('Готов к загрузке', '')))
                else:
                    run.set_status(item, 'Не выбрано' if not plan.choices[identity(item)].selected else 'Ошибка назначения')
        workflow['current'] = run
        disk_state['told'] = False
        run.collection = ModCollection.create(PATHS['sessions'], valid)
        run.collection.set_selection([(item, choices[identity(item)]) for item in valid])
        workflow['targets'] = choices
        workflow['file_choices'] = chosen_file_choices
        workflow['destination_options'] = delivery_options()
        workflow['running'] = True
        remember_unfinished(valid)
        trace.emit('ui.download_preparing', selected=len(valid))
        free_queue.clear()
        auto_free.stop()
        waiting_for_helper['value'] = False
        stall_warning['value'] = False
        browser_download_watcher.clear()
        free_count.value = 'Ожидают подтверждения: 0'
        workflow_summary.value = f'Подготавливаем {len(valid)} выбранных модов.'
        if errors: workflow_summary.value += ' Пропущены: ' + '; '.join(list(errors)[:2])
        refresh_workflow()
        prepare_downloads(valid, run)

    def remember_unfinished(items):
        """A long batch can outlive the program: keep its list until it ends."""
        try:
            save_pack(PATHS['unfinished'], items)
        except OSError:
            pass

    def forget_unfinished():
        try:
            PATHS['unfinished'].unlink(missing_ok=True)
        except OSError:
            pass

    def cancel_workflow(e=None):
        trace.emit('ui.action', step='cancel')
        session = workflow['current']
        if session is None or not workflow['running']:
            return
        session.stop(jobs.cancel)
        free_queue.clear()
        auto_free.stop()
        forget_unfinished()
        waiting_for_helper['value'] = False
        browser_download_watcher.clear()
        free_count.value = 'Ожидают подтверждения: 0'
        workflow['running'] = False
        if session.collection:
            session.collection.discard_if_empty()
        workflow_summary.value = 'Операция отменена. Уже скачанные файлы сохранены.'
        tell(source_text(workflow_summary))
        refresh_workflow()
        refresh_jobs()

    def prepare_downloads(targets, session):
        """Choose the files of every selected mod and route each one.

        Premium + API key: direct Nexus API downloads. Free account or no API
        key: the paired browser downloads each file through the website.
        Files already in the library are reused without downloading again.
        """
        apikey = (key.value or '').strip()
        is_premium = premium['value']
        use_browser = bool(auto_free_opt.value)
        choices = dict(workflow.get('file_choices', {}))
        targets_by_mod = dict(workflow.get('targets', {}))
        def task():
            known = ArchiveIndex(PATHS['sessions'])
            client = NexusClient(apikey) if apikey else None
            account_premium = is_premium
            if client is not None and account_premium is None:
                try:
                    account_premium = bool(client.validate().get('is_premium'))
                except Exception as exc:
                    if not use_browser:
                        return ('latest_error', str(exc), session)
                    # A broken key must not stop browser downloads.
                    client = None
            queued, browser_specs, reused, misses, planned_kb = 0, [], [], [], 0
            for item in targets:
                if session.cancelled.is_set():
                    break
                session.set_status(item, 'Проверка файлов')
                incoming.put(('workflow_refresh',))
                try:
                    pref = choices.get(identity(item), FileChoice())
                    explicit = list(item.get('file_ids') or [])
                    available = None
                    if client is not None:
                        try:
                            available = client.files(item['game'], item['mod_id'])
                        except NexusRateLimited:
                            # The API budget is spent: stop asking. The website does not use it.
                            if not use_browser:
                                raise
                            client = None
                        except NexusError:
                            # Hidden mod, API limit, broken key: the website may still offer the file.
                            if not use_browser:
                                raise
                    if available is not None:
                        listed = catalog(available)
                        if pref.mode == 'manual':
                            chosen = pref.resolve(available)
                        elif explicit:
                            lookup = {f['file_id']: f for f in listed}
                            chosen = [lookup.get(fid, {'file_id': fid, 'file_name': '', 'size_kb': 0})
                                      for fid in explicit]
                        else:
                            chosen = pref.resolve(available)
                            if not chosen:
                                # A mod without MAIN files: its newest file that is not an old version.
                                rest = [f for f in listed if f['available'] and f['category_name'] != 'OLD_VERSION']
                                chosen = sorted(rest, key=lambda f: (f['uploaded_timestamp'], f['file_id']), reverse=True)[:1]
                    else:
                        ids = list(pref.file_ids) if pref.mode == 'manual' else explicit
                        chosen = [{'file_id': fid, 'file_name': '', 'size_kb': 0} for fid in (ids or [0])]
                    if not chosen:
                        reason = ('Не выбран ни один файл' if pref.mode == 'manual' else
                                  'У мода нет доступных файлов.')
                        misses.append(f"{item['game']} #{item['mod_id']}: {reason}")
                        session.set_status(item, 'Ошибка', reason)
                        session.expect(item, f"{item['game']}:{item['mod_id']}:none")
                        session.fail_mod(item)
                        continue
                    archives_chosen = sum(not is_plain_file(f.get('file_name') or '') for f in chosen)
                    if archives_chosen > 1 and targets_by_mod.get(identity(item)) == 'game':
                        raise ValueError('Несколько архивов нельзя автоматически объединять в Data игры. Выберите MO2 или «Только скачать».')
                    waiting = 0
                    for f in chosen:
                        if session.cancelled.is_set():
                            break
                        fid = int(f['file_id'])
                        spec = {'game': item['game'], 'mod_id': item['mod_id'], 'file_id': fid,
                                'filename': f.get('file_name') or '', 'size_kb': int(f.get('size_kb') or 0),
                                'task': f"{item['game']}:{item['mod_id']}:{fid}"}
                        session.expect(item, spec['task'])
                        existing = known.find(item['game'], item['mod_id'], fid)
                        if existing is None and not fid:
                            existing = known.recent(item['game'], item['mod_id'], RECENT_ARCHIVE_SECONDS)
                            if existing is not None:
                                spec['file_id'] = existing.file_id
                        if existing is not None:
                            reused.append((item, spec, existing.path))
                            continue
                        info = mod_info.get(identity(item))
                        planned_kb += spec['size_kb'] or (info.size_kb if info else 0)
                        if available is not None and account_premium:
                            def enqueue(spec=spec):
                                return jobs.add(spec['game'], spec['mod_id'], spec['file_id'],
                                                spec['filename'],
                                                apikey=apikey, task=spec['task'],
                                                directory=str(session.collection.folder))
                            job = session.attach_job(item, enqueue)
                            if job:
                                job_routes[job.id] = (item, targets_by_mod.get(identity(item), 'library'), session, workflow.get('destination_options', delivery_options()))
                                queued += 1
                            else:
                                session.finish(spec['task'], True)
                                session.set_status(item, 'Уже в очереди')
                        else:
                            browser_specs.append(spec)
                            waiting += 1
                    if waiting:
                        session.set_status(item, 'Ожидает браузер', f'{waiting} файл(ов)')
                    elif session.mod_counts(item)['pending']:
                        session.set_status(item, 'В очереди', f'{len(chosen)} файл(ов)')
                except Exception as exc:
                    misses.append(f"{item['game']} #{item['mod_id']}: {exc}")
                    session.set_status(item, 'Ошибка', str(exc)[:160])
                    session.expect(item, f"{item['game']}:{item['mod_id']}:none")
                    session.fail_mod(item)
                finally:
                    incoming.put(('workflow_refresh',))
            return ('prepared', queued, browser_specs, reused, misses, account_premium, session, planned_kb)
        background(task)

    def deliver_task(item, task_key, archive, target, options, session, file_id):
        """Record and deliver one archive (MO2 / Vortex / game / library) off the UI thread."""
        def work():
            if session.cancelled.is_set():
                return ('task_delivered', task_key, item, 'Отменено', '', session, False)
            try:
                record_archive(archive, item['game'], item['mod_id'], file_id)
                if target == 'library':
                    return ('task_delivered', task_key, item, 'Готово', f'В библиотеке: {Path(archive).name}', session, True)
                stage, detail = deliver(archive, {**item, 'download_file_id': file_id}, target, **options)
                return ('task_delivered', task_key, item, stage, detail, session, True)
            except Exception as exc:
                return ('task_delivered', task_key, item, 'Ошибка установки', str(exc), session, False)
        background(work)

    def session_item(session, game, mod_id):
        if session is None:
            return None
        return next((x for x in session.items if identity(x) == (game, int(mod_id))), None)

    def finish_if_done():
        """End the batch once every selected file is delivered or failed."""
        session = workflow['current']
        if session is None or not workflow['running'] or session.cancelled.is_set():
            return
        # A search has no files, and a lookup does not know them yet: no files here does not mean done.
        if workflow.get('prepared') is not session:
            return
        counts = session.task_counts()
        if counts['pending']:
            return
        if any(jobs.items.get(jid) and jobs.items[jid].state in ('queued', 'downloading')
               for jid in session.all_job_ids()):
            return
        if any(jid in installing_jobs for jid in session.all_job_ids()):
            return
        workflow['running'] = False
        forget_unfinished()
        if session.collection:
            session.collection.discard_if_empty()
        if auto_free.is_idle():
            auto_free.stop()
        total = counts['done'] + counts['failed']
        workflow_summary.value = (f'Готово: скачано и обработано {counts["done"]} из {total} файлов.' +
                                  (f' Ошибок: {counts["failed"]} — подробности в строках модов и во вкладке «Загрузки».'
                                   if counts['failed'] else ''))
        tell(source_text(workflow_summary))
        announce('Загрузка закончена', source_text(workflow_summary))
        refresh_present()
        refresh_workflow()

    def warn_about_disk(needed=0, through_browser=True):
        """Only says so. The download goes on, and the person can free some room or press «Отмена»."""
        folders = [PATHS['sessions'], *(browser_download_folders(browser_download_folder.value) if through_browser else ())]
        problems = shortages(folders, needed)
        if not problems:
            return
        folder, free = problems[0]
        message = (f'Мало места на диске {folder.anchor}: свободно {size_text(free)}, файлы займут примерно {size_text(needed)}.' if needed
                   else f'Мало места на диске {folder.anchor}: свободно {size_text(free)}.')
        disk_text.value = message + ' Загрузка продолжается: если места не хватит, файл закончится ошибкой. Можно освободить место или нажать «Отмена».'
        disk_notice.visible = True
        if not disk_state['told']:
            disk_state['told'] = True
            announce('Мало места на диске', message)
        redraw()

    def handle_auto_outcome(outcome):
        """A browser job finished: link, saved file, retry or failure."""
        kind, info = outcome.get('kind'), outcome.get('job', {})
        session = workflow['current']
        if session is None or session.collection is None or session.cancelled.is_set():
            return
        game, mod_id, fid = info.get('game'), int(info.get('mod_id') or 0), int(info.get('file_id') or 0)
        item = session_item(session, game, mod_id)
        if item is None:
            return
        task_key = info.get('task') or session.task_for(game, mod_id, fid)
        target = workflow.get('targets', {}).get(identity(item), 'library')
        options = workflow.get('destination_options', delivery_options())
        if kind == 'url':
            trace.emit('ui.auto_handoff', mod_id=mod_id, file_id=fid)
            free_queue.remove_file(game, mod_id, fid)
            job = jobs.add(game, mod_id, fid, info.get('filename') or '', url=outcome.get('url'),
                           task=task_key, directory=str(session.collection.folder))
            if job is None:
                session.set_status(item, 'Уже в очереди')
                return
            job_routes[job.id] = (item, target, session, options)
            session.register_job(item, job)
            session.set_status(item, 'Скачивается', job.filename or f'файл #{fid}')
        elif kind == 'file':
            trace.emit('ui.auto_handoff', mod_id=mod_id, file_id=fid)
            free_queue.remove_file(game, mod_id, fid)
            session.set_status(item, 'Получен файл из браузера')
            path = outcome.get('path', '')
            def work():
                try:
                    stored = store_browser_file(path, session.collection.folder)
                except Exception as exc:
                    return ('task_failed', task_key, item, 'Ошибка импорта', str(exc), session)
                trace.emit('ui.browser_file_imported', mod_id=mod_id, file_id=fid)
                return ('browser_file_stored', task_key, item, str(stored), target, options, session, fid)
            background(work)
        elif kind == 'retry':
            trace.emit('ui.auto_retry', mod_id=mod_id, file_id=fid)
            session.set_status(item, info.get('step') or 'Повторная попытка', info.get('error', ''))
        elif kind == 'paused':
            trace.emit('ui.auto_paused', mod_id=mod_id, file_id=fid)
            reason = auto_free.state()['pause_reason']
            session.set_status(item, 'Нужен вход в Nexus' if reason == 'login_required' else 'Загрузка остановлена',
                               info.get('error', ''))
            workflow_summary.value = 'Загрузка приостановлена, подробности вверху окна.'
        elif kind == 'cooldown':
            trace.emit('ui.auto_retry', mod_id=mod_id, file_id=fid)
            session.set_status(item, 'Пауза: Nexus просит подождать', info.get('error', ''))
        elif kind == 'failed':
            trace.emit('ui.auto_failed', mod_id=mod_id, file_id=fid)
            free_queue.remove_file(game, mod_id, fid)
            session.set_status(item, 'Ошибка', info.get('error', '')[:180])
            if task_key:
                session.finish(task_key, False)
            # The last failure of a run may have stopped the queue.
            stopped = auto_free.state()
            if stopped['paused']:
                workflow_summary.value = 'Загрузка приостановлена, подробности вверху окна.'
        free_count.value = f'Ожидают подтверждения: {len(free_queue)}'
        refresh_workflow()
        finish_if_done()

    def resume_auto(e=None):
        if auto_free.resume():
            workflow_summary.value = 'Продолжаем загрузку через браузер.'
            refresh_workflow()

    def import_nxm(e=None, uri=None):
        trace.emit('ui.action', step='import')
        try:
            game, mod_id, file_id, keypart, expires, userid = nxm_parts(uri if uri is not None else nxm_field.value)
            if not key.value.strip():
                tell('Сначала введите Personal API Key для NXM-загрузки.')
                return False
            running_session = workflow['current']
            # Do not accept a random NXM file as part of a running selected batch.
            if (workflow['running'] and running_session and
                    not free_queue.contains_file(game, mod_id, file_id) and
                    not running_session.task_for(game, mod_id, file_id)):
                tell('NXM относится к файлу, не выбранному в текущей операции. Загрузка не запущена.')
                return False
            task_key = running_session.task_for(game, mod_id, file_id) if running_session else ''
            # A file outside the running download gets a folder of its own, shared until the program closes.
            if workflow['running'] and running_session and running_session.collection:
                batch = running_session.collection
            else:
                if collection['loose'] is None or not collection['loose'].folder.is_dir():
                    collection['loose'] = ModCollection.create(PATHS['sessions'], [])
                batch = collection['loose']
            # The worker resolves the real extension using the API, without
            # freezing Flet for up to 30 seconds on every incoming NXM.
            file_name = f'{game}-{mod_id}-{file_id}.download'
            # All files land in the portable library first. Only the per-mod
            # delivery step may copy/install into MO2, Vortex or a game.
            job = jobs.add(game, mod_id, file_id, file_name,
                     key=keypart, expires=expires, user_id=userid, task=task_key,
                     apikey=key.value.strip(), directory=str(batch.folder), export_directory='')
            nxm_field.value = ''
            auto_free.acknowledge_external(game, mod_id, file_id)
            browser_download_watcher.forget(game, mod_id, file_id)
            free_queue.remove_file(game, mod_id, file_id)
            if running_session and not running_session.cancelled.is_set():
                matching_item = next((x for x in running_session.items
                                      if identity(x) == (game, mod_id)), None)
                if matching_item:
                    running_session.set_status(matching_item, 'В очереди', file_name)
                    if job is not None:
                        job_routes[job.id] = (matching_item, workflow.get('targets', {}).get(identity(matching_item), 'library'), running_session, workflow.get('destination_options', delivery_options()))
                        # Attach the already-created NXM job for progress display.
                        running_session.register_job(matching_item, job)
            free_count.value = f'Ожидают подтверждения: {len(free_queue)}'
            trace.emit('ui.nxm_import_accepted', mod_id=mod_id, file_id=file_id)
            tell('NXM-ссылка принята. ' + ('Файл добавлен в очередь.' if job else 'Файл уже находится в очереди.'))
            refresh_jobs()
            if len(free_queue) and not auto_free_opt.value:
                next_free()
            return True
        except Exception as exc:
            trace.emit('ui.nxm_import_failed')
            tell(str(exc))
            return False

    def open_free(specs):
        """Hand Free / no-API files to the browser helper, or to the user."""
        if not specs:
            return
        trace.emit('ui.free_queue_created', count=len(specs))
        free_queue.add([file_page_url(s['game'], s['mod_id'], s['file_id']) for s in specs if s['file_id']])
        free_count.value = f'Ожидают подтверждения: {len(free_queue)}'
        if auto_free_opt.value:
            # Jobs wait in the batch until a browser profile asks for work, so
            # the queue starts by itself as soon as the extension connects.
            count = auto_free.start(specs)
            waiting_for_helper['value'] = not auto_free.has_live_helper()
            if waiting_for_helper['value']:
                workflow_summary.value = (
                    f"{plural(count, 'файл ждёт', 'файла ждут', 'файлов ждут')} браузер. Расширение Nexus Tab Collector пока не ответило: "
                    'откройте браузер с установленным расширением — загрузка начнётся сама.')
            else:
                workflow_summary.value = (f"Браузер скачает {plural(count, 'файл', 'файла', 'файлов')} по очереди, примерно "
                                          f'{duration_text(pacer.estimate(count))} без учёта самих загрузок. '
                                          'Не закрывайте браузер: между файлами идут паузы.')
            tell('Загрузка запущена.' if not waiting_for_helper['value'] else workflow_summary.value)
        else:
            waiting_for_helper['value'] = False
            workflow_summary.value = ('Скачивание через браузер выключено. Подтвердите каждый файл на сайте '
                                      '(Slow download) — программа заберёт архив из папки загрузок.')
            tell(f'Подготовлено {len(specs)} файлов. ' + workflow_summary.value)
            next_free()

    def next_free(e=None):
        url = free_queue.peek()
        if not url:
            tell('Нет ожидающих подтверждения файлов.')
            return
        # webbrowser.open_new() often OPENS A TAB in existing Edge/Chrome.
        # The native path requests a real new browser window; do not silently
        # fall back to the old behaviour or claim that the helper is running.
        opened, reason = open_nexus_window(url)
        if opened:
            tell(reason + ' После подтверждения Nexus приложение примет NXM или готовый архив.')
        else:
            tell(reason + ' Автоматические нажатия требуют подключённого расширения браузера.')

    def persist_config(*, silent=False):
        trace.emit('ui.action', step='settings')
        # All editable paths were previously dropped by settings.FIELDS.
        # Save on field blur and on navigation; never persist the API secret
        # without the opt-in checkbox.
        save_settings(CONFIG_FILE, {
            'game_folder': game_folder.value or '',
            'vortex_folder': vortex_folder.value or '',
            'mo2_profile': mo2_profile.value or '',
            'folder': portable_path(folder.value) or '',
            'destination_mode': destination_mode.value or 'folder',
            'mo2_ini': mo2_ini.value or '',
            'pack_path': portable_path(pack_path.value) or '',
            'remember_api_key': bool(remember_key.value),
            'api_key': key.value or '',
            'browser_token': token,
            'auto_free_enabled': bool(auto_free_opt.value),
            'browser_download_folder': browser_download_folder.value or '',
            'browser_paired': browser_paired['value'],
            'download_pace': pace_choice.value or DEFAULT_PRESET,
            'pace_gap': pace_gap.value or '',
            'pace_batch': pace_batch.value or '',
            'pace_rest': pace_rest.value or '',
            'pace_daily': pace_daily.value or '',
            'vortex_exe': vortex_exe.value or '',
            'default_target': plan.default_target,
            'language': screen.language,
            'notify_enabled': bool(notify_opt.value),
            'auto_scan': bool(auto_scan_opt.value),
            'updates_auto': bool(updates_auto.value),
            'requirements_mode': requirements_mode.value or 'ask',
            'known_elsewhere': '\n'.join(sorted(info_state['known_elsewhere'])),
        })
        if not silent:
            tell('Настройки сохранены.' + (' API-ключ сохранён в портативной папке без шифрования.' if remember_key.value else ' API-ключ не сохраняется.'))

    def store_config(e=None):
        persist_config()
        redraw()

    # Automatic persistence makes changing a screen / importing a pack safe.
    for field in (key, folder, mo2_ini, pack_path, vortex_folder, vortex_exe, game_folder, browser_download_folder,
                  pace_gap, pace_batch, pace_rest, pace_daily):
        field.on_blur = lambda e: persist_config(silent=True)
    for ctrl in (remember_key, auto_free_opt, notify_opt, auto_scan_opt, updates_auto):
        ctrl.on_change = lambda e: persist_config(silent=True)
    for ctrl in (destination_mode, mo2_profile):
        ctrl.on_select = lambda e: persist_config(silent=True)

    def describe_pace():
        pace = pacer.preset
        long_pause = (f'после {pace.batch} файлов пауза {pace.rest // 60} минут' if pace.batch and pace.rest
                      else 'длинных пауз нет')
        ceiling = f'не больше {pace.daily} файлов в сутки' if pace.daily else 'предела в сутки нет'
        between = f'Файл раз в {pace.gap} секунд' if pace.gap else 'Без паузы между файлами'
        pace_hint.value = (f'{between}, {long_pause}, {ceiling}. '
                           f'100 файлов займут примерно {duration_text(pacer.estimate(100))} без учёта самих загрузок. '
                           'Nexus не публикует лимит скачивания с сайта: готовые варианты выбраны программой и не гарантируют '
                           'отсутствие блокировки. Пауза сохраняется и после перезапуска программы.')

    def set_pace(e=None):
        pacer.set_preset(chosen_pace())
        pace_custom.visible = pace_warning.visible = pace_choice.value == CUSTOM
        describe_pace()
        persist_config(silent=True)
        redraw()

    def change_own_pace(e=None):
        pacer.set_preset(chosen_pace())
        describe_pace()
        redraw()

    pace_choice.on_select = set_pace
    for field in (pace_gap, pace_batch, pace_rest, pace_daily):
        field.on_change = change_own_pace
    describe_pace()

    def pack_save(e=None):
        try:
            save_pack(pack_path.value, entries)
            tell(f'Сборка сохранена: {pack_path.value}')
        except Exception as exc: tell(f'Не удалось сохранить: {exc}')

    def pack_load(e=None):
        try:
            add(load_pack(pack_path.value))
        except Exception as exc: tell(f'Не удалось открыть сборку: {exc}')

    def change_requirements_mode(e=None):
        info_state['hidden'] = None
        persist_config(silent=True)
        refresh_present()
        refresh_workflow()

    requirements_mode.on_select = change_requirements_mode

    def paint_language_switch():
        for code, button in language_buttons.items():
            button.bgcolor = '#35315A' if code == screen.language else None

    def set_language(code):
        screen.set(code)
        language_choice.value = screen.language
        paint_language_switch()
        change_tab(active['tab'])  # builds the screen anew, and saves the choice on the way
        refresh_workflow()
        if active['tab'] == 'downloads':
            refresh_library()

    language_choice.on_select = lambda e: set_language(language_choice.value)

    def test_notification(e=None):
        notify(screen.text('Nexus Tab Installer'), screen.text('Так будет выглядеть уведомление.'), page.title)
        tell('Уведомление отправлено. Если его нет, проверьте режим «Не беспокоить» в Windows.')

    def refresh_library(e=None):
        sessions = list_sessions(PATHS['sessions'])
        archives = sum(len(session.archives) for session in sessions)
        library_summary.value = (f"{plural(len(sessions), 'сессия', 'сессии', 'сессий')} · "
                                 f"{plural(archives, 'файл', 'файла', 'файлов')} · "
                                 f"{size_text(sum(session.size for session in sessions))}"
                                 if sessions else 'Здесь появятся скачанные файлы: каждая загрузка в своей папке с датой и временем.')
        now = datetime.now()
        library.controls[:] = [
            session_card(session, opened=session_open.get(session.folder.name, index == 0), now=now,
                         on_toggle=toggle_session, on_open_folder=open_folder, on_reveal=reveal_file,
                         pending=pending_delete['path'], on_delete=ask_delete, on_confirm=confirm_delete, on_cancel=cancel_delete)
            for index, session in enumerate(sessions[:200])]
        refresh_updates()
        redraw()

    def ask_delete(path):
        pending_delete['path'] = path
        refresh_library()

    def cancel_delete():
        pending_delete['path'] = None
        refresh_library()

    def confirm_delete():
        """To the Recycle Bin. The folder of a running download stays: files are still arriving there."""
        path, pending_delete['path'] = pending_delete['path'], None
        session = workflow['current']
        running = session.collection.folder if workflow['running'] and session and session.collection else None
        if path is None:
            return
        if running is not None and running in (path, path.parent):
            tell('Эта папка сейчас используется загрузкой. Удалить её можно, когда загрузка закончится.')
            refresh_library()
            return
        try:
            if path.is_dir():
                remove_session(path, to_recycle_bin)
            elif path.is_file():
                remove_archive(path, to_recycle_bin)
            trace.emit('ui.action', step='delete')
            tell('Удалено в Корзину. Вернуть можно из Корзины Windows, на флешке файлы удаляются сразу.')
        except OSError as exc:
            tell(f'Не удалось удалить: {exc}')
        refresh_library()
        refresh_present()

    def refresh_updates():
        """The offer disappears by itself once the new file is here, or the mod itself is not."""
        if updates_state['found']:
            known = ArchiveIndex(PATHS['sessions'])
            here = {(archive.game, archive.mod_id) for archive in known.archives}
            still = [update for update in updates_state['found']
                     if update.key in here and known.find(update.game, update.mod_id, update.file_id) is None]
            if len(still) != len(updates_state['found']):
                updates_state['found'] = still
                try:
                    save_found(PATHS['updates'], still, now=updates_state['checked_at'])
                except OSError:
                    pass
        found = updates_state['found']
        updates_area.controls[:] = [updates_card(found, on_fetch=lambda update: fetch_updates([update]),
                                                 on_fetch_all=lambda: fetch_updates(updates_state['found']),
                                                 on_page=lambda update: webbrowser.open(f'https://www.nexusmods.com/{update.game}/mods/{update.mod_id}'))] if found else []
        updates_area.visible = bool(found)
        downloads_count.value = str(len(found))
        downloads_badge.visible = bool(found)

    def check_updates(e=None, quiet=False):
        if updates_state['checking']:
            return
        updates_state['checking'] = True
        if not quiet:
            tell('Проверяем новые версии скачанных модов…')
        def work():
            try:
                return ('updates', find_updates(list_sessions(PATHS['sessions']), mod_infos, mod_files), '', quiet)
            except Exception as exc:  # a check that failed changes nothing
                return ('updates', None, str(exc)[:160], quiet)
        background(work)

    def fetch_updates(updates):
        if workflow['running']:
            tell('Сейчас идёт другая загрузка. Новую версию можно скачать, когда она закончится.')
            return
        if not updates or not ready_to_download():
            return
        trace.emit('ui.action', step='update')
        items = normalize_entries([{'url': f'https://www.nexusmods.com/{update.game}/mods/{update.mod_id}?tab=files&file_id={update.file_id}',
                                    'title': update.title} for update in updates])
        run_outside_list(items, {identity(item): (plan.choices[identity(item)].target if identity(item) in plan.choices else plan.default_target)
                                 for item in items})

    def toggle_session(name):
        sessions = list_sessions(PATHS['sessions'])
        first = sessions[0].folder.name if sessions else ''
        session_open[name] = not session_open.get(name, name == first)
        refresh_library()

    def reveal_file(path):
        """Explorer with this very file selected."""
        path = Path(path)
        if not path.is_file():
            tell('Этого файла уже нет на диске. Список обновлён.')
            refresh_library()
            return
        try:
            if os.name == 'nt':
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                open_folder(path.parent)
        except OSError as exc:
            tell(f'Не удалось показать файл: {exc}')

    archive_field = ft.TextField(label='Путь к архиву ZIP, 7z или RAR', hint_text='Вставьте путь к архиву (Ctrl+V)', expand=True)
    archive_result = ft.Text('Выберите ZIP, 7z или RAR для проверки.', color='#AAB2C7', size=12)

    def check_archive(e=None):
        try:
            details = inspect_archive(archive_field.value.strip().strip('"'))
            archive_result.value = f"Структура архива проверена: {details['files']} файлов, {details['bytes'] / 1024**2:.1f} МБ."
        except (DownloadError, OSError) as exc:
            archive_result.value = f'Проверка не пройдена: {exc}'
        redraw()

    def unpack_archive(e=None):
        try:
            location, details = stage_archive(archive_field.value.strip().strip('"'), PATHS['staging'])
            archive_result.value = f"Распаковано {details['files']} файлов в {location}. Файлы игры не изменены."
        except (DownloadError, OSError, ValueError) as exc:
            archive_result.value = f'Не удалось подготовить архив: {exc}'
        redraw()

    library = ft.Column(spacing=8)
    session_open = {}  # sessions the person folded or unfolded; the newest starts unfolded
    pending_delete = {'path': None}  # what is waiting for «В Корзину», right where it was asked
    updates_area = ft.Column(spacing=8, visible=False)
    checked_at, found_earlier = load_found(PATHS['updates'])
    updates_state = {'found': found_earlier, 'checked_at': checked_at, 'checking': False}

    def open_folder(folder_path):
        import os
        try:
            if not Path(folder_path).is_dir():
                if Path(folder_path) != PATHS['sessions']:
                    tell('Этой папки уже нет на диске. Список обновлён.')
                    refresh_library()
                    return
                Path(folder_path).mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(str(folder_path))
            else:
                tell(f'Папка: {folder_path}')
        except OSError as exc:
            tell(f'Не удалось открыть папку: {exc}')

    def open_library(e=None):
        open_folder(PATHS['sessions'])

    def find_mo2(e=None):
        manually_selected = (mo2_ini.value or '').strip().strip('"')
        matches = find_mo2_instances([manually_selected]) if manually_selected else find_mo2_instances()
        if not matches and manually_selected:
            matches = find_mo2_instances()
        if not matches:
            tell('MO2 не найден автоматически. Вставьте папку его установки или путь ModOrganizer.ini.')
            return
        if len(matches) == 1:
            mo2_ini.value = str(matches[0])
            destination_mode.value = 'mo2'
            refresh_mo2_profiles()
            tell(f'Найден экземпляр MO2: {matches[0]}. Проверьте папку и сохраните настройки.')
        else:
            # Do not silently select the wrong game/profile.
            tell(f'Найдено {len(matches)} экземпляров MO2. Укажите нужный ModOrganizer.ini: ' + '; '.join(str(x) for x in matches[:4]))
        redraw()

    def make_downloads():
        return downloads_view(nxm_field=nxm_field, import_nxm=import_nxm, next_free=next_free, free_count=free_count, archive_field=archive_field, check_archive=check_archive, unpack_archive=unpack_archive, archive_result=archive_result, summary=library_summary, now_area=now_area, library=library, history_button=history_button, history_area=history_area, refresh_library=refresh_library, open_library=open_library, updates_area=updates_area, check_updates=check_updates, refresh=redraw)

    def enable_nxm(e=None):
        try:
            register_nxm()
            tell('Обработчик nxm:// зарегистрирован для текущего пользователя Windows. Выберите Nexus Tab Installer, если браузер спросит, чем открыть ссылку.')
        except Exception as exc:
            tell(f'Не удалось зарегистрировать nxm://: {exc}')

    def detect_managers(e=None):
        try:
            root = (mo2_ini.value or '').strip().strip('\"')
            mo2_found = discover_mo2(roots=[root]) if root else discover_mo2()
            if not mo2_found and root:
                mo2_found = discover_mo2()
            found = mo2_found + discover_vortex()
        except (OSError, ValueError) as exc:
            tell(f'Не удалось выполнить поиск MO2/Vortex: {exc}')
            return
        detected_managers.controls.clear()
        if not found:
            detected_managers.controls.append(ft.Text('MO2/Vortex автоматически не найдены. Укажите папку вручную.'))
        for candidate in found:
            def choose(e, candidate=candidate):
                destination_mode.value = candidate.manager
                if candidate.manager == 'mo2':
                    mo2_ini.value = str(candidate.config)
                    tell('Найден экземпляр MO2. Проверьте Downloads перед сохранением.')
                    store_config()
                else:
                    # Vortex downloads path can be customized: never silently guess it.
                    if candidate.downloads:
                        folder.value = str(candidate.downloads)
                    tell('Vortex обнаружен. Подтвердите папку Downloads в Vortex → Settings → Download.')
                redraw()
            detected_managers.controls.append(ft.Row([
                ft.Text(candidate.label, expand=True, size=12),
                ft.Button('Выбрать', on_click=choose)
            ]))
        redraw()

    def describe_managers():
        lines = []
        try:
            info = instance_info(mo2_ini.value)
            lines.append(f"MO2 {info['version']}: {info['game']}, профиль {mo2_profile.value or 'не выбран'}.")
        except (OSError, ValueError):
            lines.append('MO2 не найден: укажите его папку и нажмите «Найти MO2».')
        exe = (vortex_exe.value or '').strip()
        lines.append(f'Vortex: {exe}.' if exe and Path(exe).is_file()
                     else 'Vortex не найден: укажите Vortex.exe.')
        manager_status.value = ' '.join(lines)

    def find_vortex_program(e=None):
        found = vortex_link.find_vortex()
        if found:
            vortex_exe.value = str(found)
            persist_config(silent=True)
        describe_managers()
        tell(f'Найден Vortex: {found}' if found else 'Vortex не найден в обычных местах. Укажите путь к Vortex.exe.')

    def check_vortex(e=None):
        exe = (vortex_exe.value or '').strip()
        if not exe or not Path(exe).is_file():
            tell('Сначала укажите Vortex.exe.')
            return
        tell('Проверяем Vortex…')
        background(lambda: ('vortex_checked', vortex_link.describe(exe)))

    def open_manager(program):
        """Starts the manager the person chose in the settings, nothing else."""
        if not program or not Path(program).is_file():
            tell('Программа не найдена. Проверьте её путь.')
            return
        try:
            os.startfile(str(program))
        except OSError as exc:
            tell(f'Не удалось запустить: {exc}')

    def open_mo2(e=None):
        try:
            open_manager(instance_info(mo2_ini.value)['exe'])
        except (OSError, ValueError):
            tell('Сначала укажите папку MO2. Если он установлен не портативно, запустите его сами.')

    def open_vortex(e=None):
        open_manager((vortex_exe.value or '').strip())

    def refresh_mo2_profiles(e=None):
        try:
            names=available_profiles(mo2_ini.value)
            mo2_profile.options=[ft.dropdown.Option(p) for p in names]
            preferred=config.get('mo2_profile') or instance_info(mo2_ini.value)['profile']
            mo2_profile.value=preferred if preferred in names else (names[0] if len(names)==1 else None)
            managed_status.value=f'Профилей найдено: {len(names)}. Выберите профиль вручную.'
        except (OSError, ValueError) as exc:
            managed_status.value=f'Не удалось найти профили: {exc}'
        redraw()

    def install_archive_into_mo2(e=None):
        try:
            if not mo2_profile.value: raise MO2InstallError('Сначала выберите профиль MO2')
            archive = archive_field.value.strip().strip('\"')
            if not archive: raise MO2InstallError('Выберите архив на вкладке Загрузки')
            location, info = stage_archive(archive, PATHS['staging'])
            result=install_simple(location, mo2_ini.value, mo2_profile.value, mo2_name.value or Path(archive).stem)
            install_stage.value=str(location)
            managed_status.value=f"Архив {info['name']} распакован. В MO2 включён {result['mod']} (профиль {result['profile']})."
        except (MO2InstallError, DownloadError, OSError, UnicodeError, ValueError) as exc:
            managed_status.value=f'Установка архива MO2 остановлена: {exc}'
        redraw()

    def install_into_mo2(e=None):
        try:
            if not mo2_profile.value: raise MO2InstallError('Сначала выберите профиль MO2')
            result=install_simple(install_stage.value,mo2_ini.value,mo2_profile.value,mo2_name.value)
            managed_status.value=f"Установлен и включён в профиле {result['profile']}: {result['mod']}"
        except (MO2InstallError, OSError, UnicodeError) as exc:
            managed_status.value=f'Установка MO2 отклонена: {exc}'
        redraw()

    def managed_install(e=None):
        try:
            target = Path(install_target.value).expanduser().resolve()
            # Do not let this installer manipulate known externally managed downloads.
            if target == Path(destination_folder()).resolve():
                raise InstallError('Выбрана папка загрузок менеджера. Для MO2/Vortex установка выполняется внутри их интерфейса.')
            mid = deploy(install_stage_custom.value, target, install_log, 'Manual installation')
            install_id_field.value = mid
            managed_status.value = f'Установлено. ID: {mid}. Резервные копии сохранены.'
            refresh_managed()
        except (InstallError, OSError, ValueError, DestinationError) as exc:
            managed_status.value = f'Ошибка установки: {exc}'
        redraw()

    def managed_uninstall(e=None):
        try:
            uninstall(install_log, install_id_field.value.strip())
            managed_status.value = 'Файлы этого мода удалены, исходные версии восстановлены.'
            refresh_managed()
        except (InstallError, OSError, ValueError) as exc:
            managed_status.value = f'Удаление остановлено: {exc}'
        redraw()

    def refresh_managed(e=None):
        managed_list.controls.clear()
        for manifest in sorted(install_log.glob('*/manifest.json')) if install_log.is_dir() else []:
            try:
                record = json.loads(manifest.read_text(encoding='utf-8'))
                if record.get('status') != 'installed': continue
                mid = record.get('id','')
                if len(mid) != 32:continue
                def choose(e, mid=mid):
                    install_id_field.value = mid
                    redraw()
                managed_list.controls.append(ft.Row([
                    ft.Text(f"{record.get('name','Мод')} · {len(record.get('files', []))} файлов · {record.get('target','')}",
                            expand=True, size=12, max_lines=1),
                    ft.Button('Выбрать', on_click=choose)]))
            except (OSError, ValueError, TypeError):continue
        if not managed_list.controls:
            managed_list.controls.append(ft.Text('Установок под управлением Nexus Tab Installer пока нет.'))
        redraw()

    def manual_install_controls():
        return [
            ft.Text('MO2/Vortex: удаляйте установленные моды в соответствующем менеджере. Не изменяйте их папки вручную.', color='#B5BBD1', size=12),
            ft.Text('Установка простого мода напрямую в MO2', weight=ft.FontWeight.BOLD),
            ft.Text('Закройте MO2. Для FOMOD и сложных установщиков используйте сам MO2. Из staging будут скопированы файлы в отдельный мод и активированы в выбранном профиле.', size=12),
            install_stage, mo2_name,
            ft.Row([ft.Button('Распаковать архив и включить в MO2', on_click=install_archive_into_mo2),
                    ft.Button('Установить подготовленную папку в MO2', on_click=install_into_mo2)], wrap=True),
            ft.Divider(color='#30364B'),
            ft.Text('Собственная управляемая установка (экспериментально)', weight=ft.FontWeight.BOLD),
            ft.Text('Для папок, которыми НЕ управляют MO2/Vortex. Перед установкой создаются резервные копии, удаление восстанавливает их. Изменённые файлы не удаляются.', size=12),
            install_stage_custom, install_target,
            ft.Row([ft.Button('Установить с резервными копиями', on_click=managed_install),
                    ft.Button('Обновить список моих установок', on_click=refresh_managed)], wrap=True),
            managed_list,
            install_id_field,
            ft.Button('Удалить свою установку и восстановить файлы', on_click=managed_uninstall),
            managed_status,
        ]

    def make_settings():
        # One scroll for everything: nested scrolling panels can render as a grey Flutter error rectangle.
        return settings_view(
            general=[language_choice,
                     ft.Row([notify_opt, ft.TextButton('Проверить уведомление', on_click=test_notification)], wrap=True),
                     auto_scan_opt, updates_auto, requirements_mode,
                     ft.Text('Названия, размеры, требования и новые версии программа спрашивает у Nexus одним запросом, без ключа и без данных о вас: только номера модов.', size=12, color='#AAB2C7')],
            key=key, remember_key=remember_key, auto_free_opt=auto_free_opt, pace_choice=pace_choice, pace_custom=pace_custom, pace_hint=pace_hint, pace_warning=pace_warning,
            browser_download_folder=browser_download_folder, check_key=check_key,
            open_api_help=lambda e: webbrowser.open('https://www.nexusmods.com/settings/api-keys'),
            open_browser_setup=open_browser_setup, enable_nxm=enable_nxm,
            destination_panel=destination_options_panel.content, destination_mode=destination_mode,
            mo2_ini_note=ft.Text('Если MO2 портативный, вставьте в «Куда устанавливать» папку установки или ModOrganizer.exe.', color='#AEB5CB', size=12),
            folder=folder, destination_hint=destination_hint, find_managers=detect_managers, detected_managers=detected_managers,
            validate_destination=validate_destination, store_config=store_config,
            pack_path=pack_path, pack_save=pack_save, pack_load=pack_load,
            manual_install=manual_install_controls(), refresh=redraw)

    def select_all_workflow(value):
        trace.emit('ui.action', step='select')
        plan.set_all(value)
        save_choices()
        refresh_workflow()

    def toggle_help(e=None):
        help_panel.visible = not help_panel.visible
        redraw()

    def open_key_settings(e=None):
        persist_config(silent=True)
        change_tab('settings')

    def show_import_options(e=None):
        import_options.visible = not import_options.visible
        redraw()

    def toggle_destination_options(e=None):
        destination_options_panel.visible = not destination_options_panel.visible
        describe_managers()
        redraw()

    def open_nexus_key_page(e=None):
        webbrowser.open('https://www.nexusmods.com/settings/api-keys')

    # Short help belongs behind Info, not in the primary working area.
    help_panel = ft.Container(visible=False, border_radius=12, padding=16,
        bgcolor='#1B2031', content=ft.Column([
            ft.Text('Как пользоваться', weight=ft.FontWeight.BOLD, size=16),
            ft.Text('1. Нажмите «Найти моды» или импортируйте ссылки.', size=13),
            ft.Text('2. Выберите нужные моды и назначение для каждого.', size=13),
            ft.Text('3. Нажмите «Скачать выбранные».', size=13),
            ft.Divider(color='#30364B'),
            ft.Text('Найти все фоновые вкладки средствами Windows не всегда возможно, поэтому подключите '
                    'расширение браузера. Файлы Free оно скачивает само; если Nexus попросит войти '
                    'или пройти проверку, сделайте это в его окне.',
                    size=12, color='#AAB2C7'),
            ft.TextButton('Где получить API-ключ Nexus', on_click=open_nexus_key_page),
        ], spacing=8))
    api_notice_title = ft.Text('API-ключ Nexus не обязателен', size=15, weight=ft.FontWeight.BOLD,
                               color='#F7D4A0')
    api_notice = ft.Container(visible=False,
        bgcolor='#292331', border_radius=14, padding=14,
        border=ft.Border.all(1, '#59435E'),
        content=ft.Column([
            api_notice_title,
            ft.Row([
                ft.Button('Настроить Nexus', icon=ft.Icons.SETTINGS, on_click=open_key_settings),
                ft.TextButton('Получить API-ключ', on_click=open_nexus_key_page),
            ], spacing=8),
        ], spacing=9))
    import_options = ft.Container(visible=not browser_paired['value'], border_radius=14,
        bgcolor='#1B2031', padding=14,
        content=ft.Column([
            ft.Text('Добавить моды вручную', weight=ft.FontWeight.W_600),
            link_field,
            ft.Button('Добавить ссылки', icon=ft.Icons.ADD_LINK, on_click=add_text),
            ft.Divider(color='#30364B'),
            ft.Text('Загрузить или сохранить список JSON', size=12, color='#AAB2C7'),
            pack_path,
            ft.Button('Открыть JSON', on_click=pack_load),
            ft.Button('Сохранить список JSON', on_click=pack_save),
        ], spacing=9))
    destination_options_panel = ft.Container(visible=False, bgcolor='#1B2031',
        border_radius=14, padding=16,
        content=ft.Column([
            ft.Text('Настроить место установки', weight=ft.FontWeight.BOLD, size=15),
            manager_status,
            ft.Text('Mod Organizer 2', weight=ft.FontWeight.W_600),
            ft.Text('Файл появляется во вкладке Downloads с данными Nexus. Простой мод сразу ставится и включается, если MO2 закрыт. '
                    'Мод с установщиком или при открытом MO2 ждёт двойного щелчка в Downloads.', color='#AAB2C7', size=12),
            mo2_ini,
            ft.Row([ft.Button('Найти MO2', icon=ft.Icons.SEARCH, on_click=find_mo2),
                    ft.Button('Открыть MO2', icon=ft.Icons.OPEN_IN_NEW, on_click=open_mo2)], spacing=10, wrap=True),
            mo2_profile,
            ft.Button('Найти профили MO2', on_click=refresh_mo2_profiles),
            ft.Text('Vortex', weight=ft.FontWeight.W_600),
            ft.Text('Закрытый Vortex запускается с нужной игрой и сам устанавливает файл. Открытый на этой игре тоже. Если в нём открыта '
                    'другая игра, файл кладётся в загрузки нужной, а Install нажимаете вы. Так мод не попадёт в чужую игру.',
                    color='#AAB2C7', size=12),
            vortex_exe,
            ft.Row([ft.Button('Найти Vortex', icon=ft.Icons.SEARCH, on_click=find_vortex_program),
                    ft.Button('Проверить Vortex', on_click=check_vortex),
                    ft.Button('Открыть Vortex', icon=ft.Icons.OPEN_IN_NEW, on_click=open_vortex)], spacing=10, wrap=True),
            vortex_folder,
            ft.Text('Папка игры', weight=ft.FontWeight.W_600),
            game_folder,
            ft.Button('Сохранить пути', on_click=store_config),
        ], spacing=10))

    browser_guide_browser = ft.Dropdown(
        label='Какой браузер подключить?', value='edge', width=270,
        options=[ft.dropdown.Option(code, setup.name) for code, setup in BROWSERS.items()],
        # Flet 0.80+: Dropdown selection uses on_select; on_change was removed.
        on_select=render_browser_steps)
    browser_guide_steps = ft.Column(spacing=10)
    browser_guide_note = ft.Text('', size=12, color='#CEAD85')
    browser_guide_path = ft.Text('', size=12, color='#B8C1D9', selectable=True)
    # Open by itself on the first start, when the browser has never been connected.
    browser_setup_panel = ft.Container(
        visible=not browser_paired['value'], bgcolor='#1B2031', border_radius=16, padding=18,
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.EXTENSION, color='#B5A0FF'),
                ft.Text('Подключение браузера — один раз', weight=ft.FontWeight.BOLD, size=17),
            ], spacing=10),
            ft.Text('Расширение передаёт программе только ссылки на моды Nexus Mods. Код копировать не нужно. '
                    'Если вы перенесли папку программы, загрузите расширение заново из нового места.',
                    color='#B8C1D9', size=13),
            browser_guide_browser,
            ft.TextButton('Наглядная инструкция с примерами экранов',
                icon=ft.Icons.MENU_BOOK, on_click=open_browser_visual_guide),
            browser_guide_steps,
            browser_guide_note,
            browser_guide_path,
            ft.Row([
                ft.Button('Открыть расширения', icon=ft.Icons.OPEN_IN_BROWSER,
                          on_click=open_selected_browser_extensions),
                ft.Button('Показать папку', icon=ft.Icons.FOLDER_OPEN,
                          on_click=show_selected_extension_folder),
                ft.Button('Проверить связь', icon=ft.Icons.REFRESH,
                          on_click=check_browser_connection),
            ], wrap=True, spacing=8),
            browser_guide_status,
        ], spacing=13))

    browser_line = ft.Container(padding=ft.Padding.symmetric(horizontal=12, vertical=6), bgcolor='#1D2132', border_radius=12,
        content=ft.Column([
            helper_status,
            ft.Row([ft.TextButton('Подключить браузер (инструкция)',
                                  icon=ft.Icons.EXTENSION, on_click=toggle_browser_setup)], spacing=8),
        ], spacing=0))

    def make_start():
        update_browser_status()
        start_button.content = 'Найти моды'
        start_button.icon = ft.Icons.SEARCH
        start_button.on_click = start_workflow
        download_selected_button.on_click = download_selected
        select_all_button.on_click = lambda e: select_all_workflow(True)
        clear_all_button.on_click = lambda e: select_all_workflow(False)
        cancel_button.on_click = cancel_workflow
        resume_button.on_click = resume_auto
        return ft.Column([
            # The commands stay in sight however long the list is.
            ft.Row([
                ft.Text('Мои моды', size=24, weight=ft.FontWeight.BOLD),
                workflow_count,
                ft.Container(expand=True),
                ft.TextButton('Настроить MO2 / Vortex / папку игры', icon=ft.Icons.FOLDER_OUTLINED,
                              on_click=toggle_destination_options),
                ft.IconButton(ft.Icons.INFO_OUTLINE, tooltip='Как пользоваться', on_click=toggle_help),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(bgcolor='#1B2031', padding=12, border_radius=14,
                content=ft.Column([
                    ft.Row([start_button,
                            ft.Button('Вставить ссылки или открыть JSON', icon=ft.Icons.ADD_LINK, on_click=show_import_options),
                            cancel_button], wrap=True, spacing=10),
                    ft.Row([download_selected_button, target_for_all, select_all_button, clear_all_button],
                           wrap=True, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Container(padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                        bgcolor='#151A2A', border_radius=9,
                        content=workflow_summary),
                    batch_progress,
                ], spacing=8)),
            ft.Column([
                premium_notice,
                api_notice,
                disk_notice,
                requirements_banner,
                help_panel,
                browser_line,
                browser_setup_panel,
                import_options,
                destination_options_panel,
                workflow_rows,
                ft.Row([ft.TextButton('Скачанные файлы', icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                      on_click=lambda e: change_tab('downloads'))]),
            ], scroll=ft.ScrollMode.AUTO, expand=True, spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
        ], expand=True, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # A dedicated, simple support-oriented log screen. No live API keys,
    # full URLs, filenames or cookies can appear: TraceLog stores allowlisted data.
    log_filter = ft.Dropdown(label='Показать', width=220, value='main',
        options=[ft.dropdown.Option('main', 'Главное'),
                 ft.dropdown.Option('all', 'Все события'),
                 ft.dropdown.Option('error', 'Ошибки'),
                 ft.dropdown.Option('browser', 'Браузер'),
                 ft.dropdown.Option('download', 'Загрузки'),
                 ft.dropdown.Option('app', 'Программа')])
    log_headline = ft.Text('', color='#B7C5E5', size=13)
    log_diagnosis = ft.Text('', color='#D9C8FF', size=13)
    log_rows = ft.Column(spacing=7)
    log_hint = ft.Text('События записываются автоматически при работе программы.',
                       size=12, color='#ABB5CF')

    def refresh_log_view(e=None):
        if e is not None:
            trace.emit('ui.log_refresh')
        try:
            records = trace.read_recent(350)
            chosen = log_filter.value or 'main'
            if chosen == 'main':
                records = [r for r in records if is_main_event(r.get('event', ''))]
            elif chosen != 'all':
                records = [r for r in records if classify_event(r.get('event','')) == chosen]
            visible = records[-90:]
            total_errors = sum(classify_event(r.get('event','')) == 'error'
                               for r in trace.read_recent(350))
            log_headline.value = f'Последние события: {len(records)} · Ошибок: {total_errors} · На экране: {len(visible)}'
            log_diagnosis.value = 'Последний этап: ' + diagnose_trace(trace.read_recent(350))
            log_rows.controls.clear()
            if not visible:
                log_rows.controls.append(ft.Container(
                    padding=15, bgcolor='#202637', border_radius=10,
                    content=ft.Text('Пока нет событий для этого фильтра.', color='#BBC3D7')))
            for rec in reversed(visible):
                cat = classify_event(rec['event'])
                color = '#F0A09C' if cat == 'error' else '#B0A0F9' if cat == 'browser' else '#9AD7BB'
                stamp = local_clock(rec.get('ts', ''))
                details = rec.get('data',{})
                fields = []
                for key in ('job_id','mod_id','file_id','step','state','result','reason','http_status','area'):
                    if key in details:
                        val = str(details[key])
                        if key == 'job_id': val = val[:8]
                        fields.append(f'{key}: {val}')
                if 'frames' in rec:
                    fields += [f"{x['file']}:{x['line']}" for x in rec['frames'] if isinstance(x,dict)]
                log_rows.controls.append(ft.Container(
                    bgcolor='#1B2233', border_radius=9, padding=10,
                    content=ft.Column([
                        ft.Row([ft.Text(stamp, color='#95A1BC', width=68, size=12),
                                ft.Icon(ft.Icons.ERROR_OUTLINE if cat=='error' else ft.Icons.CIRCLE_OUTLINED,
                                        size=15,color=color),
                                ft.Text(describe_event(rec), color='#F0EEFF', size=13,expand=True)], spacing=7),
                        ft.Text('  ·  '.join(fields), visible=bool(fields),
                                color='#9AA7BD', size=11, selectable=True)
                    ],spacing=3)))
            log_hint.value = ('События автоматически записываются в data/logs/download-trace.jsonl. '
                              'Отчёт ZIP предназначен для передачи специалисту; приватные ссылки не сохраняются.')
            if active['tab'] == 'logs': redraw()
        except Exception as exc:
            trace.emit_exception(exc, area='ui')
            log_headline.value = 'Не удалось прочитать журнал. Проверьте папку data/logs.'
            if active['tab'] == 'logs': redraw()

    log_filter.on_select = refresh_log_view

    def make_logs():
        trace.emit('ui.log_opened')
        refresh_log_view()
        return ft.Column([
            ft.Row([ft.Text('Журнал работы', size=26, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.Button('Сохранить отчёт ZIP', icon=ft.Icons.SAVE, on_click=export_diagnostics)],
                   spacing=9),
            ft.Text('Здесь видно, что делает программа и на каком этапе остановилось скачивание.',
                    size=13, color='#B6C2D8'),
            ft.Container(bgcolor='#1C2334', border_radius=12, padding=15,
                content=ft.Column([
                    ft.Text('Как получить отчёт об ошибке',weight=ft.FontWeight.BOLD),
                    ft.Text('1. Запустите скачивание.  2. При остановке откройте «Журнал».  '
                            '3. Проверьте последние строки.  '
                            '4. Нажмите «Сохранить отчёт ZIP» и отправьте файл diagnostics-for-support.zip.',
                            size=12,color='#D0D6E3'),
                ],spacing=5)),
            ft.Row([log_filter,ft.Button('Обновить',icon=ft.Icons.REFRESH,
                                         on_click=refresh_log_view)],spacing=10),
            log_headline,
            ft.Container(bgcolor='#24213B', border_radius=10, padding=12, content=log_diagnosis),
            ft.Container(expand=True, content=ft.Column([log_rows],scroll=ft.ScrollMode.AUTO,expand=True)),
            log_hint,
        ],expand=True,spacing=12)

    tab_controls = [
        ('start','Мои моды',ft.Icons.VIEW_LIST_ROUNDED),
        ('downloads','Загрузки',ft.Icons.DOWNLOADING_ROUNDED),
        ('settings','Настройки',ft.Icons.TUNE_ROUNDED),
        ('logs','Журнал',ft.Icons.RECEIPT_LONG),
    ]
    nav_buttons = []
    def change_tab(tab):
        # No surprise loss of fields when moving between screens.
        persist_config(silent=True)
        active['tab'] = tab
        main_area.controls.clear()
        main_area.controls.append({'start':make_start,'downloads':make_downloads,'settings':make_settings,'logs':make_logs}[tab]())
        for name, b in nav_buttons:
            b.bgcolor = '#35315A' if name == tab else '#171926'
        redraw()
        if tab == 'downloads':
            refresh_jobs()
            refresh_library()

    downloads_count = ft.Text('', size=11, color='#10121B', weight=ft.FontWeight.BOLD)
    downloads_badge = ft.Container(content=downloads_count, bgcolor=ACCENT, border_radius=8, visible=False,
                                   padding=ft.Padding.symmetric(horizontal=6, vertical=0), tooltip='Вышли новые версии')
    for name, label, icon in tab_controls:
        marks = [ft.Container(expand=True), downloads_badge] if name == 'downloads' else []
        nav_buttons.append((name, ft.Container(
            content=ft.Row([ft.Icon(icon, color='#B6A9FF', size=21), ft.Text(label, size=14, weight=ft.FontWeight.W_600), *marks], spacing=12),
            padding=12, border_radius=10, bgcolor='#171926',
            on_click=lambda e, t=name: change_tab(t))))

    language_buttons = {code: ft.Container(content=ft.Text(code.upper(), size=12, weight=ft.FontWeight.W_600), border_radius=7,
                                           padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                                           tooltip='Язык / Language', on_click=lambda e, chosen=code: set_language(chosen))
                        for code in ('ru', 'en')}
    paint_language_switch()
    frame = ft.Row([
        ft.Container(width=212, bgcolor='#171926', padding=14, content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.EXTENSION_ROUNDED, color='#A28CFF', size=27),
                    ft.Column([ft.Text('NEXUS TAB', size=18, weight=ft.FontWeight.BOLD),
                               ft.Text('INSTALLER', size=10, color='#929AB4')], spacing=0)], spacing=8),
            ft.Container(height=14),
            *[btn for _, btn in nav_buttons],
            ft.Container(expand=True),
            ft.Container(on_click=lambda e: open_browser_setup(), ink=True, border_radius=8, padding=6,
                         tooltip='Подключить браузер (инструкция)',
                         content=ft.Row([side_browser_dot, ft.Container(expand=True, content=side_browser_text)], spacing=6)),
            ft.Row(list(language_buttons.values()), spacing=4),
            ft.Text('v1.0.0 · Portable', color='#8F96AD', size=12),
        ], expand=True, spacing=6)),
        ft.Container(expand=True, padding=18,
            content=ft.Column([
                server_notice,
                pairing_banner,
                attention_banner,
                main_area,
                ft.Container(padding=ft.Padding.symmetric(horizontal=12, vertical=8), bgcolor='#211F35', border_radius=9, content=status),
            ], expand=True, spacing=10))
    ], expand=True, spacing=0)
    screen_roots.append(frame)
    page.add(frame)
    if browser_setup_panel.visible:
        render_browser_steps()
    change_tab('start')
    refresh_workflow()
    refresh_jobs()
    refresh_library()
    refresh_present()
    if moved_archives:
        tell(f"Старые файлы разложены по папкам сессий: {plural(moved_archives, 'файл', 'файла', 'файлов')}.")
    server = None
    try:
        server = ImportServer(('127.0.0.1', PORT), server_factory(token, incoming, snapshots, pairing, auto_free, trace,
                                                                  language=lambda: screen.language))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        trace.emit('server.started')
    except OSError as exc:
        trace.emit('server.start_failed', reason='port_unavailable')
        server_notice.visible = True
        server_notice.content.value = (f'Не удалось запустить подключение браузеров (порт {PORT}). '
                                       'Закройте старые окна Nexus Tab Installer и перезапустите программу. '
                                       f'Подробности: {exc}')
        tell(f'Не удалось запустить импорт вкладок: порт {PORT} занят. {exc}')
        redraw()

    async def process_queue():
        import asyncio
        import time
        browser_poll_tick = 0
        last_redraw = 0.0
        attention_said = {'value': ''}
        while True:
            try:
                browser_poll_tick += 1
                if workflow['running'] != awake['value']:
                    # A batch of many files takes hours: Windows must not fall asleep.
                    awake['value'] = workflow['running']
                    set_keep_awake(awake['value'])
                if browser_poll_tick % 3 == 0:
                    needed = auto_free.attention() if workflow['running'] else ''
                    premium_now = premium['value'] is True or auto_free.premium_seen
                    session_now = workflow['current']
                    tasks = session_now.task_counts() if session_now is not None and workflow['running'] else {}
                    total_tasks = sum(tasks.values())
                    progress_now = (tasks['done'] + tasks['failed']) / total_tasks if total_tasks else None
                    progress_value = progress_now or 0
                    if (needed != attention_said['value'] or premium_now != premium_notice.visible
                            or (progress_now is not None) != batch_progress.visible or progress_value != batch_progress.value):
                        if needed and needed != attention_said['value']:
                            announce('Нужно ваше действие', needed)
                        attention_said['value'] = needed
                        attention_text.value = needed
                        attention_banner.visible = bool(needed)
                        premium_notice.visible = premium_now
                        batch_progress.visible = progress_now is not None
                        batch_progress.value = progress_value
                        update_workflow_controls()
                        redraw()
                if browser_poll_tick % 3 == 0 and workflow['running'] and not jobs.busy() and not auto_free.has_leased():
                    pause_seconds, pause_reason = auto_free.wait_status()
                    left = auto_free.remaining()
                    if pause_seconds > 0 and left:
                        workflow_summary.value = (f'{WAIT_TEXT[pause_reason]}: {clock_text(pause_seconds)}. Осталось файлов: {left}, '
                                                  f'примерно {duration_text(pacer.estimate(left))} без учёта самих загрузок.')
                        redraw()
                if browser_poll_tick % DISK_WATCH_TICKS == 0 and workflow['running']:
                    warn_about_disk(through_browser=bool(auto_free_opt.value))
                if browser_poll_tick % 3 == 0 and active['tab'] == 'downloads' and (workflow['running'] or now_area.visible):
                    refresh_jobs()
                if browser_poll_tick % 15 == 0 and active['tab'] == 'logs':
                    refresh_log_view()
                if browser_poll_tick % 10 == 0 and workflow['running']:
                    state = auto_free.state()
                    trace.emit('ui.heartbeat', count=len(state['jobs']), stage='active' if state['enabled'] else 'waiting')
                if browser_poll_tick % 15 == 0 and active['tab'] == 'start':
                    if update_helper_status():
                        if auto_free.has_live_helper() and source_text(status).startswith(('Разрешение выдано', 'Разрешение принято')):
                            status.value = 'Канал загрузок отвечает. Теперь можно запускать выбранные файлы.'
                        redraw()
                    if browser_setup_panel.visible:
                        check_browser_connection()
                # With a connected extension the browser reports its files itself;
                # polling Downloads as well could deliver the same archive twice.
                if (browser_poll_tick % 7 == 0 and len(free_queue) and browser_download_watcher.pending
                        and not auto_free.has_live_helper()):
                    for game, mid, fid, path in browser_download_watcher.poll():
                        incoming.put(('browser_archive', game, mid, fid, path))
                # Browser protocol handler writes validated links to a local inbox.
                # Polling keeps all UI modifications on the Flet event loop.
                # Do not consume expiring NXM links before an API key is available.
                if key.value and key.value.strip() and not (workflow['current'] and workflow['current'].cancelled.is_set()):
                    for path, nxm_uri in pending_nxm():
                        if import_nxm(uri=nxm_uri):
                            acknowledge_nxm(path)
                if waiting_for_helper['value'] and workflow['running'] and auto_free.has_live_helper():
                    # Pending jobs are claimed by the extension on its own poll.
                    waiting_for_helper['value'] = False
                    workflow_summary.value = 'Расширение подключено: браузер начинает скачивание файлов.'
                    refresh_workflow()
                if browser_poll_tick % 10 == 0:
                    # A browser that stopped answering: retry its file elsewhere.
                    expired = auto_free.tick()
                    if expired:
                        handle_auto_outcome(expired)
                    # Safety net: an error in one handler must not leave the batch open.
                    finish_if_done()
                if auto_free.stalled_page(threshold=45) and not stall_warning['value']:
                    stall_warning['value'] = True
                    pending_step = next((j['step'] for j in auto_free.state()['jobs']
                                         if j['state'] == 'leased'), 'Открываем страницу')
                    workflow_summary.value = (
                        f'Браузер давно не сообщал о ходе загрузки (этап «{pending_step}»). '
                        'Если окно браузера закрыто или свёрнуто, откройте его — задание будет повторено автоматически.')
                    refresh_workflow()
                elif stall_warning['value'] and not auto_free.stalled_page(threshold=45):
                    stall_warning['value'] = False
                if (auto_free.needs_browser(grace=45) and workflow['running']
                        and not waiting_for_helper['value']):
                    waiting_for_helper['value'] = True
                    workflow_summary.value = ('Файлы ждут браузер, но расширение Nexus Tab Collector не отвечает. '
                                              f'Откройте браузер с установленным расширением (версия {REQUIRED_EXTENSION}) — '
                                              'загрузка продолжится сама.')
                    refresh_workflow()
                changed = library_changed = list_changed = False
                while True:
                    try:
                        value = incoming.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(value, list):
                        add(value)
                    elif isinstance(value, tuple):
                        typ, *args = value
                        if typ == 'restore_unfinished':
                            trace.emit('ui.mods_restored', count=len(args[0]))
                            add(args[0])
                            workflow_summary.value = (f'Прошлая загрузка не закончилась, её список восстановлен: {len(args[0])} модов. '
                                                      'Нажмите «Скачать выбранные», уже скачанные файлы будут пропущены.')
                            refresh_workflow()
                        elif typ == 'pair_request':
                            update_pairing_requests()
                            check_browser_connection()
                        elif typ == 'browser_sync':
                            trace.emit('ui.browser_sync_received')
                            if not browser_paired['value']:
                                browser_paired['value'] = True
                                persist_config(silent=True)
                            # Pairing status must update immediately after a
                            # successful snapshot. Previously the onboarding
                            # screen could keep saying 'not connected' until
                            # the user pressed Check connection again.
                            # The instructions have done their job once the browser is connected.
                            if browser_setup_panel.visible and not setup_opened['value']:
                                browser_setup_panel.visible = False
                            if browser_setup_panel.visible:
                                check_browser_connection()
                            if snapshots.sources(max_age=120) and not plan.items and not workflow['running']:
                                status.value = ('Получены свежие вкладки расширения. '
                                                'Можно нажать «Найти моды».')
                                # Once per launch: later the person decides when the list changes.
                                found_tabs = snapshots.collect(max_age=120) if auto_scan_opt.value and not auto_scan['done'] else []
                                if found_tabs:
                                    auto_scan['done'] = True
                                    add(found_tabs)
                            update_browser_status()  # last: it also sends all of the above to the window
                        elif typ == 'auto_free_update':
                            state = auto_free.state()
                            trace.emit('ui.auto_update', count=len(state['jobs']))
                            session = workflow['current']
                            if session is not None and workflow['running'] and not session.cancelled.is_set():
                                total = len(state['jobs'])
                                finished = sum(1 for j in state['jobs'] if j['state'] in ('confirmed', 'failed'))
                                for j in state['jobs']:
                                    if j['state'] != 'leased':
                                        continue
                                    item = session_item(session, j['game'], j['mod_id'])
                                    detail = f"попытка {j['attempts']}" if j['attempts'] > 1 else ''
                                    if j['total']:
                                        detail = f"{j['bytes'] * 100 // j['total']} %"
                                    if item is not None:
                                        session.set_status(item, 'Браузер: ' + (j['step'] or 'работает'), detail)
                                    workflow_summary.value = (f'Браузер: файл {min(finished + 1, total)} из {total}. '
                                                              + ('Нужно ваше действие, подробности вверху окна.'
                                                                 if auto_free.attention() else (j['step'] or '')))
                                if state['paused']:
                                    workflow_summary.value = 'Загрузка приостановлена, подробности вверху окна.'
                            refresh_workflow()
                        elif typ == 'auto_outcome':
                            handle_auto_outcome(args[0])
                        elif typ == 'job':
                            changed = True
                            jobid = args[0]
                            job = jobs.items.get(jobid)
                            # Progress events queue up: a final state is handled exactly once.
                            settled_now = (job is not None and jobid not in settled_jobs
                                           and job.state in ('finished','failed','cancelled','interrupted'))
                            if settled_now:
                                settled_jobs.add(jobid)
                                library_changed = True
                                trace.emit('ui.download_job_terminal', state=job.state)
                            route = job_routes.get(jobid)
                            if settled_now and route and job.state in ('failed', 'cancelled'):
                                item, _target, session, _options = route
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    if (job.state == 'failed' and job.fallback == 'browser'
                                            and (auto_free.requeue(job.game, job.mod_id, job.file_id,
                                                                   transfer='browser', task=job.task)
                                                 or auto_free.add([{'game': job.game, 'mod_id': job.mod_id,
                                                                    'file_id': job.file_id, 'task': job.task}]))):
                                        # The CDN or API refused outside the browser:
                                        # let the browser itself get the file instead.
                                        trace.emit('ui.direct_link_rejected', mod_id=job.mod_id, file_id=job.file_id)
                                        job.fallback = ''
                                        session.set_status(item, 'Повтор: скачивание средствами браузера')
                                    else:
                                        session.set_status(item, 'Ошибка загрузки' if job.state == 'failed' else 'Отменено', job.error[:180])
                                        if job.task:
                                            session.finish(job.task, False)
                                    refresh_workflow()
                            if settled_now and route and job.state == 'finished':
                                item, target, session, options = route
                                installing_jobs.add(jobid)
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    session.set_status(item, 'Устанавливается' if target not in ('library','vortex') else 'Обработка архива')
                                def finish_archive(job=job, item=item, target=target, session=session, options=options):
                                    if session.cancelled.is_set():
                                        return ('delivered', job.id, item, 'Отменено', '', session)
                                    try:
                                        archive_path = Path(job.directory) / safe_name(job.filename)
                                        record_archive(archive_path, item['game'], item['mod_id'], job.file_id)
                                        if target == 'library':
                                            return ('delivered', job.id, item, 'Готово', f'В библиотеке: {archive_path.name}', session)
                                        stage, detail = deliver(archive_path,
                                                                {**item, 'download_file_id': job.file_id}, target, **options)
                                        return ('delivered', job.id, item, stage, detail, session)
                                    except Exception as exc:
                                        return ('delivered', job.id, item, 'Ошибка установки', str(exc), session)
                                background(finish_archive)
                            if settled_now and not route and job.state == 'finished':
                                # A file from outside a download run (NXM): nothing more to deliver.
                                record_archive(Path(job.directory) / safe_name(job.filename), job.game, job.mod_id, job.file_id)
                        elif typ == 'browser_archive':
                            game, mid, fid, source_path = args
                            trace.emit('ui.browser_archive_detected', mod_id=mid, file_id=fid)
                            session = workflow['current']
                            if not session or session.collection is None or session.cancelled.is_set():
                                continue
                            task_key = session.task_for(game, mid, fid)
                            if not free_queue.contains_file(game, mid, fid) and not task_key:
                                continue
                            matching = session_item(session, game, mid)
                            if matching is None:
                                continue
                            target = workflow.get('targets', {}).get((game, mid), 'library')
                            options = workflow.get('destination_options', delivery_options())
                            session.set_status(matching, 'Получен файл из браузера', Path(source_path).name)
                            free_queue.remove_file(game, mid, fid)
                            auto_free.acknowledge_external(game, mid, fid)
                            free_count.value = f'Ожидают подтверждения: {len(free_queue)}'
                            def import_browser_archive(source=source_path, item=matching, destination=target,
                                                       options=options, session=session, file_id=fid, task_key=task_key):
                                try:
                                    dst = session.collection.folder
                                    dst.mkdir(parents=True, exist_ok=True)
                                    saved = export_archive(source, dst)
                                    record_archive(saved, item['game'], item['mod_id'], file_id)
                                    if destination == 'library':
                                        return ('task_delivered', task_key, item, 'Готово', f'В библиотеке: {saved.name}', session, True)
                                    stage, detail = deliver(saved, {**item, 'download_file_id':file_id},
                                                            destination, **options)
                                    return ('task_delivered', task_key, item, stage, detail, session, True)
                                except Exception as exc:
                                    return ('task_delivered', task_key, item, 'Ошибка импорта', str(exc), session, False)
                            background(import_browser_archive)
                            if len(free_queue) and not auto_free_opt.value:
                                next_free()
                            refresh_workflow()
                        elif typ == 'workflow_refresh':
                            list_changed = True  # many of these may wait here: one redraw is enough
                        elif typ == 'error':
                            tell(args[0])
                            if workflow['running']:
                                workflow['running'] = False
                                workflow_summary.value = 'Ошибка выполнения: ' + str(args[0])[:160]
                                refresh_workflow()
                        elif typ == 'result':
                            result = args[0]
                            if result[0] in ('task_delivered', 'task_failed', 'browser_delivered'):
                                if result[0] == 'browser_delivered':
                                    _, item, stage, detail, session = result
                                    task_key, ok = '', stage not in ('Ошибка импорта', 'Ошибка установки')
                                elif result[0] == 'task_failed':
                                    _, task_key, item, stage, detail, session = result
                                    ok = False
                                else:
                                    _, task_key, item, stage, detail, session, ok = result
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    session.set_status(item, stage, detail)
                                    if task_key:
                                        session.finish(task_key, ok)
                                    refresh_workflow()
                                    refresh_library()
                                    finish_if_done()
                            elif result[0] == 'browser_file_stored':
                                _, task_key, item, stored, target, options, session, fid = result
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    deliver_task(item, task_key, stored, target, options, session, fid)
                            elif result[0] == 'prepared':
                                trace.emit('ui.file_catalog_received')
                                _, n, browser_specs, reused, misses, is_premium, session, planned_kb = result
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    workflow['prepared'] = session
                                    if is_premium is not None:
                                        premium['value'] = is_premium
                                    for item, spec, existing in reused:
                                        trace.emit('ui.archive_reused', mod_id=spec['mod_id'], file_id=spec['file_id'])
                                        session.set_status(item, 'Уже скачан ранее', existing.name)
                                        deliver_task(item, spec['task'], existing,
                                                     workflow.get('targets', {}).get(identity(item), 'library'),
                                                     workflow.get('destination_options', delivery_options()),
                                                     session, spec['file_id'])
                                    # Manual-download fallback: recognise archives saved by the browser.
                                    browser_download_watcher.folders = browser_download_folders(browser_download_folder.value)
                                    for spec in browser_specs:
                                        if spec['file_id'] and spec['filename']:
                                            browser_download_watcher.register(spec['game'], spec['mod_id'], spec['file_id'],
                                                                              spec['filename'], spec['size_kb'])
                                    parts = []
                                    if n: parts.append(f'прямых загрузок: {n}')
                                    if browser_specs: parts.append(f'через браузер: {len(browser_specs)}')
                                    if reused: parts.append(f'уже скачано: {len(reused)}')
                                    if misses: parts.append(f'ошибок подготовки: {len(misses)}')
                                    workflow_summary.value = f"Файлы подготовлены — {', '.join(parts) or 'нечего скачивать'}."
                                    warn_about_disk(planned_kb * 1024, bool(browser_specs))
                                    if browser_specs:
                                        open_free(browser_specs)
                                    refresh_workflow()
                                    changed = True
                                    finish_if_done()
                            elif result[0] == 'mod_info':
                                _, infos, problem, wanted = result
                                mod_info.update(infos)
                                info_state['error'] = problem
                                if problem:
                                    info_state['asked'].difference_update(wanted)
                                for item in plan.items:
                                    info = mod_info.get(identity(item))
                                    if info and info.name:
                                        item['title'] = info.name[:160]
                                # Nexus told nothing about these: the older way, one by one with a key.
                                fill_titles([item for item in plan.items if identity(item) not in mod_info])
                                refresh_present()
                                refresh_workflow()
                            elif result[0] == 'present':
                                info_state['downloaded'], info_state['installed'] = result[1], result[2]
                                if requirements_mode.value == 'auto' and not workflow['running'] and len(plan.items) < SURE_LIMIT:
                                    missing = requirement_overview().required
                                    if missing:
                                        add_requirements(missing)
                                refresh_workflow()
                            elif result[0] == 'updates':
                                _, found, problem, quiet = result
                                updates_state['checking'] = False
                                if found is None:
                                    if not quiet:
                                        tell(f'Не удалось проверить новые версии: {problem}')
                                else:
                                    updates_state['found'] = found
                                    try:
                                        save_found(PATHS['updates'], found)
                                    except OSError:
                                        pass
                                    refresh_updates()
                                    if found:
                                        tell(f'Вышли новые версии: {len(found)}. Они на вкладке «Загрузки».')
                                    elif not quiet:
                                        tell('Новых версий нет: всё скачанное актуально.')
                                    redraw()
                            elif result[0] == 'vortex_checked':
                                info = result[1]
                                if info['running']:
                                    tell('Vortex сейчас открыт, поэтому подробности недоступны. Он на месте: файлы будут класться в его загрузки.')
                                else:
                                    if info['downloads'] and not vortex_folder.value:
                                        vortex_folder.value = info['downloads']
                                        persist_config(silent=True)
                                    tell(f"Vortex {info['version'] or ''} отвечает. Загрузки: {info['downloads'] or 'не удалось узнать'}.")
                            elif result[0] == 'file_catalog':
                                _, item, records, error = result
                                ident = identity(item)
                                file_loading.discard(ident)
                                if error:
                                    file_errors[ident] = 'Не удалось получить файлы: ' + str(error)[:160]
                                else:
                                    file_errors.pop(ident, None)
                                    file_catalogs[ident] = records
                                if ident in file_expanded:
                                    refresh_workflow()
                            elif result[0] == 'delivered':
                                trace.emit('ui.archive_delivery_result')
                                _, jobid, item, stage, detail, session = result
                                installing_jobs.discard(jobid)
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    session.set_status(item, stage, detail)
                                    finished_job = jobs.items.get(jobid)
                                    if finished_job is not None and finished_job.task:
                                        session.finish(finished_job.task, stage not in ('Ошибка установки', 'Отменено'))
                                    refresh_workflow()
                                changed = library_changed = True
                            elif result[0] == 'extension_timeout':
                                trace.emit('ui.extension_scan_timeout')
                                _, waiting_session, revision, progress = result
                                if workflow['current'] is waiting_session and not waiting_session.cancelled.is_set():
                                    responses, expected = progress
                                    if responses:
                                        # Only show freshly renewed profiles, with a
                                        # warning about other unresponsive profiles.
                                        refreshed = snapshots.collect_refreshed(revision)
                                        begin_workflow_items(refreshed, waiting_session)
                                        workflow_summary.value = (
                                            f'Ответили профили: {responses} из {expected}. '
                                            f'Найдено {len(refreshed)} модов. '
                                            'Остальные профили не ответили; список может быть неполным.')
                                    else:
                                        workflow['running'] = False
                                        workflow['current'] = workflow.get('previous')
                                        workflow_summary.value = (
                                            'Расширение ранее подключалось, но не ответило на новый поиск. '
                                            'Проверьте Nexus Tab Collector в браузере. '
                                            'Старые результаты не заменены неполным Windows-списком.')
                                    refresh_workflow()
                            elif result[0] == 'extension_scanned':
                                trace.emit('ui.extension_scanned')
                                _, waiting_session, old_snapshot = result
                                if workflow['current'] is waiting_session and not waiting_session.cancelled.is_set():
                                    combined = normalize_entries(snapshots.collect(max_age=120) + old_snapshot)
                                    begin_workflow_items(combined, waiting_session)
                                    names = ', '.join(f'{name}: {n} модов, {snapshots.profile_counts(max_age=120).get(name, 1)} проф.' for name, n in snapshots.summary(max_age=120).items())
                                    browser_status.value = 'Все вкладки подключённых профилей: ' + (names or 'нет активных профилей')
                                    workflow_summary.value = (f'Получено {len(combined)} модов из подключённых браузеров. '
                                                              'Не требуется переключать вкладки. Неподключённые профили не проверены.')
                                    refresh_workflow()
                            elif result[0] == 'windows_scanned':
                                trace.emit('ui.windows_scanned')
                                _, scan, waiting_session, snapshot_mods = result
                                try:
                                    save_scan_diagnostics(scan, STATE_DIR / 'logs' / 'browser-scan-last.json')
                                except OSError:
                                    pass
                                if workflow['current'] is waiting_session and not waiting_session.cancelled.is_set():
                                    combined = normalize_entries(snapshots.collect(max_age=120) + snapshot_mods + scan.mods)
                                    if combined:
                                        browser_status.value = ('Проверено окон: ' + str(scan.windows_scanned)
                                                                + ' · Браузеры: ' + ', '.join(scan.browsers)
                                                                + f' · Вкладок проверено: {scan.examined}'
                                                                + f' · Модов: {len(combined)}'
                                                                + f' · Пропущено: {scan.skipped}'
                                                                + f' · Другие страницы Nexus: {scan.pages.get("nexus-other", 0)}')
                                        begin_workflow_items(combined, waiting_session)
                                        if scan.skipped and not snapshots.sources(max_age=120):
                                            workflow_summary.value += (f' Внимание: {scan.skipped} фоновых вкладок не прочитаны. '
                                                                       'Для полного охвата подключите расширение браузера.')
                                            refresh_workflow()
                                    else:
                                        begin_workflow_items([], waiting_session)
                                        workflow_summary.value = ('Не удалось получить ссылки модов. ' + ' '.join(scan.warnings)[:360])
                                        refresh_workflow()
                            elif result[0] == 'scan_waited':
                                _, items, waiting_session = result
                                if workflow['current'] is waiting_session and not waiting_session.cancelled.is_set():
                                    begin_workflow_items(items, waiting_session)
                            elif result[0] == 'latest_error':
                                trace.emit('ui.file_catalog_failed')
                                _, problem, session = result
                                if session is workflow['current'] and not session.cancelled.is_set():
                                    workflow['running'] = False
                                    workflow_summary.value = 'Не удалось получить файлы Nexus: ' + str(problem)[:180]
                                    if any(code in str(problem).lower() for code in ('401', 'unauthorized', 'invalid api', 'api key')):
                                        api_notice_title.value = 'Проверьте Nexus API-ключ: сервер отклонил авторизацию'
                                        api_notice.visible = True
                                    refresh_workflow()
                            elif result[0] == 'account':
                                trace.emit('ui.nexus_account_verified')
                                api_notice.visible = False
                                persist_config(silent=True)
                                tell(f'Nexus Mods: аккаунт {result[1]} подключён.')
                if list_changed and not changed:
                    refresh_workflow()
                if changed:
                    # Progress events arrive many times a second: redraw once a second.
                    now = time.monotonic()
                    if library_changed or now - last_redraw >= 1.0:
                        last_redraw = now
                        if active['tab'] == 'downloads':
                            refresh_jobs()
                        refresh_workflow()
                    if library_changed:
                        refresh_library()
                    finish_if_done()
                    if list_changed:
                        refresh_workflow()
            except Exception as exc:
                if 'destroyed session' in str(exc):
                    return  # The window was closed: there is nothing left to draw.
                trace.emit_exception(exc, area='eventloop')
                # Keep the polling task alive after a recoverable exception.
                if active['tab'] == 'logs':
                    log_headline.value = 'Сбой обработки очереди записан в журнал.'
                    redraw()
            await asyncio.sleep(.3)
    try:
        unfinished = load_pack(PATHS['unfinished']) if PATHS['unfinished'].exists() else []
    except Exception:
        unfinished = []
    if unfinished:
        incoming.put(('restore_unfinished', unfinished))
    page.run_task(process_queue)
    if updates_auto.value and time_now() - updates_state['checked_at'] > UPDATE_CHECK_SECONDS:
        check_updates(quiet=True)

def run():
    # The portable build keeps the window of the program in its own folder, not in the user profile.
    window = application_root() / 'runtime' / 'flet'
    if (window / 'flet.exe').is_file():
        os.environ.setdefault('FLET_VIEW_PATH', str(window))
    ft.run(main)


if __name__ == "__main__":
    run()
