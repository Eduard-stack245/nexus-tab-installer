"""Regression tests for grey screen from unbounded Flutter constraints."""
from pathlib import Path
import ast

VIEWS = Path(__file__).resolve().parents[1] / "src/nexus_tab_installer/ui/views.py"

def test_wrapping_rows_do_not_have_expanded_children():
    tree = ast.parse(VIEWS.read_text(encoding="utf-8"))
    for row in ast.walk(tree):
        if not isinstance(row, ast.Call):
            continue
        if not isinstance(row.func, ast.Attribute) or row.func.attr != "Row":
            continue
        wrapping = any(k.arg == "wrap" and isinstance(k.value, ast.Constant)
                       and k.value.value is True for k in row.keywords)
        if not wrapping:
            continue
        for child in ast.walk(row):
            if child is row or not isinstance(child, ast.Call):
                continue
            assert not any(k.arg == "expand" and isinstance(k.value, ast.Constant)
                           and k.value.value is True for k in child.keywords), (
                f"expanding child under wrapping Row on line {row.lineno}"
            )

def test_mod_list_action_outside_wrapping_row():
    source = VIEWS.read_text(encoding="utf-8")
    assert "Последние версии · скачать выбранные" not in source
    assert "ft.Container(expand=True),\n                    ft.Button('Последние версии" not in source
    assert "list_area," in source
