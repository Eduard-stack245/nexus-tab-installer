"""Own pace, files that are not archives, and real hand-over to Mod Organizer 2 and Vortex."""
import configparser
import os
import threading
import zipfile
from pathlib import Path

import flet as ft
import pytest

from nexus_tab_installer.browser import pacing
from nexus_tab_installer.browser.pacing import CUSTOM, DownloadPacer, PRESETS, custom_preset
from nexus_tab_installer.core.file_catalog import FileChoice, default_files
from nexus_tab_installer.core.file_kinds import is_archive_name, is_plain_file, is_program
from nexus_tab_installer.core.install_plan import ModPlan, validate_target
from nexus_tab_installer.core.paths import storage_paths
from nexus_tab_installer.core.settings import load_settings
from nexus_tab_installer.downloads.browser_watcher import BrowserDownloadWatcher
from nexus_tab_installer.integrations import delivery, mo2_install, vortex
from nexus_tab_installer.integrations.delivery import deliver, wrap_plain_file
from nexus_tab_installer.integrations.mo2_install import (MO2InstallError, check_game, hand_over, instance_info,
                                                          install_simple, mark_installed)
from test_pacing_v410 import Clock
from test_ui_end_to_end_v400 import CDN, GAME, KEY, MOD_A, account, launch, nexus_file  # noqa: F401 (fixture)

SETUP_DONE = {'browser_paired': True}
ITEM = {'game': 'skyrimspecialedition', 'mod_id': 9, 'title': 'Лук Ветра', 'download_file_id': 77}


def text_button(h, label):
    return [c for c in h.tree() if isinstance(c, (ft.TextButton, ft.Button)) and c.content == label]


def open_tab(h, label):
    next(c for c in h.tree() if isinstance(c, ft.Container) and c.on_click is not None and isinstance(c.content, ft.Row)
         and any(isinstance(x, ft.Text) and x.value == label for x in c.content.controls)).on_click(None)


def field_named(h, label):
    return next(c for c in h.tree() if isinstance(c, (ft.TextField, ft.Dropdown)) and c.label == label)


# ------------------------------------------------------------------ the person's own pace
def test_own_pace_takes_the_numbers_of_the_person():
    pace = custom_preset('5', '10', '2', '50')
    assert (pace.gap, pace.batch, pace.rest, pace.daily) == (5, 10, 120, 50)


@pytest.mark.parametrize('value', ['', None, 'abc', '1.5'])
def test_a_field_that_is_not_a_number_keeps_the_default(value):
    assert custom_preset(gap=value).gap == pacing.CUSTOM_DEFAULTS['gap']


def test_negative_and_huge_numbers_stay_in_range():
    assert custom_preset(gap='-5').gap == 0
    assert custom_preset(gap=str(10 ** 12)).gap == pacing.DAY


def test_zero_turns_every_pause_and_limit_off():
    clock = Clock()
    pacer = DownloadPacer(preset=custom_preset('0', '0', '0', '0'), clock=clock)
    for _ in range(1000):
        assert pacer.wait() == (0, '')
        pacer.record_start()
    assert pacer.estimate(1000) == 0


def test_own_pause_is_really_kept():
    clock = Clock()
    pacer = DownloadPacer(preset=custom_preset('7', '0', '0', '0'), clock=clock)
    pacer.record_start()
    assert pacer.wait() == (7, 'gap')
    clock.skip(7)
    assert pacer.wait() == (0, '')


def test_own_long_pause_and_daily_limit_are_really_kept():
    clock = Clock()
    pacer = DownloadPacer(preset=custom_preset('0', '2', '3', '0'), clock=clock)
    pacer.record_start(), clock.skip(1), pacer.record_start()
    assert pacer.wait() == (180, 'rest')
    pacer = DownloadPacer(preset=custom_preset('0', '0', '0', '1'), clock=clock)
    pacer.record_start()
    assert pacer.wait()[1] == 'daily'


def test_estimate_follows_the_own_numbers():
    pacer = DownloadPacer(preset=custom_preset('10', '3', '1', '0'), clock=Clock())
    # 6 files: 5 steps, one of them a long pause.
    assert pacer.estimate(6) == 10 * 4 + 60


def test_what_the_site_asks_for_stays_whatever_the_pace():
    clock = Clock()
    pacer = DownloadPacer(preset=custom_preset('0', '0', '0', '0'), clock=clock)
    pacer.record_start()
    pacer.record_failure()
    assert pacer.wait() == (pacing.MIN_FAILURE_GAP * 2, 'failures'), 'A failing site is not asked again at once'
    pacer.reset_failures()
    pacer.cool_down()
    assert pacer.wait()[1] == 'cooldown'


def test_ready_paces_are_unchanged():
    assert PRESETS['careful'].gap == 60 and PRESETS['moderate'].gap == 25


def settings_pace(h):
    open_tab(h, 'Настройки')
    return field_named(h, 'Темп скачивания через браузер')


