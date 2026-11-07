"""Pure Flet screen factories. Behaviour is injected by the application controller.
Screens do not own Nexus credentials, queues, or persistence.
"""
import flet as ft

from nexus_tab_installer.core.requirements import DOWNLOADED, ELSEWHERE, GAME_DLC, INSTALLED, LISTED, MISSING

PREMIUM_WARNING = ('Premium-аккаунт: эта часть программы на Premium не проверялась. '
                   'Скачивание может работать неправильно. Начните с одного-двух модов и проверьте результат.')


def mods_view(*, link_field, count, list_area, add_text):
    """Mod list with bounded, non-wrapping header layout and a single page scroll."""
    return ft.Column([
        ft.Row([
            ft.Text('Мои моды', size=25, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True), count,
        ]),
        ft.Text('Импорт из вкладок Chrome, Edge, Opera GX и Firefox или вставка ссылок.', color='#99A2B7'),
        ft.Container(
            padding=14, bgcolor='#1B2031', border_radius=14,
            content=ft.Column([
                link_field,
                ft.Row([
                    ft.Button('Добавить в библиотеку', icon=ft.Icons.ADD_LINK, on_click=add_text),
                ]),
            ]),
        ),
        ft.Container(
            bgcolor='#151A28',
            border=ft.Border.all(1, '#2B3043'),
            border_radius=18,
            padding=14,
            content=ft.Column([
                # IMPORTANT: do not use an expanding spacer inside a wrapping Row.
                # Flutter receives unbounded width constraints in that combination,
                # which can display a large grey rendering-error widget on Windows.
                ft.Text('Список модов', size=17, weight=ft.FontWeight.BOLD),
                ft.Text('Сохранённые моды. Загрузка из открытых вкладок запускается на Главной.',
                        size=12, color='#9FA8BF'),
                ft.Divider(height=1, color='#2B3043'),
                list_area,
            ], spacing=14),
        ),
    ], scroll=ft.ScrollMode.AUTO, expand=True, spacing=14)

MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
          'сентября', 'октября', 'ноября', 'декабря')
MUTED = '#9CA3B8'
ACCENT = '#B6A9FF'
GOOD = '#8DDEBB'
WARN = '#F1C181'
BAD = '#F0A09C'
SURFACE = '#1B2031'
JOB_STATES = {'queued': ('В очереди', ACCENT), 'downloading': ('Скачивается', ACCENT), 'finished': ('Готово', GOOD),
              'failed': ('Ошибка', BAD), 'cancelled': ('Отменено', MUTED), 'interrupted': ('Прервано', WARN)}
RETRYABLE_STATES = ('failed', 'cancelled', 'interrupted')
ACTIVE_STATES = ('queued', 'downloading')


def plural(count, one, few, many):
    tens, units = count % 100, count % 10
    word = many if 11 <= tens <= 14 else one if units == 1 else few if 2 <= units <= 4 else many
    return f'{count} {word}'


def size_text(size):
    if size < 1024 ** 2:
        return f'{max(1, round(size / 1024)) if size else 0} КБ'
    if size < 1024 ** 3:
        return f'{size / 1024 ** 2:.1f} МБ'.replace('.', ',')
    return f'{size / 1024 ** 3:.2f} ГБ'.replace('.', ',')


def session_title(when, now):
    clock = when.strftime('%H:%M')
    days = (now.date() - when.date()).days
    if days == 0:
        return f'Сегодня, {clock}'
    if days == 1:
        return f'Вчера, {clock}'
    return f'{when.day} {MONTHS[when.month - 1]} {when.year}, {clock}'


# ------------------------------------------------------------------ small building blocks
def chip(text, color=MUTED, *, on_click=None, tooltip=None, icon=None):
    """A short remark that does not take a line of its own, and opens more when it can."""
    parts = [ft.Icon(icon, size=14, color=color)] if icon else []
    parts.append(ft.Text(text, size=12, color=color, no_wrap=True))
    return ft.Container(content=ft.Row(parts, spacing=4, tight=True), border_radius=10, on_click=on_click, ink=bool(on_click),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=3), bgcolor='#242A40', tooltip=tooltip)


