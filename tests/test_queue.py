import unittest
import tempfile
import threading
from pathlib import Path
from nexus_tab_installer.downloads.queue import DownloadQueue
from nexus_tab_installer.downloads.transfer import download, DownloadCancelled

class TestQueue(unittest.TestCase):
    def test_sequential_and_status(self):
        calls=[]
        def process(job, progress):
            calls.append(job.file_id)
            progress(100,200)
        q=DownloadQueue(process)
        a=q.add('game',1,1,'one.zip')
        b=q.add('game',1,2,'two.zip')
        self.assertTrue(q.pending.join() is None)
        self.assertEqual(calls,[1,2])
        self.assertEqual((a.state,b.state),('finished','finished'))
        self.assertEqual(a.progress,100)
        q.close()

    def test_failed_job_does_not_block(self):
        def process(job, cb):
            if job.file_id==1: raise RuntimeError('network')
        q=DownloadQueue(process)
        a=q.add('game',1,1,'bad.zip')
        b=q.add('game',1,2,'good.zip')
        q.pending.join()
        self.assertEqual(a.state,'failed')
        self.assertEqual(b.state,'finished')
        q.close()

    def test_pre_cancelled_job(self):
        gate=threading.Event()
        def process(job, cb): gate.wait(timeout=3)
        q=DownloadQueue(process)
        a=q.add('game',1,1,'first')
        # Ensure first job is running before cancelling queued second job.
        import time
        while a.state=='queued': time.sleep(.01)
        b=q.add('game',1,2,'second')
        self.assertTrue(q.cancel(b.id))
        gate.set(); q.pending.join()
        self.assertEqual(b.state,'cancelled')
        q.close()

if __name__=='__main__': unittest.main()
