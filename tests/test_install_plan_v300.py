"""Selection, per-mod destinations and safe installation tests."""
import zipfile
import pytest
from nexus_tab_installer.core.install_plan import ModPlan, validate_target
from nexus_tab_installer.integrations.delivery import deliver
from nexus_tab_installer.integrations.managed_install import uninstall


def mod(i):
    return {'url': f'https://www.nexusmods.com/skyrimspecialedition/mods/{i}',
            'title': f'Mod {i}'}


def archive_zip(tmp_path, name, content):
    archive = tmp_path / name
    with zipfile.ZipFile(archive, 'w') as z:
        for key, value in content.items():
            z.writestr(key, value)
    return archive


def test_merge_all_windows_and_pick_subset():
    plan = ModPlan()
    plan.merge([mod(11), mod(12)], [mod(12), mod(13)], [mod(14)])
    assert [x['mod_id'] for x in plan.items] == [11, 12, 13, 14]
    plan.update(plan.items[1], selected=False)
    plan.update(plan.items[2], target='mo2')
    assert [(i['mod_id'], target) for i, target in plan.chosen()] == [(11,'library'),(13,'mo2'),(14,'library')]
    plan.merge([mod(14)], [mod(13)])
    assert [i['mod_id'] for i, _ in plan.chosen()] == [14,13]
    assert plan.choices[('skyrimspecialedition',13)].target == 'mo2'


def test_bad_target_and_invalid_game_data_folder(tmp_path):
    plan=ModPlan(); plan.replace([mod(50)])
    with pytest.raises(ValueError): plan.update(plan.items[0], target='anything')
    with pytest.raises(ValueError): validate_target('game', game_folder=str(tmp_path))
    game=tmp_path/'Data'; game.mkdir()
    validate_target('game', game_folder=str(game))


def test_library_does_not_touch_external_paths(tmp_path):
    source=archive_zip(tmp_path,'mod.zip',{'textures/a.dds':b'abc'})
    st,detail=deliver(source,{'game':'skyrimspecialedition','mod_id':1},'library',
                      staging_root=tmp_path/'stage',journal=tmp_path/'managed')
    assert st=='Готово' and not (tmp_path/'managed').exists()


def test_vortex_copies_archive_not_install(tmp_path):
    source=archive_zip(tmp_path,'mod.zip',{'textures/a.dds':b'abc'})
    dest=tmp_path/'Vortex'; dest.mkdir()
    stage,detail=deliver(source,{'game':'skyrimspecialedition','mod_id':1},'vortex',
                         staging_root=tmp_path/'stage',journal=tmp_path/'managed',vortex_folder=str(dest))
    assert stage=='Передано Vortex'
    assert (dest/'mod.zip').read_bytes()==source.read_bytes()
    assert 'включение' in detail


def test_game_simple_install_and_reversible_restore(tmp_path):
    game=tmp_path/'Game'/'Data';game.mkdir(parents=True)
    original=game/'textures'/'floor.dds';original.parent.mkdir()
    original.write_bytes(b'old')
    source=archive_zip(tmp_path,'mod.zip',{'textures/floor.dds':b'new','meshes/one.nif':b'test'})
    result,detail=deliver(source,{'game':'skyrimspecialedition','mod_id':1},'game',
                          staging_root=tmp_path/'staging',journal=tmp_path/'managed',game_folder=str(game))
    assert result=='Установлен в игру' and original.read_bytes()==b'new'
    manifest=next((tmp_path/'managed').glob('*/manifest.json'))
    uninstall(tmp_path/'managed',manifest.parent.name)
    assert original.read_bytes()==b'old'
    assert not (game/'meshes'/'one.nif').exists()


def test_game_rejects_complex_installer_without_changes(tmp_path):
    game=tmp_path/'Data';game.mkdir()
    source=archive_zip(tmp_path,'mod.zip',{'fomod/ModuleConfig.xml':'<config/>','meshes/file.nif':'data'})
    with pytest.raises(ValueError,match='FOMOD'):
        deliver(source,{'game':'skyrimspecialedition','mod_id':1},'game',
                staging_root=tmp_path/'stage',journal=tmp_path/'managed',game_folder=str(game))
    assert list(game.iterdir())==[]


def test_install_simple_mo2_and_activate_profile(tmp_path, monkeypatch):
    # A person may have MO2 open while the tests run: it is not the one being tested here.
    from nexus_tab_installer.integrations import delivery, mo2_install
    monkeypatch.setattr(mo2_install, 'mo2_is_running', lambda: False)
    monkeypatch.setattr(delivery, 'mo2_is_running', lambda: False)
    mo=tmp_path/'MO2'; (mo/'mods').mkdir(parents=True); (mo/'profiles'/'Default').mkdir(parents=True)
    (mo/'profiles'/'Default'/'modlist.txt').write_text('# mods\n',encoding='utf-8')
    ini=mo/'ModOrganizer.ini';ini.write_text('[Settings]\nbase_directory=%BASE_DIR%\n',encoding='utf-8')
    archive=archive_zip(tmp_path,'mod.zip',{'textures/floor.dds':'data'})
    stage, detail=deliver(archive,{'game':'skyrimspecialedition','mod_id':9},'mo2',
                          staging_root=tmp_path/'stage',journal=tmp_path/'managed',
                          mo2_ini=str(ini),mo2_profile='Default')
    assert stage=='Установлен MO2' and 'Default' in detail
    assert any((x/'textures'/'floor.dds').exists() for x in (mo/'mods').iterdir())
    assert '+mod' in (mo/'profiles'/'Default'/'modlist.txt').read_text(encoding='utf-8')

def test_direct_install_rejects_unrecognized_wrapper_folder(tmp_path):
    game=tmp_path/'Data'; game.mkdir()
    archive=archive_zip(tmp_path,'badlayout.zip',{'SomeModName/Readme.txt':'hello'})
    with pytest.raises(ValueError, match='Структура файлов'):
        deliver(archive,{'game':'skyrimspecialedition','mod_id':4},'game',
                staging_root=tmp_path/'stage',journal=tmp_path/'managed',game_folder=str(game))
    assert not list(game.iterdir())
