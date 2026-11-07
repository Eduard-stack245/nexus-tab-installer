"""Archives of one download lie together in a folder named by its date and time."""
import asyncio
import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

import flet as ft
import pytest

from nexus_tab_installer.core.session_library import (ArchiveIndex, MANIFEST, ModCollection, file_hash, list_sessions,
                                                      place_archive, record_archive, remove_empty_sessions)
from nexus_tab_installer.core.session_migration import migrate_old_layout
from nexus_tab_installer.downloads.transfer import DownloadCancelled, DownloadError
from nexus_tab_installer.ui.views import plural, session_title, size_text
from test_ui_end_to_end_v400 import CDN, GAME, MOD_A, MOD_B, account, launch, nexus_file  # noqa: F401 (fixture)

GOOD_NAME = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}( \(\d+\))?$')
MODS = [{'game': 'skyrim', 'mod_id': 7, 'title': 'Лук Ветра', 'url': 'https://www.nexusmods.com/skyrim/mods/7'}]
SETUP_DONE = {'browser_paired': True}


# ------------------------------------------------------------------ a session on disk
def test_session_folder_is_named_by_date_and_time(tmp_path):
    moment = lambda: datetime(2026, 11, 6, 19, 7, 12)
    assert ModCollection.create(tmp_path, MODS, clock=moment).folder.name == '2026-11-06 19-07-12'
    assert ModCollection.create(tmp_path, MODS, clock=moment).folder.name == '2026-11-06 19-07-12 (2)'


def test_archive_is_described_by_a_note_the_person_does_not_see(tmp_path):
    session = ModCollection.create(tmp_path, MODS)
    path = session.folder / 'bow.7z'
    path.write_bytes(b'7z')
    record_archive(path, 'skyrim', 7, 99)
    record_archive(path, 'skyrim', 7, 99)  # the note can be rewritten while it is hidden
    assert [p.name for p in session.folder.iterdir() if not p.name.startswith('coll')] == ['bow.7z']
    if os.name == 'nt':
        import ctypes
        assert ctypes.windll.kernel32.GetFileAttributesW(str(session.folder / MANIFEST)) & 2
    found = list_sessions(tmp_path)[0].archives[0]
    assert (found.title, found.game, found.mod_id, found.file_id, found.size) == ('Лук Ветра', 'skyrim', 7, 99, 2)


def test_two_different_files_with_one_name_both_stay(tmp_path):
    first, second = tmp_path / 'a' / 'same.zip', tmp_path / 'b' / 'same.zip'
    first.parent.mkdir(), second.parent.mkdir()
    first.write_bytes(b'first'), second.write_bytes(b'second')
    folder = tmp_path / 'session'
    one, two = place_archive(first, folder), place_archive(second, folder)
    assert (one.name, two.name) == ('same.zip', 'same (2).zip')
    assert (one.read_bytes(), two.read_bytes()) == (b'first', b'second')


def test_one_file_placed_twice_stays_one_file(tmp_path):
    folder = tmp_path / 'session'
    for name in ('a', 'b'):
        source = tmp_path / name / 'same.zip'
        source.parent.mkdir()
        source.write_bytes(b'same')
        place_archive(source, folder)
    assert [p.name for p in folder.iterdir()] == ['same.zip']


def test_file_already_in_place_is_left_alone(tmp_path):
    path = tmp_path / 'same.zip'
    path.write_bytes(b'same')
    assert place_archive(path, tmp_path) == path and path.read_bytes() == b'same'


def test_only_folders_without_archives_are_removed(tmp_path):
    empty, full = ModCollection.create(tmp_path, MODS), ModCollection.create(tmp_path, MODS)
    (full.folder / 'bow.7z').write_bytes(b'7z')
    (tmp_path / 'partial').mkdir()
    (tmp_path / 'partial' / '.bow.part').write_bytes(b'unfinished')
    remove_empty_sessions(tmp_path)
    assert not empty.folder.exists()
    assert (full.folder / 'bow.7z').is_file() and (tmp_path / 'partial' / '.bow.part').is_file()


