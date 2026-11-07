import json
import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.downloads.queue import DownloadQueue

class HistoryTests(unittest.TestCase):
    def test_history_does_not_store_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'history.json'
            q = DownloadQueue(lambda job, cb: None, history_path=path)
            job = q.add('skyrimspecialedition', 10, 20, 'mod.zip', apikey='PRIVATE_API_KEY', key='NXM_SECRET', expires='123456', user_id='2')
            q.pending.join()
            q.close()
            saved = path.read_text()
            self.assertNotIn('PRIVATE_API_KEY', saved)
            self.assertNotIn('NXM_SECRET', saved)
            self.assertEqual(json.loads(saved)[0]['state'], 'finished')
            q2 = DownloadQueue(lambda job, cb: None, history_path=path)
            self.assertEqual(len(q2.items), 1)
            self.assertEqual(next(iter(q2.items.values())).state, 'finished')
            q2.close()

    def test_duplicate_active_download_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            from threading import Event
            hold = Event()
            started = Event()
            def work(job, cb):
                started.set()
                hold.wait(2)
            q = DownloadQueue(work)
            first = q.add('game', 1, 2, 'first.zip')
            self.assertTrue(started.wait(1))
            self.assertIsNone(q.add('game', 1, 2, 'second.zip'))
            self.assertEqual(len(q.items), 1)
            hold.set()
            q.pending.join()
            q.close()

    def test_pending_history_becomes_interrupted(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'history.json'
            path.write_text(json.dumps([{'id': 5, 'game':'game', 'mod_id':1, 'file_id':2, 'filename':'a.zip', 'state':'downloading'}]))
            q = DownloadQueue(lambda j, cb: None, history_path=path)
            self.assertEqual(q.items[5].state, 'interrupted')
            self.assertEqual(q.add('game', 1, 3, 'b.zip').id, 6)
            q.pending.join()
            q.close()
