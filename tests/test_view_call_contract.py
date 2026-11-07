"""Catch missing keyword arguments in Flet view factories without importing Flet."""
import ast
import inspect
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "src" / "nexus_tab_installer" / "ui"

class ViewCallContractTests(unittest.TestCase):
    def test_view_calls_supply_all_required_keywords(self):
        view_tree = ast.parse((ROOT / "views.py").read_text(encoding="utf-8"))
        app_tree = ast.parse((ROOT / "flet_app.py").read_text(encoding="utf-8"))
        factories = {}
        for node in ast.walk(view_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.endswith("_view"):
                args = node.args.kwonlyargs
                defaults = node.args.kw_defaults
                factories[node.name] = {arg.arg for arg, default in zip(args, defaults) if default is None}
        self.assertIn("settings_view", factories)
        visited = set()
        for node in ast.walk(app_tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in factories:
                name = node.func.id
                given = {k.arg for k in node.keywords if k.arg is not None}
                missing = factories[name] - given
                self.assertFalse(missing, f"{name} at line {node.lineno} missing: {sorted(missing)}")
                visited.add(name)
        self.assertTrue(visited, "No view factory calls found")
        self.assertIn("settings_view", visited)

if __name__ == "__main__":
    unittest.main()
