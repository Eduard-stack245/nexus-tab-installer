"""Flet 0.80+ Dropdown accepts on_select, not on_change."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'src/nexus_tab_installer/ui/flet_app.py'


def test_dropdown_events_use_selection_api():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    dropdowns = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == 'Dropdown'
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 'ft']
    assert len(dropdowns) >= 4, 'Review all dropdowns when adding new screens'
    for call in dropdowns:
        keywords = {kw.arg for kw in call.keywords}
        assert 'on_change' not in keywords, f'Dropdown at line {call.lineno} uses removed on_change'


def test_browser_guide_selection_updates_instructions():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    guide = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'browser_guide_browser'
                         for target in node.targets))
    params = {kw.arg: kw.value for kw in guide.value.keywords}
    assert 'on_select' in params
    assert isinstance(params['on_select'], ast.Name)
    assert params['on_select'].id == 'render_browser_steps'
