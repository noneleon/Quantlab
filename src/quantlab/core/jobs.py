"""Background jobs (CORE-007): long runs started from the web console (backtests, optimizations).

A job is a function run on a worker thread of the API server. It receives a ``threading.Event`` that is
set when the owner cancels; the function passes it to the service, which stops the engine (backtest) or
skips the trials not started yet (optimization). A job returns the id of what it produced (backtest run /
optimization), so the console can link to it; the run itself is stored by its service as usual, so nothing
is lost when the server stops — only the job list (in memory) is. Queued jobs are cancelled at once; at most
``max_workers`` run together.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from quantlab.core.ids import new_id
from quantlab.core.logging import get_logger

log = get_logger(__name__)

JobFn = Callable[[threading.Event], str | None]
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class JobError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    kind: str  # "backtest" | "optimization"
    title: str
    params: dict[str, Any]
    state: str = "queued"  # queued | running | succeeded | failed | cancelled
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    result_id: str | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "cancel"} | {
            "cancel_requested": self.cancel.is_set()
        }


class JobRunner:
    def __init__(self, max_workers: int = 1, keep: int = 200) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="quantlab-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, title: str, params: dict[str, Any], fn: JobFn) -> Job:
        job = Job(new_id(), kind, title, params)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._pool.submit(self._run, job, fn)
        log.info("job.queued", job_id=job.id, kind=kind, title=title)
        return job

    def _run(self, job: Job, fn: JobFn) -> None:
        with self._lock:
            if job.cancel.is_set():  # cancelled while queued
                return
            job.state, job.started_at = "running", datetime.now(UTC).isoformat()
        result: str | None = None
        error: str | None = None
        try:
            result = fn(job.cancel)
        except Exception as exc:  # reported on the job; the server keeps running
            state, error = "failed", str(exc) or type(exc).__name__
        else:
            state = "cancelled" if job.cancel.is_set() else "succeeded"
        with self._lock:
            job.state, job.result_id, job.error = state, result, error
            job.finished_at = datetime.now(UTC).isoformat()
        log.info("job.finished", job_id=job.id, state=state, result_id=result)

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobError(f"no job {job_id}")
            if job.state in TERMINAL:
                return job
            job.cancel.set()
            if job.state == "queued":
                job.state, job.finished_at = "cancelled", datetime.now(UTC).isoformat()
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobError(f"no job {job_id}")
        return job

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.state in TERMINAL]
        for job in sorted(finished, key=lambda j: j.created_at)[: max(0, len(self._jobs) - self._keep)]:
            del self._jobs[job.id]

    def shutdown(self) -> None:
        for job in self.list():
            job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
