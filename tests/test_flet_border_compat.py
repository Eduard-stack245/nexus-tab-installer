"""Regression guard for Flet 0.70+ border API."""
from pathlib import Path
import unittest


class TestFletBorderCompat(unittest.TestCase):
    def test_no_removed_border_all_calls(self):
        ui = Path(__file__).resolve().parents[1] / 'src' / 'nexus_tab_installer' / 'ui'
        for path in ui.rglob('*.py'):
            with self.subTest(path=path.name):
                self.assertNotIn('ft.border.all(', path.read_text(encoding='utf-8'))

    def test_new_border_api_present(self):
        ui = Path(__file__).resolve().parents[1] / 'src' / 'nexus_tab_installer' / 'ui'
        self.assertIn('ft.Border.all(', (ui / 'views.py').read_text(encoding='utf-8'))
        self.assertIn('ft.Border.all(', (ui / 'flet_app.py').read_text(encoding='utf-8'))
