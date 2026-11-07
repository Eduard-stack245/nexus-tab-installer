"""The program folder can be moved: nothing in the settings may point to its old place."""
from pathlib import Path

from nexus_tab_installer.core.paths import application_root, data_root, portable_path, saved_path
from nexus_tab_installer.core.settings import load_settings
from nexus_tab_installer.core.paths import storage_paths
from test_ui_end_to_end_v400 import launch  # noqa: F401 (fixture)


def test_paths_inside_the_program_are_saved_relative(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path))
    assert portable_path(str(tmp_path / 'data' / 'packs' / 'my.json')) == 'data/packs/my.json'


def test_paths_outside_the_program_stay_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path / 'program'))
    outside = str(tmp_path / 'Vortex' / 'downloads')
    assert portable_path(outside) == outside
    assert saved_path(outside) == outside


def test_relative_path_follows_the_folder(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path / 'moved here'))
    assert Path(saved_path('data/packs/my.json')) == application_root() / 'data' / 'packs' / 'my.json'


def test_absolute_path_from_an_older_version_is_brought_to_the_new_place(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path))
    old = r'E:\OldPlace\NexusTabInstaller\data\packs\my-mods.json'
    assert Path(saved_path(old)) == data_root() / 'packs' / 'my-mods.json'


def test_a_foreign_folder_called_data_is_not_touched(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_TAB_HOME', str(tmp_path))
    foreign = r'D:\games\data\mods'
    assert saved_path(foreign) == foreign


def test_empty_values_stay_empty():
    assert portable_path('') == '' and saved_path('') == ''
    assert portable_path(None) is None and saved_path(None) is None


def test_settings_of_the_program_are_saved_relative(launch):
    h = launch()

    async def scenario():
        h.paste_links('https://www.nexusmods.com/skyrim/mods/7')
        h.click('Скачать выбранные')
        await h.next_job()

    h.run(scenario)
    saved = load_settings(storage_paths()['config'])
    assert saved['pack_path'] == 'data/packs/my-mods.json'
    assert not Path(saved['folder']).is_absolute()


def test_old_absolute_settings_work_after_a_move(launch):
    h = launch(config={'pack_path': r'Z:\old place\data\packs\my-mods.json', 'browser_paired': True})

    async def scenario():
        h.paste_links('https://www.nexusmods.com/skyrim/mods/7')
        h.click('Скачать выбранные')
        await h.next_job()

    h.run(scenario)
    assert load_settings(storage_paths()['config'])['pack_path'] == 'data/packs/my-mods.json'
