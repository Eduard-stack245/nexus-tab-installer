import unittest
import tempfile
from pathlib import Path
from nexus_tab_installer.downloads.importer import import_archive, ImportArchiveError

class ArchiveTests(unittest.TestCase):
    def test_copy_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'demo.7z';src.write_bytes(b'example')
            lib=Path(tmp)/'library'
            a=import_archive(src,lib)
            self.assertEqual(a.read_bytes(),b'example')
            self.assertEqual(import_archive(src,lib),a)
    def test_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'demo.rar';src.write_bytes(b'first')
            lib=Path(tmp)/'library';import_archive(src,lib)
            src.write_bytes(b'other')
            self.assertIn('(1)',str(import_archive(src,lib)))
    def test_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'script.exe';src.write_bytes(b'bad')
            with self.assertRaises(ImportArchiveError):import_archive(src,Path(tmp)/'library')
