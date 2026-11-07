import tempfile
import unittest
import zipfile
from pathlib import Path
from nexus_tab_installer.downloads.archives import inspect_archive, stage_archive
from nexus_tab_installer.downloads.transfer import DownloadError

class ArchiveIntegrationTests(unittest.TestCase):
    def test_zip_stages_in_isolated_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            z=root/'mod.zip'
            with zipfile.ZipFile(z,'w') as out:
                out.writestr('textures/test.dds',b'abc')
            meta=inspect_archive(z)
            self.assertEqual(meta['files'],1)
            dest, _=stage_archive(z,root/'staging')
            self.assertEqual((dest/'textures/test.dds').read_bytes(),b'abc')
            self.assertFalse((root/'textures').exists())
            with self.assertRaises(DownloadError):
                stage_archive(z,root/'staging')
    def test_rejects_windows_ads(self):
        with tempfile.TemporaryDirectory() as tmp:
            z=Path(tmp)/'mod.zip'
            with zipfile.ZipFile(z,'w') as out: out.writestr('file.txt:stream',b'abc')
            with self.assertRaises(DownloadError):inspect_archive(z)
    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            z=Path(tmp)/'mod.zip'
            with zipfile.ZipFile(z,'w') as out: out.writestr('../escape.txt',b'abc')
            with self.assertRaises(DownloadError):stage_archive(z,Path(tmp)/'staging')
            self.assertFalse((Path(tmp)/'escape.txt').exists())
    def test_rejects_duplicates_ignoring_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            z=Path(tmp)/'mod.zip'
            with zipfile.ZipFile(z,'w') as out:
                out.writestr('A.txt',b'abc')
                out.writestr('a.TXT',b'def')
            with self.assertRaises(DownloadError):inspect_archive(z)
    def test_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'archive.exe';p.write_bytes(b'no')
            with self.assertRaises(DownloadError):inspect_archive(p)
if __name__=='__main__':unittest.main()
