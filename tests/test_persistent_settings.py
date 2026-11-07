import json
from pathlib import Path
from nexus_tab_installer.core.settings import save_settings, load_settings


def test_persist_api_when_opted_in(tmp_path):
    path = tmp_path / 'config' / 'config.json'
    save_settings(path, {'folder': 'folder', 'destination_mode': 'mo2', 'mo2_ini': 'MO2.ini', 'pack_path': 'data/packs/a.json', 'remember_api_key': True, 'api_key': 'secret'})
    assert load_settings(path)['api_key'] == 'secret'
    assert load_settings(path)['pack_path'] == 'data/packs/a.json'
    assert 'secret' in path.read_text()


def test_never_save_key_without_consent(tmp_path):
    path = tmp_path / 'config.json'
    save_settings(path, {'api_key': 'secret', 'remember_api_key': False, 'folder': 'test'})
    assert 'api_key' not in load_settings(path)
    save_settings(path, {'api_key': 'secret', 'remember_api_key': True})
    save_settings(path, {'remember_api_key': False})
    assert 'secret' not in path.read_text()


def test_corrupted_or_extra_settings(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{bad json')
    assert load_settings(path) == {}
    save_settings(path, {'folder':'a', 'unknown':'unwanted'})
    assert 'unknown' not in json.loads(path.read_text())
