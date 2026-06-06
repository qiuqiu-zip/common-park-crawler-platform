from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine import CrawlerEngine
from .lifecycle import LifecycleSignal, WorkerLifecycleService
from .models import SchedulerRun, SpiderConfig, TaskStatus, WorkerJob, WorkerRunResult, WorkerState, WorkerStats
from .observability import (
    log_event,
    new_trace_id,
    record_scheduler_metrics,
    record_scheduler_report,
    record_worker_metrics,
    record_worker_report,
    safe_observe,
    start_trace,
    trace_id_from_job,
)
from .storage import FileStore
from .validation import ensure_valid_spider_config


class WorkerError(RuntimeError):
    pass


class FakeClock:
    def __init__(self, now: str | datetime) -> None:
        self._now = _coerce_datetime(now)

    def now(self) -> datetime:
        return self._now

    def set(self, value: str | datetime) -> None:
        self._now = _coerce_datetime(value)

    def advance(self, **kwargs: Any) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now


class WorkerService:
    def __init__(
        self,
        store: FileStore | None = None,
        engine: CrawlerEngine | None = None,
        *,
        fetcher=None,
        playwright_fetcher=None,
        clock: FakeClock | None = None,
        lease_seconds: int = 300,
        max_concurrent_jobs: int = 1,
    ) -> None:
        self.store = store or FileStore()
        self.engine = engine
        self.fetcher = fetcher
        self.playwright_fetcher = playwright_fetcher
        self.clock = clock
        self.lease_seconds = max(1, lease_seconds)
        self.max_concurrent_jobs = max(1, max_concurrent_jobs)

    def enqueue_spider_run(
        self,
        spider_config: SpiderConfig | dict[str, Any],
        *,
        source: str = "manual",
        priority: int = 0,
        run_after: str | datetime | None = None,
        schedule_id: str | None = None,
        task_id: str | None = None,
        max_attempts: int = 1,
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> WorkerJob:
        spider = ensure_valid_spider_config(spider_config)
        self.store.save_spider(spider)
        job_metadata = dict(metadata or {})
        job_metadata.setdefault("trace_id", new_trace_id())
        job = WorkerJob(
            job_id=job_id or uuid.uuid4().hex,
            job_type="spider_run",
            spider_id=spider.id,
            spider_config=spider.to_dict(),
            task_id=task_id,
            schedule_id=schedule_id,
            source=source,
            priority=int(priority or 0),
            run_after=_format_datetime(_coerce_datetime(run_after)) if run_after else None,
            max_attempts=max(1, int(max_attempts or 1)),
            metadata=job_metadata,
        )
        queued = self.store.enqueue_job(job)
        safe_observe(start_trace, self.store, str(queued.metadata.get("trace_id")), metadata={"job_id": queued.job_id, "spider_id": queued.spider_id})
        safe_observe(
            log_event,
            self.store,
            None,
            level="INFO",
            component="worker",
            event_type="job_enqueued",
            message=f"Worker job {queued.job_id} enqueued",
            trace_id=trace_id_from_job(queued),
            job_id=queued.job_id,
            schedule_id=queued.schedule_id,
            spider_id=queued.spider_id,
            metadata=queued.to_dict(),
        )
        return queued

    def run_once(self, worker_id: str | None = None) -> WorkerRunResult:
        selected_worker_id = worker_id or _worker_id()
        job = self.store.claim_job(selected_worker_id, now=self._now(), lease_seconds=self.lease_seconds)
        if job is None:
            self._save_worker_state(selected_worker_id, status="idle", current_job_id=None)
            return WorkerRunResult(run_id=uuid.uuid4().hex, worker_id=selected_worker_id, status="idle")
        return self._execute_claimed_job(job, selected_worker_id)

    def run_until_empty(self, worker_id: str | None = None, max_jobs: int | None = None) -> dict[str, Any]:
        base_worker_id = worker_id or _worker_id()
        runs: list[WorkerRunResult] = []
        concurrency_peak = 0
        while max_jobs is None or len(runs) < max_jobs:
            remaining = None if max_jobs is None else max_jobs - len(runs)
            batch_size = min(self.max_concurrent_jobs, remaining if remaining is not None else self.max_concurrent_jobs)
            if batch_size <= 0:
                break
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = [executor.submit(self.run_once, f"{base_worker_id}-{index + 1}") for index in range(batch_size)]
                batch = [future.result() for future in futures]
            claimed = [run for run in batch if run.status != "idle"]
            if not claimed:
                break
            concurrency_peak = max(concurrency_peak, len(claimed))
            runs.extend(claimed)
        self._save_worker_state(
            base_worker_id,
            status="idle",
            current_job_id=None,
            stats_patch={"concurrency_peak": concurrency_peak},
        )
        return {
            "worker_id": base_worker_id,
            "processed": len(runs),
            "concurrency_peak": concurrency_peak,
            "runs": [run.to_dict() for run in runs],
        }

    def start_polling(
        self,
        worker_id: str | None = None,
        *,
        interval_seconds: float = 1,
        stop_event=None,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        selected_worker_id = worker_id or _worker_id()
        runs: list[WorkerRunResult] = []
        iterations = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            result = self.run_once(selected_worker_id)
            if result.status == "idle":
                if interval_seconds > 0:
                    time.sleep(interval_seconds)
                continue
            runs.append(result)
        self._save_worker_state(selected_worker_id, status="stopped", current_job_id=None)
        return {"worker_id": selected_worker_id, "iterations": iterations, "processed": len(runs), "runs": [run.to_dict() for run in runs]}

    def recover_expired_jobs(self, now: str | datetime | None = None) -> list[dict[str, Any]]:
        return self.store.requeue_expired_leases(now=now or self._now())

    def stats(self) -> dict[str, Any]:
        return self.store.get_queue_stats()

    def queue_health(self) -> dict[str, Any]:
        storage = self.store.check_storage()
        return {"ok": storage["ok"], "storage": storage, "queue": self.stats()}

    def pause_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        return WorkerLifecycleService(self.store, operator="worker").pause_job(job_id, reason=reason)

    def resume_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        return WorkerLifecycleService(self.store, operator="worker").resume_job(job_id, reason=reason)

    def cancel_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        return WorkerLifecycleService(self.store, operator="worker").cancel_job(job_id, reason=reason)

    def retry_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        return WorkerLifecycleService(self.store, operator="worker").retry_job(job_id, reason=reason)

    def rerun_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        return WorkerLifecycleService(self.store, operator="worker").rerun_job(job_id, reason=reason)

    def list_job_events(self, job_id: str) -> list[dict[str, Any]]:
        return WorkerLifecycleService(self.store, operator="worker").list_job_events(job_id)

    def _execute_claimed_job(self, job: WorkerJob, worker_id: str) -> WorkerRunResult:
        started_at = _format_datetime(self._now())
        trace_id = trace_id_from_job(job) or new_trace_id()
        job.metadata["trace_id"] = trace_id
        run = WorkerRunResult(run_id=uuid.uuid4().hex, worker_id=worker_id, job_id=job.job_id, status="running", started_at=started_at)
        safe_observe(start_trace, self.store, trace_id, metadata={"job_id": job.job_id, "worker_id": worker_id, "spider_id": job.spider_id})
        safe_observe(
            log_event,
            self.store,
            None,
            level="INFO",
            component="worker",
            event_type="job_claimed",
            message=f"Worker {worker_id} claimed {job.job_id}",
            trace_id=trace_id,
            job_id=job.job_id,
            schedule_id=job.schedule_id,
            spider_id=job.spider_id,
            metadata={"attempt": job.attempt, "source": job.source},
        )
        self._save_worker_state(worker_id, status="running", current_job_id=job.job_id, stats_patch={"claimed_jobs": 1})
        self._record_scheduler_run(job, status="running", started_at=started_at)
        try:
            if job.job_type != "spider_run":
                raise WorkerError(f"unsupported worker job type: {job.job_type}")
            spider = SpiderConfig.from_dict(job.spider_config)
            task_id = job.task_id or f"worker-{job.job_id}"
            signal = LifecycleSignal(self.store, task_id=task_id, job_id=job.job_id)
            task = self._engine_for_job().run(spider, task_id=task_id, lifecycle_signal=signal, trace_id=trace_id)
            run.task_id = task.id
            run.summary = {
                "task_status": task.status.value,
                "total_requests": task.total_requests,
                "success_requests": task.success_requests,
                "failed_requests": task.failed_requests,
                "saved_records": task.saved_records or task.saved_count,
            }
            if task.status == TaskStatus.CANCELLED:
                cancelled = WorkerLifecycleService(self.store, operator=worker_id).mark_job_cancelled(job.job_id, worker_id=worker_id, reason=task.error_message)
                run.status = cancelled.status
                run.finished_at = cancelled.finished_at
                self._save_worker_state(worker_id, status="idle", current_job_id=None)
                self._record_scheduler_run(job, status="cancelled", task_id=task.id, finished_at=run.finished_at, summary=run.summary)
                self.store.record_worker_run(run)
                self._finalize_job_observability(job, run)
                return run
            if task.status != TaskStatus.SUCCESS:
                raise WorkerError(task.error_message or f"task {task.id} ended with {task.status.value}")
            completed = self.store.complete_job(job.job_id, worker_id, {"task_id": task.id, **run.summary})
            run.status = completed.status
            run.finished_at = completed.finished_at
            self._save_worker_state(worker_id, status="idle", current_job_id=None, stats_patch={"succeeded_jobs": 1})
            self._record_scheduler_run(job, status="success", task_id=task.id, finished_at=run.finished_at, summary=run.summary)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            failed = self.store.fail_job(job.job_id, worker_id, error, retry=True)
            run.status = "retried" if failed.status == "queued" else failed.status
            run.error = error
            run.finished_at = failed.updated_at
            stats_patch = {"retried_jobs": 1} if run.status == "retried" else {"dead_letter_jobs": 1, "failed_jobs": 1}
            self._save_worker_state(worker_id, status="idle", current_job_id=None, stats_patch=stats_patch)
            self._record_scheduler_run(job, status="failed", finished_at=run.finished_at, error=error, summary={"job_status": failed.status})
        self.store.record_worker_run(run)
        self._finalize_job_observability(job, run)
        return run

    def _engine_for_job(self) -> CrawlerEngine:
        if self.engine is not None and self.max_concurrent_jobs == 1:
            return self.engine
        return CrawlerEngine(store=self.store, fetcher=self.fetcher, playwright_fetcher=self.playwright_fetcher)

    def _save_worker_state(
        self,
        worker_id: str,
        *,
        status: str,
        current_job_id: str | None,
        stats_patch: dict[str, int] | None = None,
    ) -> WorkerState:
        now = _format_datetime(self._now())
        try:
            state = self.store.get_worker_state(worker_id)
        except FileNotFoundError:
            state = WorkerState(worker_id=worker_id, started_at=now)
        stats = WorkerStats.from_dict(state.stats)
        for key, value in (stats_patch or {}).items():
            if key == "concurrency_peak":
                setattr(stats, key, max(getattr(stats, key), int(value or 0)))
            elif hasattr(stats, key):
                setattr(stats, key, getattr(stats, key) + int(value or 0))
        state.status = status
        state.current_job_id = current_job_id
        state.heartbeat_at = now
        state.stopped_at = now if status in {"idle", "stopped"} else None
        state.stats = stats.to_dict()
        return self.store.save_worker_state(state)

    def _record_scheduler_run(
        self,
        job: WorkerJob,
        *,
        status: str,
        task_id: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        error: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        if not job.schedule_id:
            return
        run_id = str(job.metadata.get("scheduler_run_id") or job.job_id)
        run = SchedulerRun(
            id=run_id,
            schedule_id=job.schedule_id,
            spider_id=job.spider_id,
            task_id=task_id or job.task_id,
            status=status,
            trigger="worker",
            scheduled_for=job.metadata.get("scheduled_for"),
            started_at=started_at,
            finished_at=finished_at,
            error_type=error.get("type") if error else None,
            error_message=error.get("message") if error else None,
            summary={"job_id": job.job_id, "trace_id": trace_id_from_job(job), **(summary or {})},
        )
        self.store.record_scheduler_run(run)
        if finished_at or status not in {"running"}:
            safe_observe(record_scheduler_metrics, self.store, run)
            safe_observe(record_scheduler_report, self.store, run)

    def _finalize_job_observability(self, job: WorkerJob, run: WorkerRunResult) -> None:
        trace_id = trace_id_from_job(job)
        safe_observe(
            log_event,
            self.store,
            None,
            level="INFO" if run.status == "succeeded" else "ERROR" if run.status in {"failed", "dead_letter"} else "WARNING",
            component="worker",
            event_type="job_finished",
            message=f"Worker job {job.job_id} finished with {run.status}",
            trace_id=trace_id,
            job_id=job.job_id,
            schedule_id=job.schedule_id,
            spider_id=job.spider_id,
            error_type=run.error.get("type") if run.error else None,
            metadata={"run": run.to_dict(), "job_status": job.status},
        )
        safe_observe(record_worker_metrics, self.store, job, run)
        safe_observe(record_worker_report, self.store, job, run)

    def _now(self) -> datetime:
        if self.clock is not None:
            return _coerce_datetime(self.clock.now())
        return datetime.now(timezone.utc)


def _worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:10]}"


def _coerce_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
