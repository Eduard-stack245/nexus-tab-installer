import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.integrations.destinations import find_mo2_instances


class DiscoveryTest(unittest.TestCase):
    def test_portable_and_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / 'ModOrganizer.ini'
            direct.write_text('[Settings]\n', encoding='utf-8')
            child = root / 'Skyrim' / 'ModOrganizer.ini'
            child.parent.mkdir()
            child.write_text('[Settings]\n', encoding='utf-8')
            self.assertEqual(find_mo2_instances([root]), sorted([direct.resolve(), child.resolve()]))

    def test_no_recursive_scanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'a' / 'b'
            nested.mkdir(parents=True)
            (nested / 'ModOrganizer.ini').write_text('')
            self.assertEqual(find_mo2_instances([tmp]), [])

    def test_direct_ini_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / 'ModOrganizer.ini'
            ini.write_text('')
            self.assertEqual(find_mo2_instances([ini, tmp, tmp]), [ini.resolve()])


if __name__ == '__main__':
    unittest.main()
