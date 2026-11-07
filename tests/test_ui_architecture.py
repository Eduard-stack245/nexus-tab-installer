"""Static architecture regression checks that do not require the GUI dependency."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'nexus_tab_installer' / 'ui'


class ScreenArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.views = (ROOT / 'views.py').read_text(encoding='utf-8')
        cls.app = (ROOT / 'flet_app.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.views)

    def test_each_screen_is_separate_function(self):
        names = {node.name for node in self.tree.body if isinstance(node, ast.FunctionDef)}
        self.assertLessEqual({'mods_view', 'downloads_view', 'settings_view'}, names)

    def test_views_do_not_perform_network_or_file_io(self):
        self.assertNotIn('NexusClient', self.views)
        self.assertNotIn('open(', self.views)
        self.assertNotIn('write_text(', self.views)
        self.assertNotIn('Thread(', self.views)

    def test_application_imports_screen_factories(self):
        self.assertIn('from nexus_tab_installer.ui.views import (', self.app)
        for name in ('downloads_view(', 'settings_view('):
            self.assertIn(name, self.app)

    def test_library_uses_resolved_destination(self):
        self.assertIn("list_sessions(PATHS['sessions'])", self.app)
        self.assertIn("PATHS['staging']", self.app)

    def test_removed_legacy_ui(self):
        self.assertFalse((ROOT / 'legacy_tk.py').exists())


if __name__ == '__main__':
    unittest.main()
