import unittest
import tempfile
import zipfile
from pathlib import Path
from nexus_tab_installer.downloads.transfer import extract_zip_to_staging, DownloadError, safe_name
from nexus_tab_installer.nexus.api import nxm_parts, NexusError

class TestDownloads(unittest.TestCase):
    def test_safe_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'mod.zip'
            with zipfile.ZipFile(a,'w') as z:z.writestr('textures/item.txt','ok')
            out=extract_zip_to_staging(a,Path(tmp)/'stage','skyrimspecialedition',123)
            self.assertEqual((out/'textures/item.txt').read_text(),'ok')
    def test_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'bad.zip'
            with zipfile.ZipFile(a,'w') as z:z.writestr('../outside.txt','bad')
            with self.assertRaises(DownloadError):extract_zip_to_staging(a,Path(tmp)/'stage','game',123)
    def test_nxm(self):
        self.assertEqual(nxm_parts('nxm://skyrimspecialedition/mods/123/files/45?key=hello&expires=987&user_id=123')[:3],('skyrimspecialedition',123,45))
        with self.assertRaises(NexusError):nxm_parts('https://evil.example')
    def test_name(self):
        self.assertNotIn('/',safe_name('../../foo'))

if __name__=='__main__':unittest.main()
