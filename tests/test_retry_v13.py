import tempfile
import threading
import unittest
from pathlib import Path
from nexus_tab_installer.downloads.queue import DownloadQueue

class RetryTests(unittest.TestCase):
    def test_retry_failed_creates_new_job_with_fresh_secret_and_no_history_leak(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'jobs.json'
            calls = []
            def work(job, progress):
                calls.append(job.id)
                if len(calls) == 1:
                    raise RuntimeError('temporary failure')
            q = DownloadQueue(work, history_path=path)
            first = q.add('game', 1, 2, 'file.zip', apikey='OLDSECRET')
            q.pending.join()
            self.assertEqual(first.state, 'failed')
            second = q.retry(first.id, apikey='NEWSECRET')
            q.pending.join()
            self.assertEqual(second.state, 'finished')
            self.assertEqual(first.state, 'failed')
            self.assertEqual(len(calls), 2)
            self.assertNotIn('NEWSECRET', path.read_text())
            self.assertNotIn('OLDSECRET', path.read_text())
            q.close()

    def test_retry_running_and_missing_key_rejected(self):
        gate = threading.Event()
        started = threading.Event()
        def work(job, progress):
            started.set()
            gate.wait(2)
        q = DownloadQueue(work)
        job = q.add('game', 1, 2, 'file.zip')
        self.assertTrue(started.wait(1))
        self.assertIsNone(q.retry(job.id, apikey='test'))
        gate.set()
        q.pending.join()
        q.close()

    def test_concurrent_duplicate_submission_single_active_job(self):
        gate = threading.Event()
        started = threading.Event()
        def work(job, progress):
            started.set()
            gate.wait(2)
        q = DownloadQueue(work)
        barrier = threading.Barrier(12)
        results = []
        def add():
            barrier.wait()
            results.append(q.add('game', 1, 2, 'file.zip'))
        threads = [threading.Thread(target=add) for _ in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sum(x is not None for x in results), 1)
        gate.set()
        q.pending.join()
        q.close()