def test_own_pace_is_chosen_in_settings_with_a_warning(launch):
    h = launch(config=SETUP_DONE)
    choice = settings_pace(h)
    gap = field_named(h, 'Пауза между файлами, секунд')
    fields = next(c for c in h.tree() if isinstance(c, ft.Column) and c.controls and isinstance(c.controls[0], ft.Row)
                  and gap in c.controls[0].controls)
    warning = next(c for c in h.tree() if isinstance(c, ft.Text) and (c.value or '').startswith('Свой темп вы выбираете'))
    assert not fields.visible and not warning.visible, 'Ready paces stay as simple as they were'
    choice.value = CUSTOM
    choice.on_select(None)
    assert fields.visible and warning.visible and 'страх и риск' in warning.value


def test_own_numbers_work_at_once_and_survive_a_restart(launch):
    h = launch(config=SETUP_DONE)
    choice = settings_pace(h)
    choice.value = CUSTOM
    choice.on_select(None)
    gap = field_named(h, 'Пауза между файлами, секунд')
    gap.value = '7'
    gap.on_change(None)
    assert h.text_starting('Файл раз в 7 секунд')
    gap.on_blur(None)
    saved = load_settings(storage_paths()['config'])
    assert saved['download_pace'] == CUSTOM and saved['pace_gap'] == '7'

    again = launch()
    open_tab(again, 'Настройки')
    assert field_named(again, 'Пауза между файлами, секунд').value == '7'
    assert again.text_starting('Файл раз в 7 секунд')


def test_no_pause_at_all_is_described_plainly(launch):
    h = launch(config={**SETUP_DONE, 'download_pace': CUSTOM, 'pace_gap': '0', 'pace_batch': '0', 'pace_daily': '0'})
    open_tab(h, 'Настройки')
    assert h.text_starting('Без паузы между файлами, длинных пауз нет, предела в сутки нет')


# ------------------------------------------------------------------ not only archives
@pytest.mark.parametrize('name', ['a.zip', 'A.7Z', 'b.rar', 'c.fomod', 'd.tar', 'e.001'])
def test_archives_are_known(name):
    assert is_archive_name(name) and not is_plain_file(name)


@pytest.mark.parametrize('name', ['plugin.esp', 'style.schem', 'mod.dll', 'read.txt', 'setup.exe'])
def test_other_files_are_plain(name):
    assert is_plain_file(name) and not is_archive_name(name)


def test_name_without_extension_is_not_taken_for_a_plain_file():
    assert not is_plain_file('Main File') and not is_plain_file('')


def test_programs_are_known():
    assert is_program('setup.EXE') and is_program('run.bat') and not is_program('a.esp')


def files(*rows):
    return [nexus_file(file_id, name=name, when=when, category=category) for file_id, name, when, category in rows]


def test_of_archives_only_the_newest_main_is_taken():
    assert default_files(files((1, 'old.zip', 1, 'MAIN'), (2, 'new.zip', 2, 'MAIN'), (3, 'extra.zip', 3, 'OPTIONAL'))) == [2]


def test_all_plain_main_files_are_taken():
    chosen = default_files(files((1, 'a.esp', 1, 'MAIN'), (2, 'b.esp', 2, 'MAIN'), (3, 'c.schem', 3, 'MAIN')))
    assert sorted(chosen) == [1, 2, 3]


def test_archive_and_plain_file_are_both_taken():
    chosen = default_files(files((1, 'mod.zip', 1, 'MAIN'), (2, 'mod-new.zip', 2, 'MAIN'), (3, 'patch.esp', 3, 'MAIN')))
    assert sorted(chosen) == [2, 3]


def test_older_copies_of_a_plain_file_are_not_taken():
    assert default_files(files((1, 'a.esp', 1, 'MAIN'), (2, 'A.ESP', 2, 'MAIN'))) == [2]


def test_plain_files_outside_main_are_not_added_by_themselves():
    assert default_files(files((1, 'mod.zip', 1, 'MAIN'), (2, 'extra.esp', 2, 'OPTIONAL'))) == [1]


def test_choice_of_a_person_is_still_exact():
    listed = files((1, 'a.esp', 1, 'MAIN'), (2, 'b.esp', 2, 'MAIN'))
    choice = FileChoice()
    choice.toggle(2, False, listed)
    assert [f['file_id'] for f in choice.resolve(listed)] == [1]


def test_manual_mode_takes_a_plain_file(tmp_path):
    clock = [1000.0]
    watcher = BrowserDownloadWatcher([tmp_path], clock=lambda: clock[0], steady_seconds=1)
    assert watcher.register('skyrim', 1, 2, 'style.schem')
    (tmp_path / 'style.schem').write_bytes(b'not an archive')
    os.utime(tmp_path / 'style.schem', (1000, 1000))
    assert watcher.poll() == []
    clock[0] += 2
    assert watcher.poll() == [('skyrim', 1, 2, tmp_path / 'style.schem')]


def test_manual_mode_does_not_take_what_is_still_downloading(tmp_path):
    clock = [1000.0]
    watcher = BrowserDownloadWatcher([tmp_path], clock=lambda: clock[0], steady_seconds=0)
    watcher.register('skyrim', 1, 2, 'style.schem.crdownload')
    (tmp_path / 'style.schem.crdownload').write_bytes(b'part')
    os.utime(tmp_path / 'style.schem.crdownload', (1000, 1000))
    clock[0] += 2
    watcher.poll()
    assert watcher.poll() == []


