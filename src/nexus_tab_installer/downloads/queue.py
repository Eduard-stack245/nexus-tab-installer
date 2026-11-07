"""Sequential, cancellable download jobs. No GUI dependencies."""
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from queue import Queue, Empty
import itertools
import json
from pathlib import Path

@dataclass
class Job:
    id: int
    game: str
    mod_id: int
    file_id: int
    filename: str
    key: str | None = field(default=None, repr=False)
    expires: str | None = field(default=None, repr=False)
    user_id: str | None = field(default=None, repr=False)
    apikey: str = field(default='', repr=False)
    # Signed CDN link handed over by the browser extension (short-lived, never saved).
    url: str | None = field(default=None, repr=False)
    task: str = ''
    fallback: str = ''  # 'browser' when the CDN refused the handed-over link
    directory: str = ''
    export_directory: str = ''
    state: str = 'queued'
    progress: int = 0
    total: int = 0
    error: str = ''
    cancel: Event = field(default_factory=Event, repr=False)

class DownloadQueue:
    def __init__(self, process, on_change=None, history_path=None):
        self.process, self.on_change = process, (on_change or (lambda job: None))
        self.items = {}
        self.pending = Queue()
        self.lock = Lock()
        self.history_lock = Lock()
        self.ids = itertools.count(1)
        self.history_path = Path(history_path) if history_path else None
        self._load_history()
        self.stopped = Event()
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _load_history(self):
        if not self.history_path:
            return
        try:
            records = json.loads(self.history_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        maximum = 0
        for record in records if isinstance(records, list) else []:
            try:
                state = record['state']
                if state not in ('finished', 'failed', 'cancelled', 'interrupted'):
                    state = 'interrupted'
                job = Job(int(record['id']), str(record['game']), int(record['mod_id']),
                          int(record['file_id']), str(record['filename']),
                          directory=str(record.get('directory', '')), export_directory=str(record.get('export_directory', '')), state=state,
                          progress=int(record.get('progress', 0)), total=int(record.get('total', 0)),
                          error=str(record.get('error', '')))
                self.items[job.id] = job
                maximum = max(maximum, job.id)
            except (TypeError, ValueError, KeyError):
                continue
        self.ids = itertools.count(maximum + 1)

    def _save_history(self):
        if not self.history_path:
            return
        # Never persist API keys, NXM tokens, or short-lived CDN URLs.
        with self.lock:
            records = [dict(id=j.id, game=j.game, mod_id=j.mod_id, file_id=j.file_id,
                            filename=j.filename, directory=j.directory, export_directory=j.export_directory, state=j.state,
                            progress=j.progress, total=j.total, error=j.error)
                       for j in list(self.items.values())[-500:]]
        with self.history_lock:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.history_path)

    def busy(self):
        with self.lock:
            return any(j.state in ('queued', 'downloading') for j in self.items.values())

    def has_active(self, game, mod_id, file_id):
        with self.lock:
            return any(j.game == game and j.mod_id == int(mod_id) and
                       j.file_id == int(file_id) and j.state in ('queued', 'downloading')
                       for j in self.items.values())

    def add(self, game, mod_id, file_id, filename, key=None, expires=None, apikey="", directory="", user_id=None, export_directory="", url=None, task=""):
        # Check and insert atomically: concurrent NXM deliveries cannot create duplicate jobs.
        with self.lock:
            if any(j.game == game and j.mod_id == int(mod_id) and
                   j.file_id == int(file_id) and j.state in ('queued', 'downloading')
                   for j in self.items.values()):
                return None
            job = Job(next(self.ids), game, int(mod_id), int(file_id), filename,
                      key=key, expires=expires, user_id=user_id, apikey=apikey,
                      url=url, task=task, directory=directory)
            job.export_directory = export_directory
            self.items[job.id] = job
        self.pending.put(job)
        self._save_history()
        self.on_change(job)
        return job

    def retry(self, id, *, apikey, key=None, expires=None, user_id=None, directory=None, export_directory=None):
        """Retry historical job with fresh credentials. NXM tokens are never restored from disk."""
        with self.lock:
            original = self.items.get(int(id))
            if not original or original.state not in ('failed', 'cancelled', 'interrupted'):
                return None
            if any(j.id != original.id and j.game == original.game and
                   j.mod_id == original.mod_id and j.file_id == original.file_id and
                   j.state in ('queued', 'downloading') for j in self.items.values()):
                return None
            if not apikey:
                raise ValueError('API key required to retry')
            job = Job(next(self.ids), original.game, original.mod_id, original.file_id,
                      original.filename, key=key, expires=expires, user_id=user_id, apikey=apikey,
                      task=original.task,
                      directory=directory if directory is not None else original.directory)
            job.export_directory = export_directory if export_directory is not None else original.export_directory
            self.items[job.id] = job
        self.pending.put(job)
        self._save_history()
        self.on_change(job)
        return job

    def cancel(self, id):
        with self.lock:
            job = self.items.get(int(id))
            if not job or job.state not in ('queued', 'downloading'):
                return False
            job.cancel.set()
            if job.state == 'queued':
                job.state = 'cancelled'
        self._save_history()
        self.on_change(job)
        return True

    def update(self, job, done, total):
        with self.lock:
            job.progress, job.total = done, total
        self.on_change(job)

    def _run(self):
        while not self.stopped.is_set():
            try:
                job = self.pending.get(timeout=.2)
            except Empty:
                continue
            if job.cancel.is_set():
                self.pending.task_done()
                continue
            with self.lock:
                job.state = 'downloading'
            self.on_change(job)
            try:
                self.process(job, lambda n, t: self.update(job, n, t))
                job.state = 'cancelled' if job.cancel.is_set() else 'finished'
            except Exception as exc:
                job.state = 'cancelled' if job.cancel.is_set() else 'failed'
                job.error = '' if job.cancel.is_set() else str(exc)
            self._save_history()
            self.on_change(job)
            self.pending.task_done()

    def close(self):
        self.stopped.set()
        for job in list(self.items.values()):
            if job.state in ('queued', 'downloading'):
                job.cancel.set()
        self.thread.join(timeout=2)
