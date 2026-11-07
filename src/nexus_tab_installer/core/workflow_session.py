"""One click tab-download workflow, with cancellation safe across threads."""
from threading import Event, RLock


def identity(item):
    return str(item['game']), int(item['mod_id'])


class WorkflowSession:
    def __init__(self, items):
        self.items = list(items)
        self.collection = None  # the folder of this run; a search has none
        self.cancelled = Event()
        self.lock = RLock()
        self.states = {identity(item): ('Готов к загрузке', '') for item in items}
        self.jobs = {}  # Most recent job per mod, for legacy UI compatibility.
        self.file_jobs = {}  # (game, mod_id, file_id) -> job id.
        self.tasks = {}  # "game:mod:file" -> {'mod': (game, mod_id), 'state': ...}

    def set_status(self, item, state, detail=''):
        with self.lock:
            if self.cancelled.is_set():
                return False
            self.states[identity(item)] = (state, detail)
            return True

    def attach_job(self, item, create):
        """Atomic with cancel: a racing stop cannot leave a live new job."""
        with self.lock:
            if self.cancelled.is_set():
                return None
            job = create()
            if job is not None:
                self.register_job(item, job)
            return job

    def register_job(self, item, job):
        """Track every selected file, not just the last file of a mod."""
        with self.lock:
            if self.cancelled.is_set():
                return False
            self.jobs[identity(item)] = job.id
            self.file_jobs[(*identity(item), getattr(job, 'file_id', job.id))] = job.id
            return True

    def file_jobs_snapshot(self):
        with self.lock:
            return dict(self.file_jobs)

    def all_job_ids(self):
        with self.lock:
            return list(dict.fromkeys(self.file_jobs.values()))

    def stop(self, cancel_job):
        with self.lock:
            self.cancelled.set()
            ids = list(dict.fromkeys([*self.jobs.values(), *self.file_jobs.values()]))
            self.states = {key: ('Отменено', '') if value[0] not in ('Готово', 'Ошибка') else value
                           for key, value in self.states.items()}
        for job_id in ids:
            cancel_job(job_id)
        return len(ids)

    def snapshot(self):
        with self.lock:
            return dict(self.states), dict(self.jobs)

    # Every selected file is a task ("game:mod:file", file 0 = main file not yet
    # known). The batch is finished when no task is pending any more.
    def expect(self, item, task):
        with self.lock:
            if self.cancelled.is_set():
                return False
            self.tasks.setdefault(task, {'mod': identity(item), 'state': 'pending'})
            return True

    def finish(self, task, ok=True):
        with self.lock:
            record = self.tasks.get(task)
            if record is None or record['state'] != 'pending':
                return False
            record['state'] = 'done' if ok else 'failed'
            return True

    def task_for(self, game, mod_id, file_id):
        """Pending task matching a delivered file, including an unresolved main file."""
        with self.lock:
            for key in (f'{game}:{int(mod_id)}:{int(file_id)}', f'{game}:{int(mod_id)}:0'):
                record = self.tasks.get(key)
                if record and record['state'] == 'pending':
                    return key
            return ''

    def fail_mod(self, item):
        with self.lock:
            for record in self.tasks.values():
                if record['mod'] == identity(item) and record['state'] == 'pending':
                    record['state'] = 'failed'

    def task_counts(self):
        with self.lock:
            counts = {'pending': 0, 'done': 0, 'failed': 0}
            for record in self.tasks.values():
                counts[record['state']] += 1
            return counts

    def mod_counts(self, item):
        with self.lock:
            counts = {'pending': 0, 'done': 0, 'failed': 0}
            for record in self.tasks.values():
                if record['mod'] == identity(item):
                    counts[record['state']] += 1
            return counts
