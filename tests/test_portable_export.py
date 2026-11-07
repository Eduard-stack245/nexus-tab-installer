import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.downloads.export import export_archive

class PortableExportTests(unittest.TestCase):
    def test_copy_no_replace_and_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library, manager = base/'library', base/'manager'
            library.mkdir(); manager.mkdir()
            source = library/'a.zip'; source.write_bytes(b'one')
            exported = export_archive(source, manager)
            self.assertEqual(exported.read_bytes(), b'one')
            self.assertTrue(source.is_file())
            self.assertEqual(export_archive(source, manager), exported)
            source.write_bytes(b'two')
            another = export_archive(source, manager)
            self.assertNotEqual(another, exported)
            self.assertEqual(exported.read_bytes(), b'one')
            self.assertEqual(another.read_bytes(), b'two')

if __name__ == '__main__': unittest.main()