def test_list_is_newest_first_and_shows_what_is_really_there(tmp_path):
    old = ModCollection.create(tmp_path, MODS, clock=lambda: datetime(2026, 1, 1, 8, 0, 0))
    new = ModCollection.create(tmp_path, MODS, clock=lambda: datetime(2026, 2, 1, 8, 0, 0))
    for folder in (old.folder, new.folder):
        (folder / 'bow.7z').write_bytes(b'7z')
    deleted = record_archive(old.folder / 'bow.7z', 'skyrim', 7, 1)
    foreign = tmp_path / 'моя папка'
    foreign.mkdir()
    (foreign / 'own.zip').write_bytes(b'zip')
    assert list_sessions(tmp_path)[1].folder == new.folder
    deleted.unlink()
    names = [session.folder.name for session in list_sessions(tmp_path)]
    assert old.folder.name not in names and 'моя папка' in names


def test_reading_the_list_never_breaks_a_write(tmp_path):
    """Windows refuses to replace a file that is being read: a good archive was marked as failed."""
    session = ModCollection.create(tmp_path, MODS)
    paths = []
    for number in range(30):
        path = session.folder / f'{number}.7z'
        path.write_bytes(b'7z')
        paths.append(path)
    stop, errors = threading.Event(), []

    def keep_reading():
        while not stop.is_set():
            list_sessions(tmp_path)

    readers = [threading.Thread(target=keep_reading) for _ in range(3)]
    for reader in readers:
        reader.start()
    try:
        for number, path in enumerate(paths):
            try:
                record_archive(path, 'skyrim', 7, number + 1)
            except OSError as exc:
                errors.append(exc)
    finally:
        stop.set()
        for reader in readers:
            reader.join()
    assert not errors
    assert len([a for a in list_sessions(tmp_path)[0].archives if a.file_id]) == 30


def test_mod_name_is_cleaned_and_a_site_name_is_not_taken_for_one(tmp_path):
    mods = [{'game': 'skyrim', 'mod_id': 1, 'title': 'Example Follower at Skyrim Special Edition Nexus - Mods and Community'},
            {'game': 'skyrim', 'mod_id': 2, 'title': 'Skyrim Special Edition Nexus - Mods and Community'},
            {'game': 'skyrim', 'mod_id': 3, 'title': 'skyrim / #3'}]
    session = ModCollection.create(tmp_path, mods)
    for number in (1, 2, 3):
        path = session.folder / f'{number}.7z'
        path.write_bytes(b'7z')
        record_archive(path, 'skyrim', number, number)
    assert [a.title for a in list_sessions(tmp_path)[0].archives] == ['Example Follower', '', '']


def test_broken_note_does_not_hide_the_archives(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / MANIFEST).write_text('{', encoding='utf-8')
    (tmp_path / 'a' / 'bow.7z').write_bytes(b'7z')
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b' / MANIFEST).write_text('{"files": 5, "mods": [1]}', encoding='utf-8')
    (tmp_path / 'b' / 'bow.7z').write_bytes(b'7z')
    assert sorted(s.folder.name for s in list_sessions(tmp_path)) == ['a', 'b']


