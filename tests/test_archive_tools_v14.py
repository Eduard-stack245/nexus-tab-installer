import tempfile
import unittest
import zipfile
from pathlib import Path
from nexus_tab_installer.downloads.archives import inspect_zip, stage_zip
from nexus_tab_installer.downloads.transfer import DownloadError

class ArchiveToolTests(unittest.TestCase):
    def test_good_zip_and_stage(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            z = root / 'good.zip'
            with zipfile.ZipFile(z, 'w') as out:
                out.writestr('textures/abc.txt', 'hello')
            self.assertEqual(inspect_zip(z)['files'], 1)
            target, _ = stage_zip(z, root / 'staging')
            self.assertEqual((target/'textures'/'abc.txt').read_text(), 'hello')

    def test_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            z = Path(root)/'evil.zip'
            with zipfile.ZipFile(z, 'w') as out:
                out.writestr('../evil.txt', 'x')
            with self.assertRaises(DownloadError): inspect_zip(z)

    def test_duplicate_case(self):
        with tempfile.TemporaryDirectory() as root:
            z = Path(root)/'dup.zip'
            with zipfile.ZipFile(z, 'w') as out:
                out.writestr('A.txt', 'a')
                out.writestr('a.TXT', 'b')
            with self.assertRaises(DownloadError): inspect_zip(z)

    def test_bad_zip(self):
        with tempfile.TemporaryDirectory() as root:
            z = Path(root)/'not.zip'
            z.write_bytes(b'not a zip')
            with self.assertRaises(DownloadError): inspect_zip(z)
