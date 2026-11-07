import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.nexus.free_workflow import FreeQueue, files_page_url

class TestFreeQueue(unittest.TestCase):
    def test_url_keeps_query(self):
        url = files_page_url('https://www.nexusmods.com/skyrimspecialedition/mods/42?utm_source=x', 101)
        self.assertIn('utm_source=x', url)
        self.assertIn('file_id=101', url)
        self.assertIn('tab=files', url)
    def test_persist_dedup_and_remove(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'queue.json'
            q = FreeQueue(path)
            url = files_page_url('https://www.nexusmods.com/skyrimspecialedition/mods/42', 101)
            self.assertEqual(q.add([url,url]), 1)
            self.assertEqual(len(FreeQueue(path)), 1)
            self.assertTrue(q.remove_file('skyrimspecialedition',42,101))
            self.assertEqual(len(FreeQueue(path)), 0)
    def test_no_removal_for_other_file(self):
        with tempfile.TemporaryDirectory() as d:
            q = FreeQueue(Path(d)/'queue.json')
            url = files_page_url('https://www.nexusmods.com/fallout4/mods/42', 101)
            q.add([url])
            self.assertFalse(q.remove_file('fallout4',42,102))
            self.assertEqual(q.peek(),url)
