import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nexus_tab_installer.core.paths import application_root, ensure_layout, storage_paths


class PortablePathsTests(unittest.TestCase):
    def test_relocatable_layout(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            for root in (one, two):
                with patch.dict(os.environ, {"NEXUS_TAB_HOME": root}):
                    self.assertEqual(application_root(), Path(root).resolve())
                    base = ensure_layout()
                    paths = storage_paths()
                    self.assertEqual(base, Path(root).resolve() / "data")
                    for name, entry in paths.items():
                        self.assertTrue(entry.is_relative_to(base), name)
                    self.assertTrue(paths["downloads"].is_dir())
                    self.assertTrue(paths["nxm"].is_dir())
                    self.assertTrue(paths["config"].parent.is_dir())

    def test_no_user_home_state(self):
        source = (Path(__file__).resolve().parents[1] / "src/nexus_tab_installer/ui/flet_app.py").read_text(encoding="utf8")
        self.assertNotIn("Path.home() / '.nexus-tab-installer'", source)
        self.assertIn("ensure_layout()", source)

if __name__ == "__main__":
    unittest.main()
