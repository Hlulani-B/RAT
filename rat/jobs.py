"""Background ingestion queue.

Ingestion (extract/clone/analyze) runs on a single worker thread: jobs are
serialised so that heavy analyses never contend for SQLite's single writer or
starve the event loop of the web server.  The UI polls ``/api/jobs/<id>`` for
progress; each job reports a phase, a fraction, and a human-readable note.
"""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Callable, Optional


class Job:
    def __init__(self, job_id: int, kind: str, repo_id: Optional[int]):
        self.id = job_id
        self.kind = kind
        self.repo_id = repo_id
        self.status = "queued"   # queued | running | done | error
        self.phase = ""
        self.note = ""
        self.progress = 0.0
        self.error = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "repo_id": self.repo_id,
            "status": self.status,
            "phase": self.phase,
            "note": self.note,
            "progress": round(self.progress, 4),
            "error": self.error,
        }


class JobRunner:
    """A tiny serial background runner for ingestion jobs."""

    def __init__(self):
        self._jobs: dict[int, Job] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._q: "queue.Queue[tuple[int, Callable]]" = queue.Queue()
        t = threading.Thread(target=self._worker, daemon=True, name="rat-jobs")
        t.start()

    def submit(self, kind: str, repo_id: Optional[int],
               fn: Callable[[Job], None]) -> Job:
        with self._lock:
            job = Job(self._next_id, kind, repo_id)
            self._next_id += 1
            self._jobs[job.id] = job
        self._q.put((job.id, fn))
        return job

    def get(self, job_id: int) -> Optional[Job]:
        return self._jobs.get(job_id)

    def _worker(self) -> None:
        while True:
            job_id, fn = self._q.get()
            job = self._jobs[job_id]
            job.status = "running"
            try:
                fn(job)
                if job.status == "running":
                    job.status = "done"
                    job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
                traceback.print_exc()
            finally:
                self._q.task_done()
