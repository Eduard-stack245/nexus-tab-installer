import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.integrations.destinations import mo2_downloads, resolve_destination, DestinationError

class DestinationsTests(unittest.TestCase):
    def test_portable_default(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); (p/'ModOrganizer.ini').write_text('[Settings]\n', encoding='utf8')
            self.assertEqual(mo2_downloads(p/'ModOrganizer.ini'), p/'downloads')
    def test_base_dir_override(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); base=p/'instance'; (base/'archives').mkdir(parents=True)
            (p/'ModOrganizer.ini').write_text(f'[Settings]\nbase_directory={base}\ndownload_directory=%BASE_DIR%/archives\n')
            self.assertEqual(resolve_destination('mo2', mo2_ini=str(p/'ModOrganizer.ini')),base/'archives')
    def test_missing_ini(self):
        with self.assertRaises(DestinationError): mo2_downloads('missing.ini')
    def test_nonexistent_target(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(DestinationError): resolve_destination('folder',str(Path(t)/'missing'))
    def test_explicit_folder(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(resolve_destination('folder', t),Path(t).resolve())
    def test_reject_unknown_mode(self):
        with self.assertRaises(DestinationError): resolve_destination('other','/tmp')
if __name__=='__main__': unittest.main()