def test_mod_of_three_plain_files_comes_whole(launch):
    listed = {(GAME, 196600): files((1, 'a.esp', 1, 'MAIN'), (2, 'b.dll', 2, 'MAIN'), (3, 'c.txt', 3, 'MAIN'))}
    h = launch(config={**KEY, **SETUP_DONE}, client=account(files=listed))

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        for _ in range(3):
            job = await h.next_job()
            await h.report(job, status='url', url=CDN.replace('Mod-196600-1-0.7z', job['filename']), file_id=job['file_id'])
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 3 из 3 файлов.'
    assert sorted(a.path.name for a in (h.archive_of(GAME, 196600, n) for n in (1, 2, 3))) == ['a.esp', 'b.dll', 'c.txt']
    assert len({h.archive_of(GAME, 196600, n).path.parent for n in (1, 2, 3)}) == 1, 'All in one session'


def test_plain_file_saved_by_the_browser_is_taken_as_it_is(launch, tmp_path):
    source = tmp_path / 'Downloads' / 'style.schem'
    source.parent.mkdir()
    source.write_bytes(b'not an archive')
    h = launch(config=SETUP_DONE)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='file', path=str(source), file_id=5, filename='style.schem')
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'
    assert h.archive_of(GAME, 196600, 5).path.read_bytes() == b'not an archive'


def test_wrapper_holds_the_file_at_its_root(tmp_path):
    plain = tmp_path / 'plugin.esp'
    plain.write_bytes(b'esp')
    wrapper = wrap_plain_file(plain, tmp_path / 'out')
    assert wrapper.name == 'plugin.zip'
    with zipfile.ZipFile(wrapper) as archive:
        assert archive.namelist() == ['plugin.esp'] and archive.read('plugin.esp') == b'esp'


# ------------------------------------------------------------------ Mod Organizer 2
@pytest.fixture
def mo2(tmp_path, monkeypatch):
    monkeypatch.setattr(mo2_install, 'mo2_is_running', lambda: False)
    monkeypatch.setattr(delivery, 'mo2_is_running', lambda: False)
    root = tmp_path / 'MO2'
    (root / 'mods').mkdir(parents=True)
    (root / 'downloads').mkdir()
    (root / 'profiles' / 'Default').mkdir(parents=True)
    (root / 'profiles' / 'Default' / 'modlist.txt').write_text('# This file was automatically generated.\n+Old mod\n', encoding='utf-8')
    ini = root / 'ModOrganizer.ini'
    ini.write_text('[General]\ngameName=Skyrim Special Edition\nselected_profile=@ByteArray(Default)\nversion=2.5.2\n'
                   '[Settings]\n', encoding='utf-8')
    return ini


def session_file(tmp_path, name, content=None):
    folder = tmp_path / 'session'
    folder.mkdir(exist_ok=True)
    path = folder / name
    if name.endswith('.zip') and content is None:
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('textures/floor.dds', 'data')
    else:
        path.write_bytes(content if content is not None else b'file')
    return path


def to_mo2(path, ini, tmp_path, item=ITEM):
    return deliver(path, item, 'mo2', staging_root=tmp_path / 'stage', journal=tmp_path / 'managed',
                   mo2_ini=str(ini), mo2_profile='Default')


def read_note(path):
    note = configparser.ConfigParser(interpolation=None)
    note.optionxform = str
    note.read(path, encoding='utf-8')
    return note


def test_instance_details_are_read_from_its_settings(mo2):
    info = instance_info(mo2)
    assert (info['game'], info['version'], info['profile']) == ('Skyrim Special Edition', '2.5.2', 'Default')


def test_simple_mod_is_installed_and_on_top_of_the_list(mo2, tmp_path):
    state, detail = to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path)
    assert state == 'Установлен MO2' and 'Лук Ветра' in detail and 'Default' in detail
    root = mo2.parent
    assert (root / 'mods' / 'Лук Ветра' / 'textures' / 'floor.dds').is_file()
    lines = (root / 'profiles' / 'Default' / 'modlist.txt').read_text(encoding='utf-8').splitlines()
    assert lines[:3] == ['# This file was automatically generated.', '+Лук Ветра', '+Old mod'], 'New mods go on top, like in MO2'


def test_installed_mod_knows_its_nexus_file(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path)
    note = read_note(mo2.parent / 'mods' / 'Лук Ветра' / 'meta.ini')
    assert note['General']['modid'] == '9' and note['General']['gameName'] == 'SkyrimSE'
    assert note['General']['installationFile'] == '"bow.zip"' and note['General']['repository'] == 'Nexus'
    assert note['installedFiles']['1\\fileid'] == '77' and note['installedFiles']['size'] == '1'


