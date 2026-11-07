import tempfile
import time
import unittest
from pathlib import Path
from nexus_tab_installer.nexus.nxm_inbox import submit_nxm, pending_nxm, acknowledge_nxm

URI = 'nxm://skyrimspecialedition/mods/42/files/123?key=abc&expires=9999999999&user_id=77'

class DurableInboxTests(unittest.TestCase):
    def test_retained_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as d:
            submit_nxm(URI, d)
            first = pending_nxm(d)
            self.assertEqual(len(first), 1)
            self.assertEqual(first, pending_nxm(d))
            acknowledge_nxm(first[0][0])
            self.assertEqual(pending_nxm(d), [])

    def test_expired_entry_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            path = submit_nxm(URI, d)
            import json
            info = json.loads(path.read_text())
            info['created'] = int(time.time()) - 10000
            path.write_text(json.dumps(info))
            self.assertEqual(pending_nxm(d), [])
            self.assertFalse(path.exists())

if __name__ == '__main__': unittest.main()
