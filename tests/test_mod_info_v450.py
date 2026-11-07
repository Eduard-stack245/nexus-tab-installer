"""Requirements, updates, room on the disk, deleting, messages. Texts of the real Nexus are used as examples."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus_tab_installer.core import disk_space
from nexus_tab_installer.core.requirements import (DOWNLOADED, ELSEWHERE, GAME_DLC, INSTALLED, LISTED, MISSING, Present, as_item, assess,
                                                   looks_optional, overview)
from nexus_tab_installer.core.session_library import ModCollection, list_sessions, record_archive, remove_archive, remove_session
from nexus_tab_installer.core.updates import Owned, Update, find_updates, load_found, newer_file, owned_mods, save_found
from nexus_tab_installer.integrations.installed import mo2_mods, mod_id_from_name, vortex_mods
from nexus_tab_installer.nexus import graphql
from nexus_tab_installer.nexus.api import NexusError
from nexus_tab_installer.nexus.graphql import ModInfo, Requirement, mod_files, mod_infos
from nexus_tab_installer.platform import notify as notify_module, recycle

GAME = 'skyrimspecialedition'
# An answer with the shape of the real one, for SSE Display Tweaks.
NODE = {'modId': 34705, 'name': 'SSE Display Tweaks', 'version': '0.5.25', 'updatedAt': '2026-08-29T20:20:34Z', 'fileSize': 182, 'status': 'published',
        'game': {'id': 1704, 'domainName': GAME, 'name': 'Skyrim Special Edition'},
        'modRequirements': {'nexusRequirements': {'nodes': [
            {'modId': 32444, 'gameId': 1704, 'modName': 'Address Library for SKSE Plugins', 'notes': '', 'url': '', 'externalRequirement': False},
            {'modId': 0, 'gameId': 0, 'modName': 'SKSE', 'notes': '', 'url': 'https://skse.silverlock.org/', 'externalRequirement': True},
            {'modId': 58101, 'gameId': 1704, 'modName': 'VR Address Library for SKSEVR', 'notes': 'Only for VR users, very latest version!', 'url': '', 'externalRequirement': False},
            {'modId': 900, 'gameId': 2295, 'modName': 'A tool of another game', 'notes': '', 'url': '', 'externalRequirement': False},
        ]}, 'dlcRequirements': [{'gameExpansion': {'name': 'Dawnguard'}, 'notes': ''}]}}


class Reply:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.data).encode()


@pytest.fixture
def nexus(monkeypatch):
    """Plays the site. Records what is sent to it."""
    sent = []
    answers = {'next': []}

    def fake_urlopen(request, timeout=0):
        sent.append({'headers': dict(request.headers), 'body': json.loads(request.data.decode())})
        return Reply(answers['next'].pop(0))
    monkeypatch.setattr(graphql, 'urlopen', fake_urlopen)
    monkeypatch.setattr(graphql, 'MIN_GAP', 0)
    return SimpleNamespace(sent=sent, answers=answers['next'])


# ------------------------------------------------------------------ the site
def test_all_of_a_list_costs_one_request_and_sends_no_key(nexus):
    nexus.answers += [{'data': {'legacyModsByDomain': {'nodes': [NODE]}}}, {'data': {'q0': {'game': {'id': 2295, 'domainName': 'site'}}}}]
    found = mod_infos([(GAME, 34705), (GAME, 34705), (GAME, 32444)])
    info = found[(GAME, 34705)]
    assert (info.name, info.version, info.size_kb, info.game_number, info.game_name) == ('SSE Display Tweaks', '0.5.25', 182, 1704, 'Skyrim Special Edition')
    assert info.updated_at == datetime.fromisoformat('2026-08-29T20:20:34+00:00').timestamp()
    variables = nexus.sent[0]['body']['variables']
    assert variables['ids'] == [{'gameDomain': GAME, 'modId': 34705}, {'gameDomain': GAME, 'modId': 32444}], 'A repeated mod is asked about once'
    headers = {name.lower(): value for name, value in nexus.sent[0]['headers'].items()}
    assert 'apikey' not in headers and headers['application-name'] == 'NexusTabInstaller'


def test_requirement_kinds_are_told_apart(nexus):
    nexus.answers += [{'data': {'legacyModsByDomain': {'nodes': [NODE]}}}, {'data': {'q0': {'game': {'id': 2295, 'domainName': 'site'}}}}]
    needs = {need.name: need for need in mod_infos([(GAME, 34705)])[(GAME, 34705)].requirements}
    assert (needs['Address Library for SKSE Plugins'].mod_id, needs['Address Library for SKSE Plugins'].game) == (32444, GAME)
    assert needs['SKSE'].external and needs['SKSE'].url == 'https://skse.silverlock.org/' and not needs['SKSE'].mod_id
    assert needs['A tool of another game'].game == 'site', 'The domain of another game is asked in a second request'
    assert needs['Dawnguard'].dlc


def test_long_list_goes_in_parts(nexus, monkeypatch):
    monkeypatch.setattr(graphql, 'CHUNK', 2)
    nexus.answers += [{'data': {'legacyModsByDomain': {'nodes': []}}}, {'data': {'legacyModsByDomain': {'nodes': []}}}]
    assert mod_infos([(GAME, 1), (GAME, 2), (GAME, 3)]) == {}
    assert [len(call['body']['variables']['ids']) for call in nexus.sent] == [2, 1]


def test_part_of_an_answer_with_an_error_still_counts(nexus):
    """Seen on the real site: a wrong game domain gives an error next to the data of the others."""
    nexus.answers.append({'errors': [{'message': 'nosuchgame is not a valid game domain'}],
                          'data': {'legacyModsByDomain': {'nodes': [{**NODE, 'modRequirements': None}]}}})
    assert list(mod_infos([(GAME, 34705), ('nosuchgame', 1)])) == [(GAME, 34705)]


@pytest.mark.parametrize('answer', [{'errors': [{'message': 'no'}], 'data': None}, 'not json at all'])
def test_site_that_gives_nothing_is_a_nexus_error(nexus, monkeypatch, answer):
    if isinstance(answer, str):
        monkeypatch.setattr(graphql, 'urlopen', lambda *a, **k: SimpleNamespace(__enter__=None))
        def broken(*a, **k):
            raise OSError('no network')
        monkeypatch.setattr(graphql, 'urlopen', broken)
    else:
        nexus.answers.append(answer)
    with pytest.raises(NexusError):
        mod_infos([(GAME, 1)])


def test_files_of_a_mod(nexus):
    nexus.answers.append({'data': {'modFiles': [{'fileId': 833348, 'name': 'Example Mod  RU', 'version': '2.6', 'date': 1793985661,
                                                 'category': 'MAIN', 'sizeInBytes': '159245'}]}})
    assert mod_files(197622, 1704) == [{'file_id': 833348, 'name': 'Example Mod  RU', 'version': '2.6', 'date': 1793985661, 'category': 'MAIN', 'size': 159245}]
    assert nexus.sent[0]['body']['variables'] == {'modId': '197622', 'gameId': '1704'}


# ------------------------------------------------------------------ requirements
@pytest.mark.parametrize('note', ['Optional: Her Best Follower Framework Manager', 'Optional. Hire her at your estates as a skilled huntress.',
                                  'Only for VR users, very latest version!', 'Only for game versions older than 1.6.1130.',
                                  'Необязательно, для меню', 'только для VR'])
def test_notes_that_mean_not_for_everyone(note):
    assert looks_optional(note)


@pytest.mark.parametrize('note', ['', 'Her scripts use it.', 'For her MCM.', 'The textures for her outfit. Her fitted meshes are included.', 'Her eyes.'])
def test_notes_that_do_not(note):
    assert not looks_optional(note)


def info_with(*requirements, mod_id=1):
    return ModInfo(GAME, mod_id, name='Mod', requirements=list(requirements))


def need(mod_id, name='Need', note=''):
    return Requirement(name=name, note=note, mod_id=mod_id, game=GAME)


def test_each_requirement_gets_its_state():
    info = info_with(need(2, 'Listed'), need(3, 'Downloaded'), need(4, 'Installed'), need(5, 'Missing'), need(6, 'For VR', 'Only for VR users'),
                     Requirement(name='SKSE', url='https://skse.silverlock.org/', external=True), Requirement(name='Dawnguard', dlc=True))
    present = Present(listed={(GAME, 2)}, downloaded={(GAME, 3)}, installed={(GAME, 4): 'MO2'})
    needs = assess(info, present)
    states = {item.name: item.state for item in needs}
    assert states == {'Listed': LISTED, 'Downloaded': DOWNLOADED, 'Installed': INSTALLED, 'Missing': MISSING, 'For VR': MISSING, 'SKSE': ELSEWHERE, 'Dawnguard': GAME_DLC}
    assert needs[0].name == 'Missing' and needs[1].name == 'For VR', 'What is missing comes first, and what everyone needs before the rest'
    assert next(item for item in needs if item.name == 'Installed').where == 'MO2'
    assert next(item for item in needs if item.name == 'SKSE').page == 'https://skse.silverlock.org/'
    assert next(item for item in needs if item.name == 'Missing').page.endswith(f'/{GAME}/mods/5')


def test_requirement_of_a_game_nexus_did_not_name_cannot_be_added():
    item = assess(info_with(Requirement(name='Somewhere', mod_id=9, game='')), Present())[0]
    assert item.state == ELSEWHERE


def test_overview_names_a_mod_once_and_required_wins_over_optional():
    infos = [info_with(need(2, 'Address Library'), need(3, 'Tweaks', 'Optional'), mod_id=10),
             info_with(need(2, 'Address Library'), need(3, 'Tweaks'), Requirement(name='SKSE', external=True), mod_id=11),
             info_with(Requirement(name='SKSE', external=True), need(4, 'Extra', 'Only for VR'), mod_id=12)]
    found = overview(infos, Present())
    assert [item.name for item in found.required] == ['Address Library', 'Tweaks']
    assert [item.name for item in found.optional] == ['Extra']
    assert [item.name for item in found.elsewhere] == ['SKSE']


def test_requirement_becomes_an_entry_of_the_list():
    item = assess(info_with(need(5, 'Missing')), Present())[0]
    assert as_item(item) == {'url': f'https://www.nexusmods.com/{GAME}/mods/5', 'title': 'Missing'}


# ------------------------------------------------------------------ updates
NOW = datetime(2026, 11, 7, 12, 0)


def row(file_id, category='MAIN', date=1000, name='Main', version='1.0'):
    return {'file_id': file_id, 'category': category, 'date': date, 'name': name, 'version': version, 'size': 5}


def owned(*file_ids, when=500):
    return Owned(GAME, 1, 'Mod', file_ids, when)


def test_real_case_old_file_moved_to_old_versions():
    """As seen on the real site: 2.5 became OLD_VERSION and 2.6 is MAIN."""
    files = [row(830040, 'OLD_VERSION', 1793487716, 'Example Mod  RU', '2.5'), row(833348, 'MAIN', 1793985661, 'Example Mod  RU', '2.6')]
    assert newer_file(owned(830040), files)['file_id'] == 833348
    assert newer_file(owned(833348), files) is None
    assert newer_file(owned(830040, 833348), files) is None, 'Both are here: nothing to offer'


def test_variant_of_the_same_version_is_not_an_update():
    files = [row(1, date=1000, name='Variant A', version='2.0'), row(2, date=2000, name='Variant B', version='2.0')]
    assert newer_file(owned(1), files) is None


def test_main_file_with_a_new_version_or_the_same_name_is_an_update():
    assert newer_file(owned(1), [row(1, date=1000, name='Mod v1', version='1'), row(2, date=2000, name='Mod v2', version='2')])['file_id'] == 2
    assert newer_file(owned(1), [row(1, date=1000, name='Main', version='1'), row(2, date=2000, name='Main', version='1')])['file_id'] == 2


def test_of_several_main_files_the_one_with_the_same_name_is_offered():
    """Backported Extended ESL Support on the real site has two main files of one version."""
    files = [row(1, 'OLD_VERSION', 100, 'Backported ESL', '1.3'), row(2, 'MAIN', 200, 'Backported ESL', '1.4'), row(3, 'MAIN', 300, 'A small patch', '1.4')]
    assert newer_file(owned(1), files)['file_id'] == 2


def test_real_case_names_that_carry_the_version():
    """Found on the real site: the newest main file was a small add-on, not the next version."""
    files = [row(794054, 'OLD_VERSION', 100, 'BEES-1_3', '1.3'), row(795184, 'MAIN', 200, 'BEES-1_4', '1.4'),
             row(795185, 'MAIN', 201, 'BEES Papyrus Scripts-1_4', '1.4')]
    files[0]['size'], files[1]['size'], files[2]['size'] = 424000, 407000, 17068
    assert newer_file(owned(794054), files)['file_id'] == 795184


def test_older_main_file_is_not_an_update():
    assert newer_file(owned(2), [row(1, date=1000, version='2'), row(2, date=2000, version='1')]) is None


def test_optional_files_are_not_compared():
    assert newer_file(owned(5), [row(5, 'OPTIONAL', 1000), row(6, 'MAIN', 2000, version='9')]) is None


def test_mod_without_a_main_file_offers_nothing():
    assert newer_file(owned(1), [row(1, 'OLD_VERSION')]) is None


def test_file_gone_from_the_site_or_unknown_goes_by_date():
    assert newer_file(owned(99, when=500), [row(1, date=1000)])['file_id'] == 1
    assert newer_file(owned(99, when=5000), [row(1, date=1000)]) is None
    assert newer_file(owned(when=500), [row(1, date=1000)])['file_id'] == 1
    assert newer_file(owned(when=5000), [row(1, date=1000)]) is None


def session_with(tmp_path, moment, *files):
    collection = ModCollection.create(tmp_path, [{'game': GAME, 'mod_id': mod_id, 'title': f'Mod {mod_id}'} for mod_id, _ in files], clock=lambda: moment)
    for mod_id, file_id in files:
        path = collection.folder / f'{mod_id}-{file_id}.7z'
        path.write_bytes(b'7z')
        record_archive(path, GAME, mod_id, file_id)
    return collection


def test_owned_mods_gather_all_sessions(tmp_path):
    session_with(tmp_path, NOW - timedelta(days=9), (1, 10))
    session_with(tmp_path, NOW, (1, 11), (2, 20))
    found = owned_mods(list_sessions(tmp_path))
    assert set(found[(GAME, 1)].file_ids) == {10, 11} and found[(GAME, 2)].file_ids == (20,)
    assert found[(GAME, 1)].when == NOW.timestamp()
    assert found[(GAME, 1)].title == 'Mod 1'


def test_only_mods_changed_after_the_download_are_asked_about(tmp_path):
    session_with(tmp_path, NOW, (1, 10), (2, 20), (3, 30))
    infos = {(GAME, 1): ModInfo(GAME, 1, 'New', updated_at=NOW.timestamp() + 7 * 86400, game_number=1704),
             (GAME, 2): ModInfo(GAME, 2, 'Old', updated_at=NOW.timestamp() - 90 * 86400, game_number=1704)}
    asked = []

    def files_of(mod_id, game_number):
        asked.append((mod_id, game_number))
        return [row(10, 'OLD_VERSION', 1), row(11, 'MAIN', 2, version='2.0')]
    found = find_updates(list_sessions(tmp_path), lambda ids: infos, files_of)
    assert asked == [(1, 1704)], 'Unchanged mods and those Nexus does not show cost no request'
    assert [(update.mod_id, update.file_id, update.version, update.title) for update in found] == [(1, 11, '2.0', 'New')]


def test_nothing_downloaded_means_no_request(tmp_path):
    def must_not_run(*_):
        raise AssertionError('No mods, no questions')
    assert find_updates(list_sessions(tmp_path), must_not_run, must_not_run) == []


def test_found_updates_survive_a_restart(tmp_path):
    path = tmp_path / 'queue' / 'updates.json'
    save_found(path, [Update(GAME, 1, 'Mod', 11, '2.0', 'Main', 5)], now=1234.0)
    checked, found = load_found(path)
    assert checked == 1234.0 and found[0].key == (GAME, 1) and found[0].version == '2.0'


@pytest.mark.parametrize('content', [None, 'broken', '{"checked_at": 1}', '{"checked_at": "x", "found": []}', '{"checked_at": 1, "found": [{"bad": 1}]}'])
def test_damaged_list_of_updates_is_an_empty_one(tmp_path, content):
    path = tmp_path / 'updates.json'
    if content is not None:
        path.write_text(content, encoding='utf-8')
    assert load_found(path) == (0.0, [])


# ------------------------------------------------------------------ room on the disk
def test_enough_room_says_nothing(tmp_path):
    assert disk_space.shortages([tmp_path], 1000, free_of=lambda _: 10 ** 12) == []


def test_too_little_room_counts_the_reserve(tmp_path):
    needed = 1000
    assert disk_space.shortages([tmp_path], needed, free_of=lambda _: disk_space.RESERVE + needed) == []
    assert disk_space.shortages([tmp_path], needed, free_of=lambda _: disk_space.RESERVE + needed - 1) == [(tmp_path, disk_space.RESERVE + needed - 1)]


def test_one_disk_is_told_once_and_empty_entries_are_ignored(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    assert len(disk_space.shortages([first, '', None, second], 0, free_of=lambda _: 0)) == 1


def test_folder_that_does_not_exist_yet_is_measured_by_what_exists(tmp_path):
    assert disk_space.free_bytes(tmp_path / 'not' / 'yet') > 0


def test_disk_that_cannot_be_read_stays_silent(tmp_path):
    def broken(_):
        raise OSError('no disk')
    assert disk_space.shortages([tmp_path], 0, free_of=broken) == []


# ------------------------------------------------------------------ deleting
def discard_for_test(path):
    Path(path).unlink() if Path(path).is_file() else __import__('shutil').rmtree(path)


def test_removing_a_file_leaves_the_rest_of_its_session(tmp_path):
    folder = session_with(tmp_path, NOW, (1, 10), (2, 20)).folder
    remove_archive(folder / '1-10.7z', discard_for_test)
    session = list_sessions(tmp_path)[0]
    assert [archive.mod_id for archive in session.archives] == [2] and session.archives[0].title == 'Mod 2'


def test_removing_the_last_file_removes_the_session(tmp_path):
    folder = session_with(tmp_path, NOW, (1, 10)).folder
    remove_archive(folder / '1-10.7z', discard_for_test)
    assert not folder.exists() and list_sessions(tmp_path) == []


def test_removing_a_session_takes_everything_of_it(tmp_path):
    folder = session_with(tmp_path, NOW, (1, 10), (2, 20)).folder
    remove_session(folder, discard_for_test)
    assert not folder.exists()


def test_refused_removal_changes_nothing(tmp_path):
    folder = session_with(tmp_path, NOW, (1, 10)).folder

    def refuses(_):
        raise OSError('in use')
    with pytest.raises(OSError):
        remove_archive(folder / '1-10.7z', refuses)
    assert list_sessions(tmp_path)[0].archives[0].mod_id == 1


@pytest.mark.skipif(os.name != 'nt', reason='The Recycle Bin of Windows')
def test_recycle_bin_reports_a_failure(tmp_path):
    with pytest.raises(OSError):
        recycle.to_recycle_bin(tmp_path / 'absent' / 'file.txt')


# ------------------------------------------------------------------ what MO2 and Vortex have
@pytest.mark.parametrize('name, mod_id', [('SkyUI-12604-6-11-1778020881.zip', 12604), ('All in one (all game versions)-32444-11-1770897704.zip', 32444),
                                          ('Skyrim Script Extender (SKSE64)-30379-2-2-6-1705522967.7z', 30379),
                                          ('SSE Display Tweaks 34705 0.5.25 2026-08-29T20-20Z QRde31FV2.zip', 34705),
                                          ('Some-Wrapper.3.1.zip', 0), ('readme.txt', 0)])
def test_mod_number_in_the_name_of_a_file(name, mod_id):
    assert mod_id_from_name(name) == mod_id


def test_vortex_folders_tell_their_game(tmp_path):
    (tmp_path / 'skyrimse').mkdir()
    (tmp_path / 'fallout4').mkdir()
    (tmp_path / 'skyrimse' / 'SkyUI-12604-6-11-1778020881.zip').write_bytes(b'')
    (tmp_path / 'fallout4' / 'Some Mod-183-4-2-5-1749477203.zip').write_bytes(b'')
    (tmp_path / '__vortex_downloads_folder').write_text('x')
    assert vortex_mods(tmp_path) == {(GAME, 12604): 'Vortex', ('fallout4', 183): 'Vortex'}


def test_missing_vortex_folder_is_nothing(tmp_path):
    assert vortex_mods(tmp_path / 'absent') == {}


def test_mo2_mods_are_read_from_their_notes(tmp_path):
    ini = tmp_path / 'ModOrganizer.ini'
    ini.write_text('[General]\ngameName=Skyrim Special Edition\nselected_profile=@ByteArray(Default)\n', encoding='utf-8')
    for name, content in (('With id', '[General]\nmodid=22603\n'), ('No id', '[General]\nmodid=0\n'), ('Broken', 'nonsense = \n[')):
        (tmp_path / 'mods' / name).mkdir(parents=True)
        (tmp_path / 'mods' / name / 'meta.ini').write_text(content, encoding='utf-8')
    assert mo2_mods(ini) == {(GAME, 22603): 'MO2'}


def test_mo2_that_is_not_there_is_nothing(tmp_path):
    assert mo2_mods('') == {} and mo2_mods(tmp_path / 'absent.ini') == {}


# ------------------------------------------------------------------ messages
def test_text_of_a_message_goes_through_the_environment(monkeypatch):
    """The name of a mod must never become a part of a script."""
    seen = {}

    def fake_run(arguments, **options):
        seen.update(arguments=arguments, env=options['env'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(notify_module.subprocess, 'run', fake_run)
    monkeypatch.setattr(notify_module.os, 'name', 'nt')
    evil = '"; Remove-Item C:\\ -Recurse; "'
    assert notify_module.show_toast('Title', evil)
    assert evil not in ' '.join(seen['arguments'])
    assert seen['env']['NEXUS_NOTIFY_TEXT'] == evil


def test_message_that_fails_changes_nothing(monkeypatch):
    def broken(*a, **k):
        raise OSError('no PowerShell here')
    monkeypatch.setattr(notify_module.subprocess, 'run', broken)
    monkeypatch.setattr(notify_module.os, 'name', 'nt')
    assert notify_module.show_toast('Title', 'Text') is False