def test_download_appears_in_downloads_with_nexus_details(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow, final.zip'), mo2, tmp_path)
    downloads = mo2.parent / 'downloads'
    assert (downloads / 'bow, final.zip').is_file()
    note = read_note(downloads / 'bow, final.zip.meta')['General']
    assert note['gameName'] == 'SkyrimSE' and note['modID'] == '9' and note['fileID'] == '77'
    assert note['name'] == '"bow, final.zip"', 'A bare comma would turn the name into a list in MO2'
    assert note['modName'] == '"Лук Ветра"' and note['installed'] == 'true'


def test_note_is_written_after_the_file(mo2, tmp_path, monkeypatch):
    """Found on a real MO2: while running, it deletes a note whose file is not there yet."""
    seen = []
    real = mo2_install._write_download_note

    def watching(note, details):
        seen.append(Path(str(note)[:-len('.meta')]).is_file())
        return real(note, details)

    monkeypatch.setattr(mo2_install, '_write_download_note', watching)
    hand_over(session_file(tmp_path, 'bow.zip'), mo2, {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'})
    assert seen == [True]
    assert not [p for p in (mo2.parent / 'downloads').iterdir() if p.name.startswith('.')], 'Nothing temporary stays'


def test_note_lost_to_mo2_comes_back_with_the_next_hand_over(mo2, tmp_path):
    source = session_file(tmp_path, 'bow.zip')
    details = {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'}
    target = hand_over(source, mo2, details)
    Path(str(target) + '.meta').unlink()
    hand_over(source, mo2, details)
    assert read_note(str(target) + '.meta')['General']['fileID'] == '77'


def test_same_disk_shares_one_copy_of_the_file(mo2, tmp_path):
    source = session_file(tmp_path, 'bow.zip')
    target = hand_over(source, mo2, {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'})
    assert source.stat().st_nlink == 2 and source.samefile(target)
    target.unlink()
    assert source.is_file(), 'Removing it in MO2 leaves the session alone'


def test_handing_the_same_file_twice_changes_nothing(mo2, tmp_path):
    source = session_file(tmp_path, 'bow.zip')
    details = {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'}
    assert hand_over(source, mo2, details) == hand_over(source, mo2, details)
    assert sorted(p.name for p in (mo2.parent / 'downloads').iterdir()) == ['bow.zip', 'bow.zip.meta']


def test_different_file_with_the_same_name_is_not_overwritten(mo2, tmp_path):
    (mo2.parent / 'downloads' / 'bow.zip').write_bytes(b'somebody elses')
    target = hand_over(session_file(tmp_path, 'bow.zip'), mo2, {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'})
    assert target.name == 'bow (1).zip' and (mo2.parent / 'downloads' / 'bow.zip').read_bytes() == b'somebody elses'
    assert read_note(str(target) + '.meta')['General']['name'] == '"bow (1).zip"'


def test_mark_installed_only_changes_the_mark(mo2, tmp_path):
    target = hand_over(session_file(tmp_path, 'bow.zip'), mo2, {**ITEM, 'file_id': 77, 'file_name': 'bow.zip'})
    mark_installed(target)
    note = read_note(str(target) + '.meta')['General']
    assert note['installed'] == 'true' and note['uninstalled'] == 'false'


def test_open_mo2_gets_the_file_but_nothing_is_installed(mo2, tmp_path, monkeypatch):
    monkeypatch.setattr(mo2_install, 'mo2_is_running', lambda: True)
    monkeypatch.setattr(delivery, 'mo2_is_running', lambda: True)
    state, detail = to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path)
    assert state == 'Требует MO2' and 'двойным щелчком' in detail
    assert (mo2.parent / 'downloads' / 'bow.zip').is_file() and not list((mo2.parent / 'mods').iterdir())


def test_fomod_waits_for_the_installer_of_mo2(mo2, tmp_path):
    path = tmp_path / 'fomod.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('fomod/ModuleConfig.xml', '<config/>')
    state, detail = to_mo2(path, mo2, tmp_path)
    assert state == 'Требует MO2' and 'Downloads' in detail
    assert (mo2.parent / 'downloads' / 'fomod.zip').is_file() and not list((mo2.parent / 'mods').iterdir())


def test_format_only_mo2_can_open_still_reaches_it(mo2, tmp_path):
    state, detail = to_mo2(session_file(tmp_path, 'mod.fomod', b'x'), mo2, tmp_path)
    assert state == 'Требует MO2' and (mo2.parent / 'downloads' / 'mod.fomod').is_file()


def test_plain_file_reaches_mo2_in_a_zip_and_as_itself_in_the_mod(mo2, tmp_path):
    state, detail = to_mo2(session_file(tmp_path, 'bow.esp', b'plugin'), mo2, tmp_path)
    root = mo2.parent
    assert state == 'Установлен MO2' and 'Plugins' in detail
    assert (root / 'mods' / 'Лук Ветра' / 'bow.esp').read_bytes() == b'plugin'
    with zipfile.ZipFile(root / 'downloads' / 'bow.zip') as archive:
        assert archive.read('bow.esp') == b'plugin'
    assert (tmp_path / 'session' / 'bow.esp').read_bytes() == b'plugin', 'The session keeps the file as downloaded'


def test_several_plain_files_of_a_mod_land_in_one_mod_folder(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow.esp', b'plugin'), mo2, tmp_path, {**ITEM, 'download_file_id': 1})
    to_mo2(session_file(tmp_path, 'bow.dll', b'code'), mo2, tmp_path, {**ITEM, 'download_file_id': 2})
    root = mo2.parent
    folder = root / 'mods' / 'Лук Ветра'
    assert sorted(p.name for p in folder.iterdir()) == ['bow.dll', 'bow.esp', 'meta.ini']
    note = read_note(folder / 'meta.ini')['installedFiles']
    assert note['size'] == '2' and {note['1\\fileid'], note['2\\fileid']} == {'1', '2'}
    assert (root / 'profiles' / 'Default' / 'modlist.txt').read_text(encoding='utf-8').count('+Лук Ветра') == 1


def test_a_different_plain_file_with_the_same_name_is_not_overwritten(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow.esp', b'one'), mo2, tmp_path, {**ITEM, 'download_file_id': 1})
    state, detail = to_mo2(session_file(tmp_path, 'bow.esp', b'two'), mo2, tmp_path, {**ITEM, 'download_file_id': 2})
    assert state == 'Требует MO2' and 'другой файл' in detail
    assert (mo2.parent / 'mods' / 'Лук Ветра' / 'bow.esp').read_bytes() == b'one'


def test_same_file_is_not_installed_twice(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path)
    state, detail = to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path)
    assert state == 'Установлен MO2' and 'уже установлен' in detail
    assert [p.name for p in (mo2.parent / 'mods').iterdir()] == ['Лук Ветра']


def test_another_archive_of_the_mod_gets_its_own_folder(mo2, tmp_path):
    to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path, {**ITEM, 'download_file_id': 1})
    (tmp_path / 'session' / 'bow2.zip').write_bytes((tmp_path / 'session' / 'bow.zip').read_bytes())
    to_mo2(tmp_path / 'session' / 'bow2.zip', mo2, tmp_path, {**ITEM, 'download_file_id': 2})
    assert sorted(p.name for p in (mo2.parent / 'mods').iterdir()) == ['Лук Ветра', 'Лук Ветра (2)']


def test_program_is_saved_but_not_given_to_a_manager(mo2, tmp_path):
    state, detail = to_mo2(session_file(tmp_path, 'setup.exe'), mo2, tmp_path)
    assert state == 'Готово' and 'запустите его сами' in detail
    assert not list((mo2.parent / 'downloads').iterdir())


def test_mod_of_another_game_is_refused(mo2, tmp_path):
    with pytest.raises(MO2InstallError, match='Skyrim Special Edition'):
        to_mo2(session_file(tmp_path, 'bow.zip'), mo2, tmp_path, {**ITEM, 'game': 'fallout4'})
    assert not list((mo2.parent / 'downloads').iterdir())


def test_skyrim_vr_takes_mods_of_special_edition(mo2):
    mo2.write_text('[General]\ngameName=Skyrim VR\n', encoding='utf-8')
    check_game(mo2, 'skyrimspecialedition')


def test_unfamiliar_game_is_not_judged(mo2):
    mo2.write_text('[General]\ngameName=Some New Game\n', encoding='utf-8')
    check_game(mo2, 'somenewgame')


def test_install_simple_without_details_stays_as_strict_as_it_was(mo2, tmp_path):
    stage = tmp_path / 'stage'
    stage.mkdir()
    (stage / 'a.esp').write_bytes(b'x')
    install_simple(stage, mo2, 'Default', 'Mod')
    with pytest.raises(MO2InstallError):
        install_simple(stage, mo2, 'Default', 'Mod')


# ------------------------------------------------------------------ Vortex
@pytest.fixture
def fake_vortex(tmp_path, monkeypatch):
    exe = tmp_path / 'Vortex' / 'Vortex.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    state = {'running': False, 'active': None, 'launched': [], 'known': {'skyrimse'}, 'switches': True, 'exe': exe}
    monkeypatch.setattr(vortex, 'is_running', lambda: state['running'])
    monkeypatch.setattr(vortex, 'read_state', lambda _exe, key, timeout=0: 'D:/Games' if key.split('.')[3] in state['known'] else None)
    monkeypatch.setattr(vortex, 'active_game', lambda log=None: state['active'])
    monkeypatch.setattr(vortex, 'wait_for_game', lambda game, offset=0, seconds=0, log=None: state['switches'] and state['active'] == game)
    monkeypatch.setattr(vortex, 'log_path', lambda env=None: None)

    def launch_program(arguments):
        state['launched'].append(arguments[1:])
        if arguments[1] == '--game':
            state['running'] = True
            state['active'] = arguments[2]

    monkeypatch.setattr(vortex, '_launch', launch_program)
    return state


def to_vortex(path, fake, tmp_path, item=ITEM, folder=''):
    return deliver(path, item, 'vortex', staging_root=tmp_path / 'stage', journal=tmp_path / 'managed',
                   vortex_exe=str(fake['exe']), vortex_folder=folder)


def test_closed_vortex_gets_the_game_first_and_the_file_after_it_is_active(fake_vortex, tmp_path):
    """Found on the real Vortex 2.0.2: both in one command installed the file into the previous game."""
    path = session_file(tmp_path, 'bow.zip')
    state, detail = to_vortex(path, fake_vortex, tmp_path)
    assert state == 'Передано Vortex' and 'устанавливает' in detail
    assert fake_vortex['launched'] == [['--game', 'skyrimse'], ['--install-archive', str(path.resolve())]]


def test_file_is_not_sent_if_the_switch_of_the_game_was_not_seen(fake_vortex, tmp_path):
    fake_vortex['switches'] = False
    downloads = tmp_path / 'downloads'
    state, detail = to_vortex(session_file(tmp_path, 'bow.zip'), fake_vortex, tmp_path, folder=str(downloads))
    assert fake_vortex['launched'] == [['--game', 'skyrimse']], 'No install while the game is not certain'
    assert (downloads / 'skyrimse' / 'bow.zip').is_file() and 'Install' in detail


def test_vortex_open_on_this_game_installs_at_once(fake_vortex, tmp_path):
    fake_vortex.update(running=True, active='skyrimse')
    path = session_file(tmp_path, 'bow.zip')
    to_vortex(path, fake_vortex, tmp_path)
    assert fake_vortex['launched'] == [['--install-archive', str(path.resolve())]]


def test_game_that_vortex_does_not_manage_is_refused(fake_vortex, tmp_path):
    with pytest.raises(vortex.VortexError, match='fallout4'):
        to_vortex(session_file(tmp_path, 'bow.zip'), fake_vortex, tmp_path, {**ITEM, 'game': 'fallout4'})
    assert fake_vortex['launched'] == []


@pytest.mark.parametrize('active', ['fallout4', None])
def test_vortex_open_on_another_or_unknown_game_is_not_given_an_install(fake_vortex, tmp_path, active):
    """--install-archive would put a Skyrim mod into whatever game happens to be active."""
    fake_vortex.update(running=True, active=active)
    downloads = tmp_path / 'Roaming' / 'downloads'
    path = session_file(tmp_path, 'bow.zip')
    state, detail = to_vortex(path, fake_vortex, tmp_path, folder=str(downloads))
    assert fake_vortex['launched'] == []
    assert state == 'Передано Vortex' and 'Install' in detail
    assert (downloads / 'skyrimse' / 'bow.zip').samefile(path)


def test_file_goes_to_its_game_folder_even_if_that_folder_was_chosen(fake_vortex, tmp_path):
    fake_vortex.update(running=True, active='fallout4')
    folder = tmp_path / 'skyrimse'
    to_vortex(session_file(tmp_path, 'bow.zip'), fake_vortex, tmp_path, folder=str(folder))
    assert (folder / 'bow.zip').is_file() and not (folder / 'skyrimse').exists()


REAL_LOG_LINES = (b'2026-11-06T23:39:14.216Z [DEBG] [RENDERER] [checking staging folder] {"gameMode":"fallout4"}\n'
                  b'2026-11-06T23:39:18.673Z [DEBG] [RENDERER] gamemode activated "fallout4"\n'
                  b'2026-11-06T23:40:18.673Z [DEBG] [RENDERER] gamemode activated "skyrimse"\n'
                  b'2026-11-06T23:40:18.693Z [DEBG] [RENDERER] [mod-dependency-manager] game mode activated {"gameMode":"skyrimse"}\n')


def test_active_game_is_read_from_the_log_of_vortex(tmp_path):
    log = tmp_path / 'vortex.log'
    log.write_bytes(REAL_LOG_LINES)
    assert vortex.active_game(log) == 'skyrimse'


def test_log_that_says_nothing_gives_no_answer(tmp_path):
    log = tmp_path / 'vortex.log'
    log.write_bytes(b'something else\n')
    assert vortex.active_game(log) is None
    assert vortex.active_game(tmp_path / 'absent.log') is None


def test_only_what_the_log_says_after_the_start_counts(tmp_path, monkeypatch):
    """A line from the previous run must not pass for the switch that is being waited for."""
    monkeypatch.setattr(vortex, 'SETTLE_SECONDS', 0)
    log = tmp_path / 'vortex.log'
    log.write_bytes(REAL_LOG_LINES)
    offset = log.stat().st_size
    assert not vortex.wait_for_game('skyrimse', offset, seconds=0.2, log=log)
    with log.open('ab') as stream:
        stream.write(b'2026-11-06T23:50:00.000Z [DEBG] [RENDERER] gamemode activated "fallout4"\n')
    assert not vortex.wait_for_game('skyrimse', offset, seconds=0.2, log=log)
    with log.open('ab') as stream:
        stream.write(b'2026-11-06T23:50:05.000Z [DEBG] [RENDERER] gamemode activated "skyrimse"\n')
    assert vortex.wait_for_game('skyrimse', offset, seconds=0.2, log=log)


def test_plain_file_reaches_vortex_in_a_zip_that_stays_for_it(fake_vortex, tmp_path):
    to_vortex(session_file(tmp_path, 'bow.esp', b'plugin'), fake_vortex, tmp_path)
    sent = Path(fake_vortex['launched'][-1][-1])
    assert sent.suffix == '.zip' and sent.is_file(), 'Vortex takes it a little later'
    with zipfile.ZipFile(sent) as archive:
        assert archive.read('bow.esp') == b'plugin'


def test_program_is_not_given_to_vortex(fake_vortex, tmp_path):
    state, detail = to_vortex(session_file(tmp_path, 'setup.exe'), fake_vortex, tmp_path)
    assert state == 'Готово' and fake_vortex['launched'] == []


def test_without_a_program_the_chosen_folder_still_works(tmp_path):
    folder = tmp_path / 'inbox'
    folder.mkdir()
    state, detail = deliver(session_file(tmp_path, 'bow.zip'), ITEM, 'vortex', staging_root=tmp_path / 's',
                            journal=tmp_path / 'm', vortex_folder=str(folder))
    assert state == 'Передано Vortex' and (folder / 'bow.zip').is_file()


def test_answer_of_vortex_is_read_through_its_noise(tmp_path, monkeypatch):
    class Done:
        stdout = (b'BROKEN LOGGING: debug something\r\n'
                  b'settings.downloads.path = "C:\\\\Users\\\\Me\\\\downloads"\r\n')
        stderr = b''

    monkeypatch.setattr(vortex.subprocess, 'run', lambda *a, **k: Done)
    assert vortex.read_state('Vortex.exe', 'settings.downloads.path') == 'C:\\Users\\Me\\downloads'


@pytest.mark.parametrize('module, name, found', [(mo2_install, 'mo2_is_running', b'ModOrganizer.exe  12 Console\r\n'),
                                                  (vortex, 'is_running', b'Vortex.exe  12 Console\r\n')])
def test_a_russian_answer_of_windows_does_not_break_the_check(module, name, found, monkeypatch):
    """The answer comes in the console code page: read as text in UTF-8 mode it used to crash."""
    class Done:
        stdout = '\u0418\u043c\u044f \u043e\u0431\u0440\u0430\u0437\u0430\r\n'.encode('cp866') + found

    monkeypatch.setattr('subprocess.run', lambda *a, **k: Done)
    assert getattr(module, name)() is True
    Done.stdout = '\u0417\u0430\u0434\u0430\u0447\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u044b'.encode('cp866')
    assert getattr(module, name)() is False


def test_silence_of_vortex_is_no_answer(monkeypatch):
    class Done:
        stdout = b'BROKEN LOGGING: debug something\r\n'
        stderr = b''

    monkeypatch.setattr(vortex.subprocess, 'run', lambda *a, **k: Done)
    assert vortex.read_state('Vortex.exe', 'settings.gameMode.discovered.skyrimse.path') is None


def test_vortex_is_found_in_a_folder_of_the_list(tmp_path):
    (tmp_path / 'Vortex').mkdir()
    (tmp_path / 'Vortex' / 'Vortex.exe').write_bytes(b'exe')
    assert vortex.find_vortex(folders=[tmp_path / 'nothing', tmp_path / 'Vortex']) == tmp_path / 'Vortex' / 'Vortex.exe'
    assert vortex.find_vortex(folders=[tmp_path / 'nothing']) is None


def test_game_names_of_nexus_and_vortex_are_matched():
    assert vortex.game_id('skyrimspecialedition') == 'skyrimse'
    assert vortex.game_id('newvegas') == 'falloutnv'
    assert vortex.game_id('fallout4') == 'fallout4'


def test_vortex_target_needs_a_program_or_a_folder(tmp_path):
    with pytest.raises(ValueError, match='Vortex.exe'):
        validate_target('vortex')
    exe = tmp_path / 'Vortex.exe'
    exe.write_bytes(b'x')
    validate_target('vortex', vortex_exe=str(exe))


# ------------------------------------------------------------------ in the window
def test_destination_for_the_whole_list_is_kept_for_later_mods():
    plan = ModPlan()
    items = [{'game': GAME, 'mod_id': 1, 'url': MOD_A}]
    plan.replace(items)
    plan.set_all_targets('mo2')
    assert plan.chosen()[0][1] == 'mo2'
    plan.replace([*items, {'game': GAME, 'mod_id': 2, 'url': MOD_A.replace('196600', '2')}])
    assert [target for _, target in plan.chosen()] == ['mo2', 'mo2']


def fake_instance_ui(h, monkeypatch, tmp_path):
    root = tmp_path / 'MO2'
    (root / 'mods').mkdir(parents=True)
    (root / 'downloads').mkdir()
    (root / 'profiles' / 'Default').mkdir(parents=True)
    (root / 'profiles' / 'Default' / 'modlist.txt').write_text('', encoding='utf-8')
    ini = root / 'ModOrganizer.ini'
    ini.write_text('[General]\ngameName=Skyrim Special Edition\nselected_profile=@ByteArray(Default)\nversion=2.5.2\n', encoding='utf-8')
    return ini


def test_managers_are_found_without_typing_paths(launch, monkeypatch, tmp_path):
    ini = fake_instance_ui(None, monkeypatch, tmp_path)
    (tmp_path / 'Vortex.exe').write_bytes(b'x')
    first = launch(config=SETUP_DONE)  # the fixture is ready: now the computer "has" the managers
    monkeypatch.setattr(first.module, 'find_mo2_instances', lambda *_: [ini])
    monkeypatch.setattr(first.module.vortex_link, 'find_vortex', lambda *a, **k: tmp_path / 'Vortex.exe')
    h = launch()
    text_button(h, 'Настроить MO2 / Vortex / папку игры')[0].on_click(None)
    assert field_named(h, 'Папка MO2 или путь к ModOrganizer.ini').value == str(ini)
    assert field_named(h, 'Профиль MO2').value == 'Default'
    assert field_named(h, 'Vortex.exe').value == str(tmp_path / 'Vortex.exe')
    assert h.text_starting('MO2 2.5.2: Skyrim Special Edition, профиль Default.')


def test_two_instances_are_not_guessed(launch, monkeypatch, tmp_path):
    first = launch(config=SETUP_DONE)
    monkeypatch.setattr(first.module, 'find_mo2_instances', lambda *_: [tmp_path / 'a.ini', tmp_path / 'b.ini'])
    h = launch()
    text_button(h, 'Настроить MO2 / Vortex / папку игры')[0].on_click(None)
    assert field_named(h, 'Папка MO2 или путь к ModOrganizer.ini').value == ''


def test_download_goes_straight_to_mo2_from_the_list(launch, monkeypatch, tmp_path):
    ini = fake_instance_ui(None, monkeypatch, tmp_path)
    monkeypatch.setattr(mo2_install, 'mo2_is_running', lambda: False)
    monkeypatch.setattr(delivery, 'mo2_is_running', lambda: False)

    def saved(url, directory, name, on_progress=None, cancel=None, **_):
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / 'bow.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('textures/floor.dds', 'data')
        return target

    h = launch(config={**SETUP_DONE, 'mo2_ini': str(ini), 'mo2_profile': 'Default'}, download=saved)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        target = field_named(h, 'Куда для всех модов')
        target.value = 'mo2'
        target.on_select(None)
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=5)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'
    assert (ini.parent / 'downloads' / 'bow.zip').is_file() and (ini.parent / 'downloads' / 'bow.zip.meta').is_file()
    assert len(list((ini.parent / 'mods').iterdir())) == 1
    assert h.text_starting('Мод «')
    assert load_settings(storage_paths()['config'])['default_target'] == 'mo2'


def test_setup_opens_only_when_something_is_missing(launch, tmp_path):
    h = launch(config=SETUP_DONE)
    panel = next(c for c in h.tree() if isinstance(c, ft.Container) and c.visible is False and isinstance(c.content, ft.Column)
                 and any(isinstance(x, ft.Text) and x.value == 'Настроить место установки' for x in c.content.controls))
    h.paste_links(MOD_A)
    target = field_named(h, 'Куда для всех модов')
    target.value = 'vortex'
    target.on_select(None)
    assert panel.visible, 'Vortex is not known here, so the person has to say where it is'
    panel.visible = False
    (tmp_path / 'Vortex.exe').write_bytes(b'x')
    field_named(h, 'Vortex.exe').value = str(tmp_path / 'Vortex.exe')
    target.on_select(None)
    assert not panel.visible, 'Nothing to ask any more'


def test_buttons_for_the_managers_work(launch, monkeypatch, tmp_path):
    ini = fake_instance_ui(None, monkeypatch, tmp_path)
    (ini.parent / 'ModOrganizer.exe').write_bytes(b'x')
    (tmp_path / 'Vortex.exe').write_bytes(b'x')
    h = launch(config={**SETUP_DONE, 'mo2_ini': str(ini), 'vortex_exe': str(tmp_path / 'Vortex.exe')})
    started = []
    monkeypatch.setattr(os, 'startfile', started.append, raising=False)
    text_button(h, 'Открыть MO2')[0].on_click(None)
    text_button(h, 'Открыть Vortex')[0].on_click(None)
    assert [Path(p).name for p in started] == ['ModOrganizer.exe', 'Vortex.exe']


def test_vortex_check_fills_the_downloads_folder(launch, monkeypatch, tmp_path):
    (tmp_path / 'Vortex.exe').write_bytes(b'x')
    h = launch(config={**SETUP_DONE, 'vortex_exe': str(tmp_path / 'Vortex.exe')})
    monkeypatch.setattr(h.module.vortex_link, 'describe', lambda exe: {'running': False, 'version': '2.0.2', 'downloads': 'C:/V/downloads'})

    async def scenario():
        text_button(h, 'Проверить Vortex')[0].on_click(None)
        await h.until(lambda: h.text_starting('Vortex 2.0.2 отвечает'))

    h.run(scenario)
    assert field_named(h, 'Папка загрузок Vortex (если пусто, стандартная)').value == 'C:/V/downloads'


def test_several_deliveries_at_once_keep_every_note(mo2, tmp_path):
    """Files finish in parallel threads and share the profile and one note in MO2's mod folder."""
    errors = []
    paths = {number: session_file(tmp_path, f'{number}.esp', b'x' * number) for number in range(1, 9)}

    def run(number):
        try:
            to_mo2(paths[number], mo2, tmp_path, {**ITEM, 'download_file_id': number})
        except Exception as exc:  # the assertion below names them
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(n,)) for n in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert read_note(mo2.parent / 'mods' / 'Лук Ветра' / 'meta.ini')['installedFiles']['size'] == '8'
    assert (mo2.parent / 'profiles' / 'Default' / 'modlist.txt').read_text(encoding='utf-8').count('+Лук Ветра') == 1
