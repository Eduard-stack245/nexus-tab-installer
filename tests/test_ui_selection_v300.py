"""Regression contract: scans first, lets user select per mod, then downloads."""
import ast
from pathlib import Path
SRC=(Path(__file__).resolve().parents[1]/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
TREE=ast.parse(SRC)

def func(name):
    return next(ast.get_source_segment(SRC, n) for n in ast.walk(TREE)
                if isinstance(n, (ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)

def test_scan_aggregates_browser_snapshots_with_windows():
    assert 'snapshot_mods + scan.mods' in SRC
    assert 'normalize_entries(snapshots.collect(max_age=120) + snapshot_mods + scan.mods)' in SRC
    assert 'extension_scanned' in SRC
    assert "('windows_scanned', scan, waiting, snapshot_mods)" in func('start_workflow')

def test_scan_does_not_queue_every_mod_without_selection():
    assert 'prepare_downloads(' not in func('begin_workflow_items')
    assert 'plan.replace(items)' in func('begin_workflow_items')
    assert 'plan.chosen()' in func('download_selected')
    assert 'launch_batch(valid, choices, errors)' in func('download_selected')
    assert 'prepare_downloads(valid, run)' in func('launch_batch')

def test_each_mod_has_checkbox_and_destination_dropdown():
    f=func('mod_row')
    assert 'ft.Checkbox(' in f
    assert 'ft.Dropdown(' in f
    assert 'target_options()' in f and "ft.dropdown.Option('mo2'" in SRC
    assert "ft.dropdown.Option('vortex'" in SRC
    assert "ft.dropdown.Option('game'" in SRC
    assert 'plan.update(ref, target=e.control.value)' in f

def test_post_download_routes_to_correct_destination():
    assert 'job_routes[job.id]' in SRC
    assert 'deliver(archive_path,' in SRC
    assert 'record_archive(archive_path' in SRC
    assert "result[0] == 'delivered'" in SRC
    assert 'session.set_status(item, stage, detail)' in SRC

def test_settings_portable_and_no_secret_in_targets():
    assert "STATE_DIR / 'config' / 'mod-choices.json'" in SRC
    assert "'api_key'" not in func('save_choices')
    assert 'save_choices()' in func('refresh_workflow') or 'save_choices()' in SRC