def test_note_cannot_point_outside_its_folder(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('x', encoding='utf-8')
    folder = tmp_path / 'sessions' / 'a'
    folder.mkdir(parents=True)
    (folder / MANIFEST).write_text(json.dumps({'files': [{'name': '../../secret.txt', 'game': 'skyrim', 'mod_id': 7, 'file_id': 1}]}),
                                   encoding='utf-8')
    assert list_sessions(tmp_path / 'sessions') == []
    assert ArchiveIndex(tmp_path / 'sessions').find('skyrim', 7, 1) is None


def test_index_finds_exact_file_and_a_recent_one(tmp_path):
    session = ModCollection.create(tmp_path, MODS)
    path = session.folder / 'bow.7z'
    path.write_bytes(b'7z')
    record_archive(path, 'skyrim', 7, 99)
    index = ArchiveIndex(tmp_path)
    assert index.find('skyrim', 7, 99).path == path
    assert index.find('skyrim', 7, 98) is None and index.find('skyrim', 7, 0) is None
    assert index.recent('skyrim', 7, 60).path == path
    assert index.recent('skyrim', 7, 60, now=path.stat().st_mtime + 61) is None


@pytest.mark.parametrize('size, text', [(0, '0 КБ'), (100, '1 КБ'), (7261, '7 КБ'), (2879088, '2,7 МБ'), (3 * 1024**3, '3,00 ГБ')])
def test_sizes_are_readable(size, text):
    assert size_text(size) == text


@pytest.mark.parametrize('count, text', [(1, '1 архив'), (2, '2 архива'), (5, '5 архивов'), (11, '11 архивов'), (21, '21 архив')])
def test_words_agree_with_numbers(count, text):
    assert plural(count, 'архив', 'архива', 'архивов') == text


def test_session_title_is_a_plain_date():
    now = datetime(2026, 11, 6, 22, 0)
    assert session_title(datetime(2026, 11, 6, 19, 7), now) == 'Сегодня, 19:07'
    assert session_title(datetime(2026, 11, 5, 19, 7), now) == 'Вчера, 19:07'
    assert session_title(datetime(2026, 1, 5, 9, 2), now) == '5 января 2026, 09:02'


# ------------------------------------------------------------------ moving archives of older versions
def old_session(root, name, files, mods=()):
    folder = root / name
    (folder / 'archives').mkdir(parents=True)
    (folder / MANIFEST).write_text(json.dumps({'id': name, 'mods': list(mods), 'files': []}), encoding='utf-8')
    for (game, mod, file_id, filename), content in files.items():
        path = folder / 'archives' / game / str(mod) / str(file_id) / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(content)


def old_library_file(root, game, mod, file_id, filename, content, when=None):
    path = root / game / str(mod) / str(file_id) / filename
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    if when:
        os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def contents(*roots):
    return {file_hash(p) for root in roots for p in root.rglob('*')
            if p.is_file() and p.name != MANIFEST and not p.name.endswith('.part')}


@pytest.fixture
def old(tmp_path):
    return tmp_path / 'sessions', tmp_path / 'library' / 'downloads', tmp_path / 'history' / 'old-sessions'


def test_older_archives_end_up_in_flat_dated_folders(old):
    sessions, library, retired = old
    bow = (b'bow', ('skyrim', 7, 99, 'bow.7z'))
    old_session(sessions, '2026-11-05_03-42-47_4e9bf8', {bow[1]: bow[0]},
                [{'game': 'skyrim', 'mod_id': 7, 'title': 'Лук Ветра', 'url': 'u'}])
    old_library_file(library, *bow[1], bow[0])
    before = contents(sessions, library)

    assert migrate_old_layout(sessions, library, retired) == 1
    assert contents(sessions) == before
    assert [p.name for p in sessions.iterdir()] == ['2026-11-05 03-42-47']
    archive = list_sessions(sessions)[0].archives[0]
    assert (archive.title, archive.game, archive.mod_id, archive.file_id) == ('Лук Ветра', 'skyrim', 7, 99)
    assert archive.path.parent.name == '2026-11-05 03-42-47', 'Flat: directly in the session folder'
    assert not library.exists() and not (sessions / '2026-11-05 03-42-47' / 'archives').exists()


def test_file_used_by_several_runs_stays_in_the_first(old):
    sessions, library, retired = old
    shared, later = ('skyrim', 7, 1, 'shared.7z'), ('skyrim', 8, 2, 'later.7z')
    old_session(sessions, '2026-11-05_10-00-00_aaaaaa', {shared: b'shared'})
    old_session(sessions, '2026-11-06_10-00-00_bbbbbb', {shared: b'shared', later: b'later'})
    old_session(sessions, '2026-11-07_10-00-00_cccccc', {shared: b'shared'})
    old_library_file(library, *shared, b'shared')
    old_library_file(library, *later, b'later')
    before = contents(sessions, library)

    assert migrate_old_layout(sessions, library, retired) == 2
    assert contents(sessions) == before
    found = {s.folder.name: [a.path.name for a in s.archives] for s in list_sessions(sessions)}
    assert found == {'2026-11-05 10-00-00': ['shared.7z'], '2026-11-06 10-00-00': ['later.7z']}
    assert (retired / '2026-11-07_10-00-00_cccccc.json').is_file(), 'Its list of links is kept'


def test_files_known_only_to_the_old_library_are_grouped_by_time(old):
    sessions, library, retired = old
    start = datetime(2026, 11, 1, 20, 0, 0)
    old_library_file(library, 'skyrim', 1, 1, 'a.7z', b'a', start)
    old_library_file(library, 'skyrim', 2, 2, 'b.7z', b'b', start + timedelta(minutes=10))
    old_library_file(library, 'skyrim', 3, 3, 'c.7z', b'c', start + timedelta(hours=5))
    old_library_file(library, 'skyrim', 4, 'unsorted', 'd.7z', b'd', start + timedelta(hours=5))
    before = contents(library)

    assert migrate_old_layout(sessions, library, retired) == 4
    assert contents(sessions) == before
    found = {s.folder.name: [a.path.name for a in s.archives] for s in list_sessions(sessions)}
    assert found == {'2026-11-01 20-00-00': ['a.7z', 'b.7z'], '2026-11-02 01-00-00': ['c.7z', 'd.7z']}
    assert ArchiveIndex(sessions).find('skyrim', 2, 2).path.name == 'b.7z'


def test_different_files_with_one_name_both_survive_the_move(old):
    sessions, library, retired = old
    old_session(sessions, '2026-11-05_10-00-00_aaaaaa', {('skyrim', 7, 1, 'main.zip'): b'one', ('skyrim', 8, 2, 'main.zip'): b'two'})
    before = contents(sessions)
    assert migrate_old_layout(sessions, library, retired) == 2
    assert contents(sessions) == before
    assert [a.path.name for a in list_sessions(sessions)[0].archives] == ['main (2).zip', 'main.zip']


def test_same_size_but_different_content_is_never_treated_as_a_copy(old):
    sessions, library, retired = old
    old_session(sessions, '2026-11-05_10-00-00_aaaaaa', {('skyrim', 7, 1, 'one.zip'): b'aaaa'})
    old_library_file(library, 'skyrim', 7, 1, 'one.zip', b'bbbb')
    before = contents(sessions, library)
    assert migrate_old_layout(sessions, library, retired) == 2
    assert contents(sessions) == before and len(before) == 2


def test_move_can_run_again_and_leaves_unfinished_files_alone(old):
    sessions, library, retired = old
    old_session(sessions, '2026-11-05_10-00-00_aaaaaa', {('skyrim', 7, 1, 'a.7z'): b'a'})
    partial = old_library_file(library, 'skyrim', 9, 1, '.b.7z.skyrim-9-1.part', b'unfinished')
    assert migrate_old_layout(sessions, library, retired) == 1
    first = sorted(p.relative_to(sessions) for p in sessions.rglob('*'))
    assert partial.is_file()
    assert migrate_old_layout(sessions, library, retired) == 0
    assert sorted(p.relative_to(sessions) for p in sessions.rglob('*')) == first


def test_nothing_to_move_on_a_new_installation(old):
    sessions, library, retired = old
    assert migrate_old_layout(sessions, library, retired) == 0
    assert not sessions.exists() and not retired.exists()


def test_a_folder_that_is_not_ours_is_left_alone(old):
    sessions, library, retired = old
    (sessions / 'моя папка').mkdir(parents=True)
    (sessions / 'моя папка' / 'own.zip').write_bytes(b'own')
    old_session(sessions, '2026-11-05_10-00-00_aaaaaa', {('skyrim', 7, 1, 'a.7z'): b'a'})
    migrate_old_layout(sessions, library, retired)
    assert (sessions / 'моя папка' / 'own.zip').read_bytes() == b'own'


def test_program_moves_older_archives_when_it_starts(launch, tmp_path):
    old_session(tmp_path / 'data' / 'sessions', '2026-11-05_10-00-00_aaaaaa', {(GAME, 196600, 5, 'old.7z'): b'7z'})
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        h.click('Скачать выбранные')
        await h.until(h.finished)
        assert (await h.get('/auto/next'))['job'] is None, 'The moved archive is still known as downloaded'

    h.run(scenario)
    assert h.archive_named('old.7z').parent.name == '2026-11-05 10-00-00'


# ------------------------------------------------------------------ one click, one folder
def session_folders(h):
    return sorted(p for p in h.sessions.iterdir() if p.is_dir()) if h.sessions.is_dir() else []


def test_one_download_is_one_folder_with_all_its_archives(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        first = await h.next_job()
        await h.report(first, status='url', url=CDN.replace('Mod-196600-1-0', 'first'), file_id=1)
        second = await h.next_job()
        await h.report(second, status='url', url=CDN.replace('Mod-196600-1-0', 'second'), file_id=2)
        await h.until(h.finished)

    h.run(scenario)
    folders = session_folders(h)
    assert len(folders) == 1 and GOOD_NAME.match(folders[0].name), folders
    assert sorted(p.name for p in folders[0].iterdir() if p.name != MANIFEST) == ['first.7z', 'second.7z']
    assert h.archive_of(GAME, 196600, 1).path.name == 'first.7z'
    assert h.archive_of('skyrim', 7, 2).path.name == 'second.7z'


def test_next_download_is_a_new_folder(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        for mod, file_id in ((MOD_A, 1), (MOD_B, 2)):
            h.paste_links(f'{mod}?tab=files&file_id={file_id}')
            h.click('Скачать выбранные')
            job = await h.next_job()
            await h.report(job, status='url', url=CDN.replace('Mod-196600-1-0', f'file{file_id}'), file_id=file_id)
            await h.until(lambda: h.archive_of(job['game'], job['mod_id'], file_id))
            await h.until(h.finished)

    h.run(scenario)
    assert len(session_folders(h)) == 2


def test_search_alone_creates_no_folder(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(MOD_A)
        await asyncio.sleep(0.2)

    h.run(scenario)
    assert session_folders(h) == []


def test_archive_of_an_earlier_download_is_not_copied(launch):
    h = launch(config=SETUP_DONE)
    h.seed_archive(GAME, 196600, 5, 'old.7z')
    before = session_folders(h)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        h.click('Скачать выбранные')
        await h.until(h.finished)

    h.run(scenario)
    assert session_folders(h) == before, 'Nothing new was downloaded, so there is no new folder'
    assert len(list(h.sessions.glob('*/old.7z'))) == 1


def test_file_from_an_nxm_link_gets_a_dated_folder_and_is_known_later(launch):
    h = launch(config={**SETUP_DONE, 'remember_api_key': True, 'api_key': 'test-key'},
               client=account(files={(GAME, 196600): [nexus_file(5, name='nxm.7z')]}))
    open_downloads(h)

    async def scenario():
        field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.label == 'NXM-ссылка от Mod Manager Download')
        field.value = f'nxm://{GAME}/mods/196600/files/5?key=abc&expires=9999999999&user_id=1'
        text_button(h, 'Добавить NXM в очередь')[0].on_click(None)
        await h.until(lambda: h.archive_of(GAME, 196600, 5))

    h.run(scenario)
    assert GOOD_NAME.match(h.archive_of(GAME, 196600, 5).path.parent.name)
    assert h.archive_of(GAME, 196600, 5).path.name == 'nxm.7z'


def test_cancelled_download_leaves_no_empty_folder(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        await h.next_job()
        h.click('Отмена')
        await asyncio.sleep(0.1)

    h.run(scenario)
    assert session_folders(h) == []


def test_unfinished_file_waits_outside_the_session(launch):
    directories = []

    def saved(url, directory, name, on_progress=None, cancel=None, **_):
        directories.append(Path(directory))
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / (name or 'mod.7z')
        target.write_bytes(b'7z')
        return target

    h = launch(config=SETUP_DONE, download=saved)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    assert h.sessions not in directories[0].parents
    assert h.archive_named('mod.7z')
    assert not directories[0].exists(), 'The waiting room is tidied up'


# ------------------------------------------------------------------ the «Загрузки» tab
def open_tab(h, label):
    next(c for c in h.tree() if isinstance(c, ft.Container) and c.on_click is not None
         and any(isinstance(x, ft.Text) and x.value == label for x in c.content.controls
                 if hasattr(c.content, 'controls'))).on_click(None)


def open_downloads(h):
    open_tab(h, 'Загрузки')


def icon_button(h, tooltip):
    return [c for c in h.tree() if isinstance(c, ft.IconButton) and c.tooltip == tooltip and c.visible is not False]


def text_button(h, label):
    return [c for c in h.tree() if isinstance(c, (ft.TextButton, ft.Button)) and c.content == label]


def texts(h):
    return [c.value for c in h.tree() if isinstance(c, ft.Text) and isinstance(c.value, str)]


def seed_two_sessions(h):
    older = h.seed_archive('skyrim', 1, 1, 'older.7z')
    newer_session = ModCollection.create(h.sessions, [], clock=lambda: datetime(2021, 6, 1, 12, 30, 0))
    path = newer_session.folder / 'newer.7z'
    path.write_bytes(b'7z')
    record_archive(path, 'skyrim', 2, 2)
    return older, path


def test_downloads_tab_shows_sessions_with_the_newest_unfolded(launch):
    h = launch(config=SETUP_DONE)
    seed_two_sessions(h)
    open_downloads(h)
    values = texts(h)
    assert h.text_starting('2 сессии · 2 файла')
    assert '1 июня 2021, 12:30' in values and '1 января 2020, 10:00' in values
    assert 'newer.7z' in values and 'older.7z' not in values, 'Older sessions stay folded'


def test_session_unfolds_and_folds_on_click(launch):
    h = launch(config=SETUP_DONE)
    seed_two_sessions(h)
    open_downloads(h)

    def header(title):
        return next(c for c in h.tree() if isinstance(c, ft.Container) and c.on_click is not None
                    and isinstance(c.content, ft.Row) and any(isinstance(x, ft.Column) and any(
                        isinstance(t, ft.Text) and t.value == title for t in x.controls) for x in c.content.controls))

    header('1 января 2020, 10:00').on_click(None)
    assert 'older.7z' in texts(h) and 'newer.7z' in texts(h)
    header('1 июня 2021, 12:30').on_click(None)
    assert 'older.7z' in texts(h) and 'newer.7z' not in texts(h)


def test_every_button_of_a_session_does_its_work(launch, monkeypatch):
    h = launch(config=SETUP_DONE)
    older, newer = seed_two_sessions(h)
    open_downloads(h)
    opened, commands = [], []
    monkeypatch.setattr(os, 'startfile', opened.append, raising=False)
    monkeypatch.setattr(h.module.subprocess, 'Popen', commands.append)

    icon_button(h, 'Открыть папку сессии')[0].on_click(None)
    assert opened[-1] == str(newer.parent)
    icon_button(h, 'Показать файл в папке')[0].on_click(None)
    assert commands[-1] == f'explorer /select,"{newer}"'
    text_button(h, 'Открыть все папки')[0].on_click(None)
    assert Path(opened[-1]) == h.sessions


def test_gone_file_or_folder_is_reported_and_leaves_the_list(launch, monkeypatch):
    h = launch(config=SETUP_DONE)
    older, newer = seed_two_sessions(h)
    open_downloads(h)
    opened, commands = [], []
    monkeypatch.setattr(os, 'startfile', opened.append, raising=False)
    monkeypatch.setattr(h.module.subprocess, 'Popen', commands.append)
    reveal = icon_button(h, 'Показать файл в папке')[0]
    open_session = icon_button(h, 'Открыть папку сессии')[0]
    newer.unlink()
    (newer.parent / MANIFEST).unlink()
    newer.parent.rmdir()

    reveal.on_click(None)
    assert not commands and h.text_starting('Этого файла уже нет')
    open_session.on_click(None)
    assert not opened and h.text_starting('Этой папки уже нет')
    assert 'newer.7z' not in texts(h)


def test_refresh_button_finds_what_appeared_on_disk(launch):
    h = launch(config=SETUP_DONE)
    open_downloads(h)
    assert h.text_starting('Здесь появятся скачанные файлы')
    h.seed_archive('skyrim', 1, 1, 'later.7z')
    icon_button(h, 'Обновить список')[0].on_click(None)
    assert 'later.7z' in texts(h) and h.text_starting('1 сессия · 1 файл')


def test_history_is_folded_and_a_finished_file_offers_no_dead_buttons(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    open_downloads(h)
    button = text_button(h, 'История загрузок (1)')[0]
    history = next(c for c in h.tree() if isinstance(c, ft.Column) and c.visible is False
                   and any(isinstance(t, ft.Text) and t.value == 'Готово' for x in c.controls for t in h_walk(x)))
    button.on_click(None)
    assert history.visible is True
    button.on_click(None)
    assert history.visible is False
    assert not text_button(h, 'Повторить') and not text_button(h, 'Отменить')


def h_walk(control):
    seen = set()
    from test_ui_end_to_end_v400 import walk
    return walk(control, seen)


def failing_once():
    calls = []

    def fake(url, directory, name, on_progress=None, cancel=None, **_):
        calls.append(url)
        if len(calls) == 1:
            raise DownloadError('Сервер не отвечает')
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / (name or 'mod.7z')
        target.write_bytes(b'7z')
        return target
    return fake


def test_retry_really_downloads_the_failed_file(launch):
    """The old button sent the browser to the site and threw away the link it brought back."""
    h = launch(config=SETUP_DONE, download=failing_once())

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
        assert 'Ошибок: 1' in h.finished()
        assert session_folders(h) == [], 'Nothing was downloaded, so no empty folder'

        open_downloads(h)
        assert h.text_starting('Сервер не отвечает')
        text_button(h, 'Повторить')[0].on_click(None)
        again = await h.next_job()
        assert (again['game'], again['mod_id'], again['file_id']) == (GAME, 196600, 1)
        await h.report(again, status='url', url=CDN, file_id=1)
        await h.until(lambda: h.archive_of(GAME, 196600, 1))
        await h.until(lambda: h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.')

    h.run(scenario)


def test_retry_works_after_the_program_was_restarted(launch):
    first = launch(config=SETUP_DONE, download=failing_once())

    async def before():
        first.paste_links(f'{MOD_A}?tab=files&file_id=1')
        first.click('Скачать выбранные')
        job = await first.next_job()
        await first.report(job, status='url', url=CDN, file_id=1)
        await first.until(first.finished)

    first.run(before)
    second = launch()

    async def after():
        open_downloads(second)
        retry = text_button(second, 'Повторить')
        assert len(retry) == 1, 'An old failure waits in the folded history, not at the top'
        retry[0].on_click(None)
        job = await second.next_job()
        assert job['file_id'] == 1
        await second.report(job, status='url', url=CDN, file_id=1)
        await second.until(lambda: second.archive_of(GAME, 196600, 1))
        await second.until(second.finished)

    second.run(after)


def test_retry_waits_for_the_running_download(launch):
    h = launch(config=SETUP_DONE, download=failing_once())

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
        open_downloads(h)
        retry = text_button(h, 'Повторить')[0]
        open_tab(h, 'Мои моды')
        h.paste_links(f'{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        await asyncio.sleep(0.1)
        h.page.update.reset_mock()
        retry.on_click(None)
        assert h.text_starting('Сейчас идёт другая загрузка')

    h.run(scenario)


def test_cancel_stops_the_file_being_downloaded(launch):
    started = threading.Event()

    def slow(url, directory, name, on_progress=None, cancel=None, **_):
        started.set()
        cancel.wait(20)
        raise DownloadCancelled('Загрузка отменена')

    h = launch(config=SETUP_DONE, download=slow)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await asyncio.to_thread(started.wait, 10)
        open_downloads(h)
        await h.until(lambda: text_button(h, 'Отменить'))
        text_button(h, 'Отменить')[0].on_click(None)
        await h.until(lambda: text_button(h, 'Повторить'))
        await h.until(h.finished)

    h.run(scenario)


def test_whole_download_can_be_cancelled_from_this_tab(launch):
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        await h.next_job()
        open_downloads(h)
        await h.until(lambda: text_button(h, 'Отменить загрузку'))
        text_button(h, 'Отменить загрузку')[0].on_click(None)
        assert (await h.get('/auto/next'))['job'] is None
        assert h.text_starting('Операция отменена')

    h.run(scenario)


def test_idle_downloads_tab_does_not_redraw(launch):
    h = launch(config=SETUP_DONE)
    h.seed_archive('skyrim', 1, 1, 'old.7z')
    open_downloads(h)

    async def scenario():
        await asyncio.sleep(1)
        h.page.update.reset_mock()
        await asyncio.sleep(2)

    h.run(scenario)
    assert h.page.update.call_count == 0


def test_main_screen_leads_to_the_archives(launch):
    h = launch(config=SETUP_DONE)
    text_button(h, 'Скачанные файлы')[0].on_click(None)
    assert text_button(h, 'Открыть все папки')


def test_old_default_folder_setting_is_replaced(launch):
    h = launch(config={**SETUP_DONE, 'folder': 'data/library/downloads'})
    open_tab(h, 'Настройки')
    field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.label == 'Папка для скачанных модов')
    assert Path(field.value) == h.sessions