def notice(text_control, *actions, color='#2E2A1C', edge='#6B5A2C', icon=ft.Icons.WARNING_AMBER_ROUNDED, icon_color='#F3C98B', visible=True):
    """A warning or a hint. It offers things, and everything goes on if nobody answers it."""
    body = [ft.Row([ft.Icon(icon, color=icon_color, size=20), ft.Container(expand=True, content=text_control)],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)]
    if actions:
        body.append(ft.Row(list(actions), spacing=8, wrap=True))
    return ft.Container(visible=visible, bgcolor=color, border_radius=12, padding=12, border=ft.Border.all(1, edge),
                        content=ft.Column(body, spacing=8))


def section(title, controls, *, opened, refresh, icon=None, hint=''):
    """A part of a long screen that can be folded away; what is rarely needed starts folded."""
    arrow = ft.Icon(ft.Icons.EXPAND_MORE if opened else ft.Icons.CHEVRON_RIGHT, color=ACCENT, size=22)
    body = ft.Column(controls, spacing=12, visible=opened)

    def toggle(e=None):
        body.visible = not body.visible
        arrow.icon = ft.Icons.EXPAND_MORE if body.visible else ft.Icons.CHEVRON_RIGHT
        refresh()

    heading = [ft.Text(title, size=17, weight=ft.FontWeight.BOLD)]
    if hint:
        heading.append(ft.Text(hint, size=12, color=MUTED))
    header = ft.Container(on_click=toggle, ink=True, border_radius=10, padding=ft.Padding.symmetric(horizontal=4, vertical=4),
                          content=ft.Row([arrow, *([ft.Icon(icon, color=ACCENT, size=20)] if icon else []),
                                          ft.Column(heading, spacing=0, expand=True)],
                                         spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
    return ft.Container(bgcolor=SURFACE, border_radius=14, padding=12, content=ft.Column([header, body], spacing=10))


# ------------------------------------------------------------------ requirements of a mod
_NEED_STATES = {MISSING: ('Нет у вас', WARN, ft.Icons.ERROR_OUTLINE), LISTED: ('В этом списке', GOOD, ft.Icons.CHECK_CIRCLE_OUTLINE),
                DOWNLOADED: ('Уже скачан', GOOD, ft.Icons.CHECK_CIRCLE_OUTLINE), INSTALLED: ('Есть в {}', GOOD, ft.Icons.CHECK_CIRCLE_OUTLINE),
                ELSEWHERE: ('Вне Nexus: проверьте сами', MUTED, ft.Icons.OPEN_IN_NEW), GAME_DLC: ('Дополнение игры', MUTED, ft.Icons.INFO_OUTLINE)}


def requirement_chip(needs, on_click):
    """How things stand in one glance. A mod without requirements shows nothing at all."""
    if not needs:
        return None
    missing = [need for need in needs if need.state == MISSING]
    required = [need for need in missing if not need.optional]
    outside = sum(need.state == ELSEWHERE for need in needs)
    if required:
        return chip(f'Не хватает: {len(required)}', WARN, on_click=on_click, icon=ft.Icons.WARNING_AMBER_ROUNDED, tooltip='Показать требования мода')
    if missing:
        return chip(f'По желанию: {len(missing)}', MUTED, on_click=on_click, tooltip='Показать требования мода')
    if outside:
        return chip(f'Вне Nexus: {outside}', MUTED, on_click=on_click, tooltip='Показать требования мода')
    return chip('Требования есть', GOOD, on_click=on_click, icon=ft.Icons.CHECK, tooltip='Показать требования мода')


def requirement_panel(needs, *, on_add, on_page):
    rows = []
    for need in needs:
        label, color, icon = _NEED_STATES[need.state]
        lines = [ft.Text(need.name, size=13, color='#F0EEFF', max_lines=2)]
        if need.note:
            lines.append(ft.Text(need.note, size=11, color=MUTED, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS, tooltip=need.note))
        actions = [ft.Text(label.format(need.where) if need.state == INSTALLED else label, size=12, color=color)]
        if need.state == MISSING and need.mod_id and need.game:
            actions.append(ft.TextButton('Добавить', icon=ft.Icons.ADD, on_click=lambda e, chosen=need: on_add(chosen)))
        if need.page:
            actions.append(ft.IconButton(ft.Icons.OPEN_IN_NEW, icon_size=16, icon_color=ACCENT, tooltip='Открыть страницу',
                                         visual_density=ft.VisualDensity.COMPACT,
                                         on_click=lambda e, page=need.page: on_page(page)))
        rows.append(ft.Container(bgcolor='#192032', border_radius=9, padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                 content=ft.Row([ft.Icon(icon, size=16, color=color),
                                                 ft.Column(lines, spacing=1, expand=True), *actions],
                                                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)))
    rows.append(ft.Text('Список пишет автор мода, а что из него нужно именно вам, решаете вы. Скачать можно и без этого.', size=11, color=MUTED))
    return ft.Column(rows, spacing=4)


# ------------------------------------------------------------------ downloads
def _confirmation(text, on_confirm, on_cancel):
    return ft.Container(bgcolor='#3A2730', border_radius=9, padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                        content=ft.Row([ft.Text(text, size=12, color='#FFE1D9', expand=True),
                                        ft.TextButton('В Корзину', icon=ft.Icons.DELETE_OUTLINE, on_click=lambda e: on_confirm()),
                                        ft.TextButton('Оставить', on_click=lambda e: on_cancel())],
                                       spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))


def _archive_row(archive, on_reveal, *, pending=None, on_delete=None, on_confirm=None, on_cancel=None):
    name = archive.path.name
    lines = [ft.Text(archive.title or name, size=13, color='#E4E1F5', max_lines=1,
                     overflow=ft.TextOverflow.ELLIPSIS, tooltip=name)]
    if archive.title:
        lines.append(ft.Text(name, size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
    actions = [ft.IconButton(ft.Icons.FIND_IN_PAGE_OUTLINED, icon_size=18, icon_color=ACCENT, tooltip='Показать файл в папке',
                             visual_density=ft.VisualDensity.COMPACT,
                             on_click=lambda e, path=archive.path: on_reveal(path))]
    if on_delete:
        actions.append(ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=MUTED, tooltip='Удалить файл',
                                     visual_density=ft.VisualDensity.COMPACT,
                                     on_click=lambda e, path=archive.path: on_delete(path)))
    row = ft.Row([ft.Column(lines, spacing=0, expand=True), ft.Text(size_text(archive.size), size=12, color=MUTED), *actions],
                 spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    content = ft.Column([row, _confirmation('Удалить этот файл? Копии в MO2 и Vortex останутся.', on_confirm, on_cancel)], spacing=2) \
        if pending == archive.path else row
    return ft.Container(padding=ft.Padding.only(left=44, right=12, top=2, bottom=2), content=content)


def session_card(session, *, opened, now, on_toggle, on_open_folder, on_reveal, pending=None, on_delete=None, on_confirm=None, on_cancel=None):
    """One download run: the header says when and how much, the files fold away."""
    summary = f"{plural(len(session.archives), 'файл', 'файла', 'файлов')} · {size_text(session.size)}"
    actions = [ft.IconButton(ft.Icons.FOLDER_OPEN, icon_size=20, icon_color=ACCENT, tooltip='Открыть папку сессии',
                             on_click=lambda e, folder=session.folder: on_open_folder(folder))]
    if on_delete:
        actions.append(ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=20, icon_color=MUTED, tooltip='Удалить сессию',
                                     on_click=lambda e, folder=session.folder: on_delete(folder)))
    header = ft.Container(
        on_click=lambda e, name=session.folder.name: on_toggle(name), ink=True, border_radius=12,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        content=ft.Row([
            ft.Icon(ft.Icons.EXPAND_MORE if opened else ft.Icons.CHEVRON_RIGHT, color=ACCENT, size=22),
            ft.Column([ft.Text(session_title(session.when, now), size=14, weight=ft.FontWeight.W_600),
                       ft.Text(summary, size=12, color=MUTED)], spacing=0, expand=True),
            *actions,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
    rows = [_confirmation(f'Удалить всю сессию: {summary}? Копии в MO2 и Vortex останутся.', on_confirm, on_cancel)] if pending == session.folder else []
    if opened:
        rows += [_archive_row(archive, on_reveal, pending=pending, on_delete=on_delete, on_confirm=on_confirm, on_cancel=on_cancel)
                 for archive in session.archives]
    return ft.Container(bgcolor=SURFACE, border_radius=12,
                        padding=ft.Padding.only(bottom=6 if rows else 0),
                        content=ft.Column([header, *rows], spacing=0))


def updates_card(updates, *, on_fetch, on_fetch_all, on_page):
    """Newer versions of what was downloaded. Only an offer, nothing starts by itself."""
    rows = []
    for update in updates:
        details = ' · '.join(part for part in (update.version and f'версия {update.version}', update.size and size_text(update.size)) if part)
        rows.append(ft.Row([
            ft.Column([ft.Text(update.title or f'Мод #{update.mod_id}', size=13, color='#F0EEFF', max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                       ft.Text(details, size=11, color=MUTED)], spacing=0, expand=True),
            ft.IconButton(ft.Icons.OPEN_IN_NEW, icon_size=16, icon_color=ACCENT, tooltip='Открыть страницу мода',
                          visual_density=ft.VisualDensity.COMPACT,
                          on_click=lambda e, chosen=update: on_page(chosen)),
            ft.TextButton('Скачать', icon=ft.Icons.DOWNLOAD, on_click=lambda e, chosen=update: on_fetch(chosen)),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
    return ft.Container(bgcolor='#1F2740', border_radius=12, padding=12, border=ft.Border.all(1, '#3A4670'), content=ft.Column([
        ft.Row([ft.Icon(ft.Icons.NEW_RELEASES_OUTLINED, color=ACCENT, size=20),
                ft.Text(f'Вышли новые версии: {len(updates)}', size=14, weight=ft.FontWeight.W_600, expand=True),
                *([ft.TextButton('Скачать все', icon=ft.Icons.DOWNLOAD, on_click=lambda e: on_fetch_all())] if len(updates) > 1 else [])],
               spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        *rows,
    ], spacing=4))


def job_row(job, *, on_retry, on_cancel):
    """One file in the queue or the history. It offers only what can be done with it right now."""
    label, color = JOB_STATES.get(job.state, (job.state, MUTED))
    actions = []
    if job.state in ACTIVE_STATES:
        actions.append(ft.TextButton('Отменить', icon=ft.Icons.CLOSE, on_click=lambda e, jid=job.id: on_cancel(jid)))
    elif job.state in RETRYABLE_STATES:
        actions.append(ft.TextButton('Повторить', icon=ft.Icons.REFRESH, on_click=lambda e, jid=job.id: on_retry(jid)))
    if job.total and job.state in ACTIVE_STATES:
        amount = f'{size_text(job.progress)} из {size_text(job.total)}'
    else:
        amount = size_text(job.total or job.progress) if (job.total or job.progress) else ''
    lines = [ft.Row([
        ft.Text(job.filename or f'Файл #{job.file_id}', size=13, color='#E4E1F5', max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS, expand=True, tooltip=job.filename or None),
        ft.Text(amount, size=12, color=MUTED),
        ft.Text(label, size=12, color=color),
        *actions,
    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
    if job.state in ACTIVE_STATES:
        lines.append(ft.ProgressBar(value=job.progress / job.total if job.total else None,
                                    color='#8B70FF', bgcolor='#323752'))
    if job.error:
        lines.append(ft.Text(job.error[:200], size=11, color=BAD, max_lines=2,
                             overflow=ft.TextOverflow.ELLIPSIS))
    return ft.Container(bgcolor=SURFACE, border_radius=10, padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                        content=ft.Column(lines, spacing=4))


def downloads_view(*, nxm_field, import_nxm, next_free, free_count, archive_field, check_archive, unpack_archive, archive_result, summary, now_area, updates_area, library, history_button, history_area, refresh_library, open_library, check_updates, refresh):
    # What most people came for goes first; the rarely needed tools stay folded.
    advanced = ft.Column(visible=False, spacing=14, controls=[
        ft.Container(padding=14, bgcolor=SURFACE, border_radius=14,
                     content=ft.Column([ft.Text('Ссылка NXM', weight=ft.FontWeight.BOLD), nxm_field,
                         ft.Row([ft.Button('Добавить NXM в очередь', icon=ft.Icons.ADD, on_click=import_nxm)])])),
        ft.Container(padding=14, bgcolor=SURFACE, border_radius=14,
            content=ft.Column([ft.Text('Ручной режим · ожидающие файлы', weight=ft.FontWeight.BOLD),
                ft.Text('Нужен, только если скачивание через браузер выключено: подтвердите файл на сайте, программа заберёт архив из папки загрузок или ссылку NXM.', color='#AAB2C7', size=12),
                free_count,
                ft.Button('Открыть следующий файл на Nexus', icon=ft.Icons.OPEN_IN_BROWSER, on_click=next_free)])),
        ft.Container(padding=14, bgcolor=SURFACE, border_radius=14,
            content=ft.Column([
                ft.Text('Проверка и подготовка ZIP / 7z / RAR', weight=ft.FontWeight.BOLD),
                ft.Text('Архив распаковывается только во временную staging-папку; установка в игру не выполняется.', color='#AAB2C7', size=12),
                archive_field,
                ft.Row([ft.Button('Проверить архив', on_click=check_archive),
                        ft.Button('Подготовить архив', on_click=unpack_archive)]),
                archive_result])),
    ])

    def toggle_advanced(e=None):
        advanced.visible = not advanced.visible
        refresh()

    return ft.Column([
        ft.Row([
            ft.Column([ft.Text('Загрузки', size=24, weight=ft.FontWeight.BOLD), summary], spacing=0, expand=True),
            ft.Button('Проверить обновления', icon=ft.Icons.NEW_RELEASES_OUTLINED, on_click=check_updates),
            ft.Button('Открыть все папки', icon=ft.Icons.FOLDER_OPEN, on_click=open_library),
            ft.IconButton(ft.Icons.REFRESH, icon_color=ACCENT, tooltip='Обновить список', on_click=refresh_library),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        now_area,
        updates_area,
        library,
        ft.Row([history_button,
                ft.TextButton('Дополнительно: ссылка NXM, ручной режим, проверка архива',
                              icon=ft.Icons.TUNE, on_click=toggle_advanced)], wrap=True),
        history_area,
        advanced,
    ], expand=True, spacing=10, scroll=ft.ScrollMode.AUTO)


def settings_view(*, general, key, remember_key, auto_free_opt, pace_choice, pace_custom, pace_hint, pace_warning, browser_download_folder, check_key, open_api_help, open_browser_setup, enable_nxm, destination_panel, destination_mode, mo2_ini_note, folder, destination_hint, find_managers, detected_managers, validate_destination, store_config, pack_path, pack_save, pack_load, manual_install, refresh):
    """Everything in one place and one scroll. What most people never need starts folded."""
    return ft.Column([
        ft.Text('Настройки', size=24, weight=ft.FontWeight.BOLD),
        ft.Text('Сохраняются сами, в папке data/config программы.', color=MUTED, size=13),
        section('Общее', general, opened=True, refresh=refresh, icon=ft.Icons.TUNE_ROUNDED,
                hint='Язык, уведомления и что программа делает сама'),
        section('Nexus Mods', [
            ft.Text('Для бесплатного аккаунта ключ не обязателен. 1. Войдите в Nexus Mods. 2. Откройте API Keys. 3. Создайте Personal API Key и вставьте его ниже.', size=12, color='#B7C2D9'),
            ft.TextButton('Открыть Nexus Mods → API Keys', on_click=open_api_help),
            ft.Row([key, ft.Button('Проверить аккаунт', on_click=check_key)]),
            remember_key,
            ft.Text('Внимание: для переносимости сохранённый API-ключ хранится без шифрования. При передаче папки программы другим людям снимите галочку и сохраните настройки.', size=12, color='#F2B86B'),
            ft.Text(PREMIUM_WARNING, size=12, color='#F2B86B'),
        ], opened=True, refresh=refresh, icon=ft.Icons.KEY_ROUNDED, hint='Аккаунт и API-ключ'),
        section('Скачивание через браузер', [
            auto_free_opt, pace_choice, pace_custom, pace_hint, pace_warning,
            ft.Text('При скачивании через браузер расширение само нажимает кнопки сайта. Правила Nexus запрещают ботов и скачивание сверх обычного использования, за это могут заблокировать аккаунт. Медленный темп снижает нагрузку на сайт, но не отменяет этот риск. Способ с официальным API: Premium и API-ключ.', size=12, color='#F2B86B'),
            ft.Text('Если Nexus попросит войти или пройти проверку (CAPTCHA), программа предупредит и подождёт: '
                    'это делает человек в окне браузера.', size=12, color='#AAB2C7'),
            ft.Text('Если Nexus скачивает файл обычной загрузкой браузера, программа автоматически заберёт его из Downloads. При нестандартной папке укажите её здесь.', size=12, color='#AAB2C7'),
            browser_download_folder,
            ft.Button('Подключить браузер (инструкция)', icon=ft.Icons.EXTENSION, on_click=open_browser_setup),
        ], opened=True, refresh=refresh, icon=ft.Icons.PUBLIC, hint='Темп, паузы и расширение'),
        section('Куда устанавливать', [destination_panel], opened=False, refresh=refresh, icon=ft.Icons.FOLDER_COPY_OUTLINED,
                hint='Mod Organizer 2, Vortex и папка игры'),
        section('Дополнительно', [
            ft.Text('Обнаружение менеджеров', weight=ft.FontWeight.BOLD),
            ft.Button('Обнаружить MO2 / Vortex', icon=ft.Icons.SEARCH, on_click=find_managers),
            detected_managers,
            ft.Divider(color='#30364B'),
            ft.Text('Список модов в файле', weight=ft.FontWeight.BOLD),
            pack_path, ft.Row([ft.Button('Сохранить сборку JSON', on_click=pack_save),
                               ft.Button('Загрузить сборку JSON', on_click=pack_load)], wrap=True),
            ft.Divider(color='#30364B'),
            ft.Text('Ссылки Mod Manager Download', weight=ft.FontWeight.BOLD),
            ft.Text('Необязательно: обработчик nxm:// нужен только для ручной кнопки Mod Manager Download на сайте. Такие ссылки будут приходить в программу, а не в Vortex или MO2. Это единственная запись программы в Windows: после переноса папки нажмите кнопку ещё раз.', size=12, color='#B5BBD1'),
            ft.Button('Подключить Mod Manager Download к Windows', icon=ft.Icons.LINK, on_click=enable_nxm),
            ft.Divider(color='#30364B'),
            ft.Text('Копия архивов в другую папку', weight=ft.FontWeight.BOLD),
            destination_mode, mo2_ini_note, folder, destination_hint,
            ft.Row([ft.Button('Проверить путь', on_click=validate_destination),
                    ft.Button('Сохранить настройки', on_click=store_config)], wrap=True),
        ], opened=False, refresh=refresh, icon=ft.Icons.SETTINGS_SUGGEST_OUTLINED, hint='Редко нужные настройки'),
        section('Ручная установка', manual_install, opened=False, refresh=refresh, icon=ft.Icons.BUILD_OUTLINED,
                hint='Установка из распакованной папки и откат своих установок'),
    ], expand=True, scroll=ft.ScrollMode.AUTO, spacing=12)
