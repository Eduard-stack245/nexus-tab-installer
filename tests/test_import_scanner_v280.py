"""Regression tests for reported import and scan problems."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'nexus_tab_installer'
APP = (ROOT / 'ui' / 'flet_app.py').read_text(encoding='utf-8')
SCAN = (ROOT / 'browser' / 'windows_tabs.py').read_text(encoding='utf-8')

def code_for(name):
    tree = ast.parse(APP)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(APP, node)
    raise AssertionError(name)

def test_import_replaces_old_list_and_updates_main_preview():
    code = code_for('add')
    assert 'entries[:] = plan.replace(items)' in code
    assert 'normalize_entries(entries + items)' not in code
    assert "workflow['current'] = WorkflowSession(entries)" in code
    assert 'refresh_workflow()' in code
    assert 'row_memory.clear()' in code

def test_file_import_uses_same_replacement_path():
    code = code_for('pack_load')
    assert 'add(load_pack(' in code
    assert 'entries.extend(' not in code

def test_scan_displays_results_before_api_key_check():
    code = code_for('begin_workflow_items')
    assert 'entries[:] = plan.replace(items)' in code
    assert 'refresh_workflow()' in code
    assert 'pick_latest(' not in code

def test_scan_never_iterates_unknown_background_tabs():
    assert 'tab is selected or _candidate(tab)' in SCAN
    assert "_candidate(tab)" in SCAN
    assert "for tab in candidates[:MAX_CANDIDATES_PER_WINDOW]" in SCAN
    assert "for tab in tabs:" not in SCAN

def test_new_scan_preserves_previous_pending_free_requests():
    code = code_for('start_workflow')
    assert 'free_queue.clear()' not in code
    assert 'snapshot_mods = snapshots.collect(max_age=120)' in code
    assert "'windows_scanned', scan, waiting, snapshot_mods" in code
