import unittest
from unittest.mock import patch
from nexus_tab_installer.nexus.api import nxm_parts, NexusClient, NexusError

class TestNxmUserId(unittest.TestCase):
    def test_nxm_requires_all_parameters(self):
        with self.assertRaises(NexusError):
            nxm_parts('nxm://skyrimspecialedition/mods/1/files/2?key=abc&expires=999')
    def test_nxm_preserves_user_id(self):
        self.assertEqual(nxm_parts('nxm://skyrimspecialedition/mods/1/files/2?key=abc&expires=999&user_id=15')[-1], '15')
    def test_api_passes_nxm_parameters(self):
        client=NexusClient('test')
        with patch.object(client, 'get', return_value=[{'URI':'https://cdn.example.org/file'}]) as req:
            client.download_links('skyrimspecialedition', 1, 2, 'abc', '999', '15')
            self.assertEqual(req.call_args.args[1], {'key':'abc','expires':'999','user_id':'15'})

if __name__ == '__main__': unittest.main()
