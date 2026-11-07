import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from nexus_tab_installer.core.links import parse_mod, normalize_entries, save_pack, load_pack, extract_mod_links

class TestMods(unittest.TestCase):
    def test_supported_links(self):
        for s in ('https://www.nexusmods.com/skyrimspecialedition/mods/123?tab=files', 'https://www.nexusmods.com/games/skyrimspecialedition/mods/123'):
            self.assertEqual(parse_mod(s)['mod_id'], 123)
    def test_reject(self):
        for s in ('http://nexusmods.com/skyrim/mods/1','https://evil.example/skyrim/mods/1','https://nexusmods.com/skyrim/images/1'):
            self.assertIsNone(parse_mod(s))
    def test_dedup(self):
        self.assertEqual(len(normalize_entries([{'url':'https://nexusmods.com/skyrim/mods/2'}, {'url':'https://nexusmods.com/skyrim/mods/2?tab=files'}])),1)
    def test_copy_paste_full_url(self):
        self.assertEqual(extract_mod_links('https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files&file_id=827594'),
                         ['https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files&file_id=827594'])
    def test_wrapped_url(self):
        self.assertEqual(len(extract_mod_links('https://www.nexusmods.com/skyrimspecialedition/mods/196600?tab=files&file_id=8\n27594')), 1)
    def test_many_urls(self):
        self.assertEqual(len(extract_mod_links('Here: https://nexusmods.com/skyrim/mods/2 and https://nexusmods.com/skyrim/mods/3')), 2)
    def test_markdown_and_encoded_links(self):
        value = '[мод](https%3A%2F%2Fwww.nexusmods.com%2Fgames%2Fskyrimspecialedition%2Fmods%2F196600%3Ftab%3Dfiles)'
        self.assertEqual(len(extract_mod_links(value)), 1)
    def test_wrapped_id(self):
        self.assertEqual(len(extract_mod_links('https://www.nexusmods.com/skyrimspecialedition/mods/\n196600')), 1)
    def test_roundtrip(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'pack.json'
            save_pack(p,[{'url':'https://nexusmods.com/skyrim/mods/2','title':'Example'}])
            self.assertEqual(load_pack(p)[0]['title'],'Example')
if __name__ == '__main__': unittest.main()
