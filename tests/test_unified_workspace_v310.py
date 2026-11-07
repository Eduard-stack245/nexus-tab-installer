"""Guard the real reported regressions without requiring a GPU/Flet on CI."""
import ast
from pathlib import Path
from nexus_tab_installer.core.settings import load_settings, save_settings

ROOT=Path(__file__).resolve().parents[1]
APP=(ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
VIEWS=(ROOT/'src/nexus_tab_installer/ui/views.py').read_text(encoding='utf-8')


def block(name):
    tree=ast.parse(APP)
    node=next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name==name)
    return ast.get_source_segment(APP,node)


def test_full_portable_settings_survive_relaunch(tmp_path):
    config=tmp_path/'data/config/config.json'
    previous={
        'game_folder':'D:/Games/Skyrim/Data',
        'vortex_folder':'D:/Vortex/Downloads',
        'mo2_profile':'Default',
        'mo2_ini':'D:/ModOrganizer/ModOrganizer.ini',
        'pack_path':'D:/Mods/my-pack.json',
        'folder':'D:/Downloads',
        'destination_mode':'mo2',
        'remember_api_key':True,
        'api_key':'private-key',
        'browser_token':'pairing-token',
    }
    save_settings(config,previous)
    assert load_settings(config)==previous
    save_settings(config,{**previous,'remember_api_key':False})
    after=load_settings(config)
    assert 'api_key' not in after
    assert all(after[k]==v for k,v in previous.items() if k!='api_key' and k!='remember_api_key')


def test_no_duplicate_mods_navigation():
    assert "('mods','Мои моды'" not in APP
    assert "('start','Мои моды'" in APP
    assert "'mods':make_mods" not in APP
    start=block('make_start')
    for phrase in ('workflow_rows','link_field','add_text','pack_load','api_notice',
                   'start_button','download_selected_button','cancel_button',
                   'select_all_button','clear_all_button'):
        assert phrase in APP
    assert 'workflow_rows' in start
    assert 'import_options' in start
    assert 'link_field' in APP.split('import_options =', 1)[1]


def test_missing_key_and_help_are_actionable():
    assert 'api_notice.visible' in APP
    assert 'Открыть Nexus Mods → API Keys' in VIEWS
    assert 'https://www.nexusmods.com/settings/api-keys' in APP
    assert 'def toggle_help(' in APP
    assert 'open_key_settings' in APP
    assert 'ready_to_download()' in block('download_selected')
    assert 'key.value' in block('ready_to_download')


def test_settings_saved_automatically_when_leaving_screen():
    assert 'field.on_blur = lambda e: persist_config(silent=True)' in APP
    assert 'ctrl.on_change = lambda e: persist_config(silent=True)' in APP
    assert 'persist_config(silent=True)' in block('change_tab')


def test_browser_permissions_and_coverage_not_falsely_claimed():
    assert 'browser_setup_panel' in block('make_start')
    assert 'pairing_banner_content.controls.append(request_actions(request))' in APP
    assert 'pairing_banner,' in APP.split('frame = ft.Row([', 1)[1]
    assert 'часть фоновых вкладок может быть недоступна' in APP
    assert 'Для полного охвата подключите расширение браузера.' in APP
