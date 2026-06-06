from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import LifecycleEvent, SchedulerJob, SchedulerRun, SpiderConfig, TaskRecord, TaskStatus, WorkerJob, WorkerRunResult, WorkerState
from .validation import ensure_valid_spider_config

STORAGE_VERSION = "1.0"
_QUEUE_STATUS_DIRS = ("queued", "leased", "running", "paused", "retrying", "cancelling", "succeeded", "failed", "cancelled", "dead_letter")
TASK_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.PAUSED},
    TaskStatus.RUNNING: {TaskStatus.RUNNING, TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.CANCELLING},
    TaskStatus.SUCCESS: {TaskStatus.SUCCESS, TaskStatus.RERUNNING},
    TaskStatus.FAILED: {TaskStatus.FAILED, TaskStatus.RUNNING, TaskStatus.RETRYING},
    TaskStatus.CANCELLED: {TaskStatus.CANCELLED, TaskStatus.RUNNING, TaskStatus.RETRYING},
    TaskStatus.PAUSED: {TaskStatus.PAUSED, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.RETRYING: {TaskStatus.PENDING},
    TaskStatus.RERUNNING: {TaskStatus.PENDING},
    TaskStatus.CANCELLING: {TaskStatus.CANCELLING, TaskStatus.CANCELLED, TaskStatus.FAILED},
}


class StorageError(RuntimeError):
    def __init__(self, message: str, *, path: str | Path | None = None, **context: Any) -> None:
        self.path = Path(path) if path is not None else None
        self.context = context
        parts = [message]
        if self.path is not None:
            parts.append(f"path={self.path}")
        for key, value in context.items():
            parts.append(f"{key}={value}")
        super().__init__("; ".join(parts))


class AtomicWriteError(StorageError):
    pass


class FileLockError(StorageError):
    pass


class CorruptedFileError(StorageError):
    pass


class InvalidTaskTransitionError(StorageError):
    pass


class SnapshotError(StorageError):
    pass


@dataclass(slots=True)
class RepairAction:
    action: str
    path: str
    target: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "target": self.target,
            "reason": self.reason,
        }


class FileStore:
    def __init__(self, root: str | Path = "data", *, lock_timeout_seconds: float = 5.0) -> None:
        self.root = Path(root)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.spiders_dir = self.root / "spiders"
        self.tasks_dir = self.root / "tasks"
        self.results_dir = self.root / "results"
        self.hashes_dir = self.root / "hashes"
        self.watermarks_dir = self.root / "watermarks"
        self.checkpoints_dir = self.root / "checkpoints"
        self.schedules_dir = self.root / "schedules"
        self.scheduler_runs_dir = self.root / "scheduler_runs"
        self.queue_dir = self.root / "queue"
        self.queue_queued_dir = self.queue_dir / "queued"
        self.queue_leased_dir = self.queue_dir / "leased"
        self.queue_running_dir = self.queue_dir / "running"
        self.queue_paused_dir = self.queue_dir / "paused"
        self.queue_retrying_dir = self.queue_dir / "retrying"
        self.queue_cancelling_dir = self.queue_dir / "cancelling"
        self.queue_succeeded_dir = self.queue_dir / "succeeded"
        self.queue_failed_dir = self.queue_dir / "failed"
        self.queue_cancelled_dir = self.queue_dir / "cancelled"
        self.queue_dead_letters_dir = self.queue_dir / "dead_letters"
        self.workers_dir = self.root / "workers"
        self.worker_runs_dir = self.root / "worker_runs"
        self.lifecycle_events_dir = self.root / "lifecycle_events"
        self.lifecycle_task_events_dir = self.lifecycle_events_dir / "tasks"
        self.lifecycle_job_events_dir = self.lifecycle_events_dir / "jobs"
        self.lifecycle_scheduler_run_events_dir = self.lifecycle_events_dir / "scheduler_runs"
        self.lifecycle_signals_dir = self.root / "lifecycle_signals"
        self.lifecycle_task_signals_dir = self.lifecycle_signals_dir / "tasks"
        self.lifecycle_job_signals_dir = self.lifecycle_signals_dir / "jobs"
        self.sessions_dir = self.root / "sessions"
        self.session_profiles_dir = self.sessions_dir / "profiles"
        self.session_cookies_dir = self.sessions_dir / "cookies"
        self.session_storage_states_dir = self.sessions_dir / "storage_states"
        self.session_accounts_dir = self.sessions_dir / "accounts"
        self.session_events_dir = self.sessions_dir / "events"
        self.observability_dir = self.root / "observability"
        self.observability_logs_dir = self.observability_dir / "logs"
        self.observability_log_tasks_dir = self.observability_logs_dir / "tasks"
        self.observability_log_jobs_dir = self.observability_logs_dir / "jobs"
        self.observability_log_scheduler_dir = self.observability_logs_dir / "scheduler"
        self.observability_log_system_dir = self.observability_logs_dir / "system"
        self.observability_metrics_dir = self.observability_dir / "metrics"
        self.observability_metric_tasks_dir = self.observability_metrics_dir / "tasks"
        self.observability_metric_jobs_dir = self.observability_metrics_dir / "jobs"
        self.observability_metric_scheduler_dir = self.observability_metrics_dir / "scheduler"
        self.observability_metric_system_dir = self.observability_metrics_dir / "system"
        self.observability_reports_dir = self.observability_dir / "reports"
        self.observability_report_tasks_dir = self.observability_reports_dir / "tasks"
        self.observability_report_jobs_dir = self.observability_reports_dir / "jobs"
        self.observability_report_scheduler_dir = self.observability_reports_dir / "scheduler"
        self.observability_traces_dir = self.observability_dir / "traces"
        self.locks_dir = self.root / "locks"
        self.tmp_dir = self.root / "tmp"
        self.snapshots_dir = self.root / "snapshots"
        self.dead_letters_dir = self.root / "dead_letters"
        self.exports_dir = self.root / "exports"
        self.exports_files_dir = self.exports_dir / "files"
        self.exports_manifests_dir = self.exports_dir / "manifests"
        self.debug_reports_dir = self.root / "debug_reports"
        self.debug_artifacts_dir = self.root / "debug_artifacts"
        self.metadata_path = self.root / "storage_metadata.json"
        self._ensure_directories()
        self._ensure_metadata()

    @property
    def required_directories(self) -> list[Path]:
        return [
            self.spiders_dir,
            self.tasks_dir,
            self.results_dir,
            self.hashes_dir,
            self.watermarks_dir,
            self.checkpoints_dir,
            self.schedules_dir,
            self.scheduler_runs_dir,
            self.queue_dir,
            self.queue_queued_dir,
            self.queue_leased_dir,
            self.queue_running_dir,
            self.queue_paused_dir,
            self.queue_retrying_dir,
            self.queue_cancelling_dir,
            self.queue_succeeded_dir,
            self.queue_failed_dir,
            self.queue_cancelled_dir,
            self.queue_dead_letters_dir,
            self.workers_dir,
            self.worker_runs_dir,
            self.lifecycle_events_dir,
            self.lifecycle_task_events_dir,
            self.lifecycle_job_events_dir,
            self.lifecycle_scheduler_run_events_dir,
            self.lifecycle_signals_dir,
            self.lifecycle_task_signals_dir,
            self.lifecycle_job_signals_dir,
            self.sessions_dir,
            self.session_profiles_dir,
            self.session_cookies_dir,
            self.session_storage_states_dir,
            self.session_accounts_dir,
            self.session_events_dir,
            self.observability_dir,
            self.observability_logs_dir,
            self.observability_log_tasks_dir,
            self.observability_log_jobs_dir,
            self.observability_log_scheduler_dir,
            self.observability_log_system_dir,
            self.observability_metrics_dir,
            self.observability_metric_tasks_dir,
            self.observability_metric_jobs_dir,
            self.observability_metric_scheduler_dir,
            self.observability_metric_system_dir,
            self.observability_reports_dir,
            self.observability_report_tasks_dir,
            self.observability_report_jobs_dir,
            self.observability_report_scheduler_dir,
            self.observability_traces_dir,
            self.locks_dir,
            self.tmp_dir,
            self.snapshots_dir,
            self.dead_letters_dir,
            self.exports_dir,
            self.exports_files_dir,
            self.exports_manifests_dir,
            self.debug_reports_dir,
            self.debug_artifacts_dir,
        ]

    def save_spider(self, spider: SpiderConfig | dict[str, Any]) -> Path:
        return self.save_spider_config(spider)

    def save_spider_config(self, config: SpiderConfig | dict[str, Any]) -> Path:
        spider = ensure_valid_spider_config(config)
        path = self.spiders_dir / f"{_safe_name(spider.id)}.json"
        payload = spider.to_dict()
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def load_spider(self, spider_id: str) -> SpiderConfig:
        return self.get_spider_config(spider_id)

    def get_spider_config(self, spider_id: str) -> SpiderConfig:
        path = self.spiders_dir / f"{_safe_name(spider_id)}.json"
        return SpiderConfig.from_dict(self._load_json(path))

    def list_spiders(self) -> list[dict[str, Any]]:
        return self.list_spider_configs()

    def list_spider_configs(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []
        for path in sorted(self.spiders_dir.glob("*.json")):
            payload = self._load_json(path)
            if enabled is None or bool(payload.get("enabled", True)) is enabled:
                configs.append(payload)
        return configs

    def delete_spider_config(self, spider_id: str) -> dict[str, Any]:
        source = self.spiders_dir / f"{_safe_name(spider_id)}.json"
        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = self.dead_letters_dir / "spiders"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{_safe_name(spider_id)}-{_compact_time()}.json"
        self._replace_file(source, target)
        self._touch_metadata()
        return {"deleted": spider_id, "moved_to": str(target)}

    def save_task(self, task: TaskRecord) -> Path:
        path = self.tasks_dir / f"{_safe_name(task.id)}.json"
        if path.exists():
            current = self.load_task(task.id)
            _validate_task_transition(current.status, _task_status(task.status), task_id=task.id, path=path)
        self._atomic_write_json(path, task.to_dict())
        self._touch_metadata()
        return path

    def load_task(self, task_id: str) -> TaskRecord:
        path = self.tasks_dir / f"{_safe_name(task_id)}.json"
        return TaskRecord.from_dict(self._load_json(path))

    def list_tasks(self) -> list[dict[str, Any]]:
        return [self._load_json(path) for path in sorted(self.tasks_dir.glob("*.json"))]

    def update_task_status(self, task_id: str, status: TaskStatus, **updates: Any) -> TaskRecord:
        task = self.load_task(task_id)
        _validate_task_transition(task.status, status, task_id=task_id, path=self.tasks_dir / f"{_safe_name(task_id)}.json")
        task.status = status
        for key, value in updates.items():
            setattr(task, key, value)
        self.save_task(task)
        return task

    def append_record(self, task_id: str, record: dict[str, Any]) -> None:
        path = self.results_dir / f"{_safe_name(task_id)}.jsonl"
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def read_records(self, task_id: str, *, strict: bool = True) -> list[dict[str, Any]]:
        return list(self.iter_records(task_id, strict=strict))

    def iter_records(
        self,
        task_id: str,
        *,
        strict: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> Iterator[dict[str, Any]]:
        path = self.results_dir / f"{_safe_name(task_id)}.jsonl"
        if not path.exists():
            return
        start = max(0, int(offset or 0))
        max_items = None if limit is None else max(0, int(limit))
        yielded = 0
        for index, record in enumerate(self._iter_jsonl(path, strict=strict)):
            if index < start:
                continue
            if max_items is not None and yielded >= max_items:
                break
            yielded += 1
            yield record

    def has_hash(self, dataset: str, hash_value: str, scope: str = "global") -> bool:
        return hash_value in self.load_hashes(dataset, scope=scope)

    def add_hash(self, dataset: str, hash_value: str, scope: str = "global") -> None:
        self.add_hashes(dataset, [hash_value], scope=scope)

    def add_hashes(self, dataset: str, hash_values: list[str] | set[str] | tuple[str, ...], scope: str = "global") -> None:
        path = self._hash_path(dataset, scope)
        with self._file_lock(path):
            hashes = self._load_hashes_from_path(path)
            hashes.update(str(item) for item in hash_values if str(item).strip())
            self._atomic_write_text(path, "".join(f"{item}\n" for item in sorted(hashes)))

    def load_hashes(self, dataset: str, scope: str = "global") -> set[str]:
        return self._load_hashes_from_path(self._hash_path(dataset, scope))

    def iter_hashes(self, dataset: str, scope: str = "global") -> Iterator[str]:
        return iter(sorted(self.load_hashes(dataset, scope=scope)))

    def get_watermark(self, spider_id: str, dataset: str) -> dict[str, Any] | None:
        path = self._watermark_path(spider_id, dataset)
        if not path.exists():
            return None
        return self._load_json(path)

    def update_watermark(
        self,
        spider_id: str,
        dataset: str,
        value: Any,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "spider_id": spider_id,
            "dataset": dataset,
            "value": value,
            "metadata": metadata or {},
            "updated_at": _now(),
        }
        self._atomic_write_json(self._watermark_path(spider_id, dataset), payload)
        self._touch_metadata()
        return payload

    def list_watermarks(self, spider_id: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        paths = self.watermarks_dir.rglob("*.json") if spider_id is None else (self.watermarks_dir / _safe_name(spider_id)).glob("*.json")
        for path in sorted(paths):
            items.append(self._load_json(path))
        return items

    def save_checkpoint(self, task_id: str, state: dict[str, Any]) -> Path:
        payload = dict(state)
        payload.setdefault("task_id", task_id)
        payload["updated_at"] = _now()
        path = self._checkpoint_path(task_id)
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def load_checkpoint(self, task_id: str) -> dict[str, Any]:
        return self._load_json(self._checkpoint_path(task_id))

    def list_checkpoints(self, spider_id: str | None = None) -> list[dict[str, Any]]:
        checkpoints: list[dict[str, Any]] = []
        for path in sorted(self.checkpoints_dir.glob("*.json")):
            payload = self._load_json(path)
            if spider_id is None or payload.get("spider_id") == spider_id:
                checkpoints.append(payload)
        return checkpoints

    def clear_checkpoint(self, task_id: str) -> bool:
        path = self._checkpoint_path(task_id)
        if not path.exists():
            return False
        path.unlink()
        self._touch_metadata()
        return True

    def save_schedule(self, schedule: SchedulerJob | dict[str, Any]) -> Path:
        job = schedule if isinstance(schedule, SchedulerJob) else SchedulerJob.from_dict(schedule)
        path = self.schedules_dir / f"{_safe_name(job.id)}.json"
        self._atomic_write_json(path, job.to_dict())
        self._touch_metadata()
        return path

    def get_schedule(self, schedule_id: str) -> SchedulerJob:
        path = self.schedules_dir / f"{_safe_name(schedule_id)}.json"
        return SchedulerJob.from_dict(self._load_json(path))

    def list_schedules(self, enabled: bool | None = None, spider_id: str | None = None) -> list[dict[str, Any]]:
        schedules: list[dict[str, Any]] = []
        for path in sorted(self.schedules_dir.glob("*.json")):
            payload = self._load_json(path)
            if spider_id is not None and payload.get("spider_id") != spider_id:
                continue
            if enabled is not None:
                is_enabled = payload.get("status") == "enabled" and bool(payload.get("scheduler", {}).get("enabled", True))
                if is_enabled is not enabled:
                    continue
            schedules.append(payload)
        return schedules

    def update_schedule(self, schedule_id: str, patch: dict[str, Any]) -> SchedulerJob:
        job = self.get_schedule(schedule_id)
        payload = job.to_dict()
        for key, value in patch.items():
            if key in payload:
                payload[key] = value
        payload["updated_at"] = _now()
        updated = SchedulerJob.from_dict(payload)
        self.save_schedule(updated)
        return updated

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        source = self.schedules_dir / f"{_safe_name(schedule_id)}.json"
        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = self.dead_letters_dir / "schedules"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{_safe_name(schedule_id)}-{_compact_time()}.json"
        self._replace_file(source, target)
        self._touch_metadata()
        return {"deleted": schedule_id, "moved_to": str(target)}

    def record_scheduler_run(self, run: SchedulerRun | dict[str, Any]) -> Path:
        scheduler_run = run if isinstance(run, SchedulerRun) else SchedulerRun.from_dict(run)
        schedule_dir = self.scheduler_runs_dir / _safe_name(scheduler_run.schedule_id)
        path = schedule_dir / f"{_safe_name(scheduler_run.id)}.json"
        self._atomic_write_json(path, scheduler_run.to_dict())
        self._touch_metadata()
        return path

    def list_scheduler_runs(self, schedule_id: str | None = None, spider_id: str | None = None) -> list[dict[str, Any]]:
        root = self.scheduler_runs_dir / _safe_name(schedule_id) if schedule_id else self.scheduler_runs_dir
        paths = root.glob("*.json") if schedule_id else root.rglob("*.json")
        runs: list[dict[str, Any]] = []
        for path in sorted(paths):
            payload = self._load_json(path)
            if spider_id is None or payload.get("spider_id") == spider_id:
                runs.append(payload)
        return runs

    def enqueue_job(self, job: WorkerJob | dict[str, Any]) -> WorkerJob:
        item = job if isinstance(job, WorkerJob) else WorkerJob.from_dict(job)
        now = _now()
        item.status = "queued"
        item.created_at = item.created_at or now
        item.updated_at = now
        item.max_attempts = max(1, int(item.max_attempts or 1))
        item.priority = int(item.priority or 0)
        with self._queue_lock():
            if self._find_job_path(item.job_id) is not None:
                raise StorageError("worker job already exists", job_id=item.job_id)
            self._write_job(item, self._job_path(item.job_id, item.status))
        self._touch_metadata()
        return item

    def save_worker_job(self, job: WorkerJob | dict[str, Any]) -> WorkerJob:
        item = job if isinstance(job, WorkerJob) else WorkerJob.from_dict(job)
        item.updated_at = _now()
        with self._queue_lock():
            current = self._find_job_path(item.job_id)
            target = self._job_path(item.job_id, item.status)
            self._write_job(item, target)
            if current is not None and current != target:
                current.unlink(missing_ok=True)
        self._touch_metadata()
        return item

    def get_job(self, job_id: str) -> WorkerJob:
        path = self._find_job_path(job_id)
        if path is None:
            raise FileNotFoundError(job_id)
        return WorkerJob.from_dict(self._load_json(path))

    def list_jobs(self, status: str | None = None, source: str | None = None, spider_id: str | None = None) -> list[dict[str, Any]]:
        statuses = [_job_status_key(status)] if status else list(_QUEUE_STATUS_DIRS)
        jobs: list[dict[str, Any]] = []
        for item_status in statuses:
            directory = self._queue_status_dir(item_status)
            for path in sorted(directory.glob("*.json")):
                payload = self._load_json(path)
                if source is not None and payload.get("source") != source:
                    continue
                if spider_id is not None and payload.get("spider_id") != spider_id:
                    continue
                jobs.append(payload)
        return sorted(jobs, key=lambda item: (item.get("created_at") or "", item.get("job_id") or ""))

    def claim_job(self, worker_id: str, now: str | datetime | None = None, *, lease_seconds: int = 300) -> WorkerJob | None:
        now_dt = _coerce_datetime(now)
        with self._queue_lock():
            self._requeue_expired_leases_unlocked(now_dt)
            candidates = []
            for path in sorted(self.queue_queued_dir.glob("*.json")):
                job = WorkerJob.from_dict(self._load_json(path))
                run_after = _optional_datetime(job.run_after)
                if run_after is not None and run_after > now_dt:
                    continue
                candidates.append((job, path))
            if not candidates:
                return None
            candidates.sort(key=lambda item: (-int(item[0].priority or 0), _sort_datetime(item[0].run_after), _sort_datetime(item[0].created_at), item[0].job_id))
            job, source = candidates[0]
            job.status = "running"
            job.lease_owner = worker_id
            job.lease_expires_at = _format_datetime(now_dt + timedelta(seconds=max(1, lease_seconds)))
            job.heartbeat_at = _format_datetime(now_dt)
            job.started_at = job.started_at or _format_datetime(now_dt)
            job.updated_at = _format_datetime(now_dt)
            job.attempt = int(job.attempt or 0) + 1
            target = self._job_path(job.job_id, job.status)
            self._write_job(job, target)
            source.unlink(missing_ok=True)
        self._touch_metadata()
        return job

    def heartbeat_job(self, job_id: str, worker_id: str, now: str | datetime | None = None, *, lease_seconds: int = 300) -> WorkerJob:
        now_dt = _coerce_datetime(now)
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            if job.lease_owner != worker_id:
                raise StorageError("worker does not own job lease", job_id=job_id, worker_id=worker_id)
            job.heartbeat_at = _format_datetime(now_dt)
            job.lease_expires_at = _format_datetime(now_dt + timedelta(seconds=max(1, lease_seconds)))
            job.updated_at = _format_datetime(now_dt)
            job.metadata["heartbeat_count"] = int(job.metadata.get("heartbeat_count", 0)) + 1
            self._write_job(job, source)
        self._touch_metadata()
        return job

    def complete_job(self, job_id: str, worker_id: str, result: dict[str, Any] | None = None) -> WorkerJob:
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            self._require_lease_owner(job, worker_id)
            now = _now()
            payload = result or {}
            job.task_id = payload.get("task_id", job.task_id)
            job.status = "succeeded"
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.finished_at = now
            job.updated_at = now
            job.error = None
            job.metadata["result"] = payload
            target = self._job_path(job.job_id, job.status)
            self._write_job(job, target)
            source.unlink(missing_ok=True)
        self._touch_metadata()
        return job

    def fail_job(self, job_id: str, worker_id: str, error: dict[str, Any] | str, retry: bool = True) -> WorkerJob:
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            self._require_lease_owner(job, worker_id)
            now = _now()
            job.error = error if isinstance(error, dict) else {"message": str(error)}
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.updated_at = now
            if retry and int(job.attempt or 0) < max(1, int(job.max_attempts or 1)):
                job.status = "queued"
                job.started_at = None
                job.finished_at = None
                job.warnings.append({"type": "worker_retry", "attempt": job.attempt, "max_attempts": job.max_attempts})
            else:
                job.status = "dead_letter"
                job.finished_at = now
                job.warnings.append({"type": "dead_letter", "attempt": job.attempt, "max_attempts": job.max_attempts})
            target = self._job_path(job.job_id, job.status)
            self._write_job(job, target)
            source.unlink(missing_ok=True)
        self._touch_metadata()
        return job

    def cancel_job(self, job_id: str) -> WorkerJob:
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            now = _now()
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = now
                job.updated_at = now
                target = self._job_path(job.job_id, job.status)
                self._write_job(job, target)
                source.unlink(missing_ok=True)
            else:
                job.warnings.append({"type": "cancel_ignored", "message": f"cannot cancel job in {job.status} state"})
                job.updated_at = now
                self._write_job(job, source)
        self._touch_metadata()
        return job

    def cancel_claimed_job(self, job_id: str, worker_id: str | None = None, reason: str | None = None) -> WorkerJob:
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            if worker_id is not None:
                self._require_lease_owner(job, worker_id)
            now = _now()
            job.status = "cancelled"
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.finished_at = now
            job.updated_at = now
            job.error = {"type": "cancelled", "message": reason or "job cancellation requested"}
            target = self._job_path(job.job_id, job.status)
            self._write_job(job, target)
            source.unlink(missing_ok=True)
        self._touch_metadata()
        return job

    def requeue_expired_leases(self, now: str | datetime | None = None) -> list[dict[str, Any]]:
        now_dt = _coerce_datetime(now)
        with self._queue_lock():
            jobs = self._requeue_expired_leases_unlocked(now_dt)
        if jobs:
            self._touch_metadata()
        return [job.to_dict() for job in jobs]

    def move_to_dead_letter(self, job_id: str, reason: str) -> WorkerJob:
        with self._queue_lock():
            job, source = self._load_job_with_path(job_id)
            now = _now()
            job.status = "dead_letter"
            job.error = {"message": reason}
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.finished_at = now
            job.updated_at = now
            target = self._job_path(job.job_id, job.status)
            self._write_job(job, target)
            source.unlink(missing_ok=True)
        self._touch_metadata()
        return job

    def get_queue_stats(self) -> dict[str, Any]:
        counts = {status: len(list(self._queue_status_dir(status).glob("*.json"))) for status in _QUEUE_STATUS_DIRS}
        runs = self.list_worker_runs()
        workers = self.list_workers()
        due = 0
        delayed = 0
        now = _coerce_datetime(None)
        for payload in self.list_jobs(status="queued"):
            run_after = _optional_datetime(payload.get("run_after"))
            if run_after is not None and run_after > now:
                delayed += 1
            else:
                due += 1
        worker_stats = [WorkerState.from_dict(item).stats for item in workers]
        heartbeat_count = sum(int(item.get("heartbeat_count", 0)) for item in worker_stats)
        heartbeat_count += sum(int(item.get("metadata", {}).get("heartbeat_count", 0)) for item in self.list_jobs())
        concurrency_peak = max([0, *[int(item.get("concurrency_peak", 0)) for item in worker_stats]])
        return {
            "counts": counts,
            "total_jobs": sum(counts.values()),
            "due_jobs": due,
            "delayed_jobs": delayed,
            "workers": len(workers),
            "worker_runs": len(runs),
            "claimed_jobs": sum(1 for item in runs if item.get("job_id")),
            "succeeded_jobs": counts["succeeded"],
            "failed_jobs": counts["failed"],
            "retried_jobs": sum(1 for item in runs if item.get("status") == "retried"),
            "dead_letter_jobs": counts["dead_letter"],
            "heartbeat_count": heartbeat_count,
            "concurrency_peak": concurrency_peak,
        }

    def save_worker_state(self, worker_state: WorkerState | dict[str, Any]) -> WorkerState:
        state = worker_state if isinstance(worker_state, WorkerState) else WorkerState.from_dict(worker_state)
        path = self.workers_dir / f"{_safe_name(state.worker_id)}.json"
        self._atomic_write_json(path, state.to_dict())
        self._touch_metadata()
        return state

    def get_worker_state(self, worker_id: str) -> WorkerState:
        return WorkerState.from_dict(self._load_json(self.workers_dir / f"{_safe_name(worker_id)}.json"))

    def list_workers(self) -> list[dict[str, Any]]:
        return [self._load_json(path) for path in sorted(self.workers_dir.glob("*.json"))]

    def record_worker_run(self, run: WorkerRunResult | dict[str, Any]) -> Path:
        item = run if isinstance(run, WorkerRunResult) else WorkerRunResult.from_dict(run)
        worker_dir = self.worker_runs_dir / _safe_name(item.worker_id)
        path = worker_dir / f"{_safe_name(item.run_id)}.json"
        self._atomic_write_json(path, item.to_dict())
        self._touch_metadata()
        return path

    def list_worker_runs(self, worker_id: str | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
        root = self.worker_runs_dir / _safe_name(worker_id) if worker_id else self.worker_runs_dir
        paths = root.glob("*.json") if worker_id else root.rglob("*.json")
        runs: list[dict[str, Any]] = []
        for path in sorted(paths):
            payload = self._load_json(path)
            if job_id is None or payload.get("job_id") == job_id:
                runs.append(payload)
        return runs

    def record_lifecycle_event(self, event: LifecycleEvent | dict[str, Any]) -> Path:
        item = event if isinstance(event, LifecycleEvent) else LifecycleEvent.from_dict(event)
        directory = self._lifecycle_event_dir(item.target_type, item.target_id)
        path = directory / f"{_safe_name(item.event_id)}.json"
        self._atomic_write_json(path, item.to_dict())
        self._touch_metadata()
        self._record_lifecycle_observability(item)
        return path

    def list_lifecycle_events(self, target_type: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        if target_type and target_id:
            paths = self._lifecycle_event_dir(target_type, target_id).glob("*.json")
        elif target_type:
            paths = self._lifecycle_event_root(target_type).rglob("*.json")
        else:
            paths = self.lifecycle_events_dir.rglob("*.json")
        events = [self._load_json(path) for path in sorted(paths)]
        return sorted(events, key=lambda item: (item.get("created_at") or "", item.get("event_id") or ""))

    def write_lifecycle_signal(self, target_type: str, target_id: str, payload: dict[str, Any]) -> Path:
        data = dict(payload)
        data.setdefault("target_type", target_type)
        data.setdefault("target_id", target_id)
        data.setdefault("created_at", _now())
        path = self._lifecycle_signal_path(target_type, target_id)
        self._atomic_write_json(path, data)
        self._touch_metadata()
        return path

    def read_lifecycle_signal(self, target_type: str, target_id: str) -> dict[str, Any]:
        path = self._lifecycle_signal_path(target_type, target_id)
        if not path.exists():
            return {}
        return self._load_json(path)

    def clear_lifecycle_signal(self, target_type: str, target_id: str) -> bool:
        path = self._lifecycle_signal_path(target_type, target_id)
        if not path.exists():
            return False
        path.unlink()
        self._touch_metadata()
        return True

    def save_session_profile(self, profile: dict[str, Any]) -> Path:
        profile_id = str(profile.get("profile_id") or profile.get("id") or "")
        if not profile_id:
            raise StorageError("session profile requires profile_id")
        path = self._session_profile_path(profile_id)
        payload = dict(profile)
        payload["profile_id"] = profile_id
        payload.setdefault("created_at", _now())
        payload["updated_at"] = _now()
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def get_session_profile(self, profile_id: str) -> dict[str, Any]:
        return self._load_json(self._session_profile_path(profile_id))

    def list_session_profiles(self) -> list[dict[str, Any]]:
        return [self._load_json(path) for path in sorted(self.session_profiles_dir.glob("*.json"))]

    def delete_session_profile(self, profile_id: str) -> dict[str, Any]:
        removed: list[str] = []
        for path in [
            self._session_profile_path(profile_id),
            self._session_cookies_path(profile_id),
            self._session_storage_state_path(profile_id),
            self._session_account_path(profile_id),
        ]:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        self._touch_metadata()
        return {"profile_id": profile_id, "removed": removed}

    def save_cookies(self, profile_id: str, cookies: dict[str, str]) -> Path:
        path = self._session_cookies_path(profile_id)
        payload = {str(key): str(value) for key, value in cookies.items()}
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def load_cookies(self, profile_id: str) -> dict[str, str]:
        payload = self._load_json(self._session_cookies_path(profile_id))
        return {str(key): str(value) for key, value in payload.items()}

    def merge_cookies(self, profile_id: str, cookies: dict[str, str]) -> dict[str, str]:
        with self._file_lock(self._session_cookies_path(profile_id)):
            try:
                current = self.load_cookies(profile_id)
            except FileNotFoundError:
                current = {}
            current.update({str(key): str(value) for key, value in cookies.items()})
            self._atomic_write_json(self._session_cookies_path(profile_id), current)
        self._touch_metadata()
        return current

    def save_storage_state(self, profile_id: str, state: dict[str, Any]) -> Path:
        path = self._session_storage_state_path(profile_id)
        self._atomic_write_json(path, state)
        self._touch_metadata()
        return path

    def load_storage_state(self, profile_id: str) -> dict[str, Any]:
        return self._load_json(self._session_storage_state_path(profile_id))

    def save_session_account(self, profile_id: str, account: dict[str, Any]) -> Path:
        path = self._session_account_path(profile_id)
        payload = dict(account)
        payload.setdefault("profile_id", profile_id)
        payload["updated_at"] = _now()
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def list_session_accounts(self) -> list[dict[str, Any]]:
        return [self._load_json(path) for path in sorted(self.session_accounts_dir.glob("*.json"))]

    def record_session_event(self, event: dict[str, Any]) -> Path:
        profile_id = str(event.get("profile_id") or "default")
        event_id = str(event.get("event_id") or uuid.uuid4().hex)
        payload = dict(event)
        payload["event_id"] = event_id
        payload["profile_id"] = profile_id
        payload.setdefault("created_at", _now())
        path = self._session_events_dir(profile_id) / f"{_safe_name(event_id)}.json"
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def list_session_events(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        paths = self._session_events_dir(profile_id).glob("*.json") if profile_id else self.session_events_dir.rglob("*.json")
        events = [self._load_json(path) for path in sorted(paths)]
        return sorted(events, key=lambda item: (item.get("created_at") or "", item.get("event_id") or ""))

    def append_log(self, scope: str, target_id: str, event: dict[str, Any]) -> Path:
        path = self._observability_log_path(scope, target_id)
        payload = dict(event)
        payload.setdefault("event_id", uuid.uuid4().hex)
        payload.setdefault("timestamp", _now())
        payload.setdefault("scope", _observability_scope_key(scope))
        payload.setdefault("target_id", target_id)
        self._append_jsonl(path, payload)
        self._touch_metadata()
        return path

    def iter_logs(
        self,
        scope: str | None = None,
        target_id: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if scope and target_id:
            paths = [self._observability_log_path(scope, target_id)]
        elif scope:
            paths = sorted(self._observability_log_dir(scope).glob("*.jsonl"))
        else:
            paths = sorted(self.observability_logs_dir.rglob("*.jsonl"))
        selected_level = level.upper() if level else None
        logs: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            for item in self._read_jsonl(path, strict=True):
                if selected_level and str(item.get("level", "")).upper() != selected_level:
                    continue
                if target_id and not scope and target_id not in {
                    str(item.get("target_id")),
                    str(item.get("task_id")),
                    str(item.get("job_id")),
                    str(item.get("schedule_id")),
                    str(item.get("scheduler_run_id")),
                }:
                    continue
                logs.append(item)
        logs = sorted(logs, key=lambda item: (item.get("timestamp") or "", item.get("event_id") or ""))
        start = max(0, int(offset or 0))
        end = None if limit is None else start + max(0, int(limit))
        return logs[start:end]

    def record_metric(self, scope: str, target_id: str, metric: dict[str, Any]) -> Path:
        path = self._observability_metric_path(scope, target_id)
        payload = dict(metric)
        payload.setdefault("metric_id", uuid.uuid4().hex)
        payload.setdefault("timestamp", _now())
        payload.setdefault("scope", _observability_scope_key(scope))
        payload.setdefault("target_id", target_id)
        self._append_jsonl(path, payload)
        self._touch_metadata()
        return path

    def get_metrics(self, scope: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        if scope and target_id:
            paths = [self._observability_metric_path(scope, target_id)]
        elif scope:
            paths = sorted(self._observability_metric_dir(scope).glob("*.jsonl"))
        else:
            paths = sorted(self.observability_metrics_dir.rglob("*.jsonl"))
        metrics: list[dict[str, Any]] = []
        for path in paths:
            if path.exists():
                metrics.extend(self._read_jsonl(path, strict=True))
        return sorted(metrics, key=lambda item: (item.get("timestamp") or "", item.get("metric_id") or ""))

    def summarize_metrics(self, scope: str | None = None, target_id: str | None = None) -> dict[str, Any]:
        counters: dict[str, float] = {}
        gauges: dict[str, float] = {}
        timers: dict[str, dict[str, float]] = {}
        for metric in self.get_metrics(scope=scope, target_id=target_id):
            name = str(metric.get("name") or "")
            if not name:
                continue
            value = float(metric.get("value") or 0)
            kind = str(metric.get("kind") or "counter")
            if kind == "gauge":
                gauges[name] = value
            elif kind == "timer":
                current = timers.setdefault(name, {"count": 0, "sum": 0, "min": value, "max": value, "avg": 0})
                current["count"] += 1
                current["sum"] += value
                current["min"] = min(current["min"], value)
                current["max"] = max(current["max"], value)
                current["avg"] = current["sum"] / current["count"]
            else:
                counters[name] = counters.get(name, 0) + value
        return {
            "scope": scope,
            "target_id": target_id,
            "counters": counters,
            "gauges": gauges,
            "timers": timers,
        }

    def create_run_report(self, target_type: str, target_id: str, report: dict[str, Any]) -> Path:
        path = self._observability_report_path(target_type, target_id)
        payload = dict(report)
        payload.setdefault("target_type", _observability_report_key(target_type))
        payload.setdefault("target_id", target_id)
        payload.setdefault("created_at", _now())
        self._atomic_write_json(path, payload)
        self._touch_metadata()
        return path

    def get_run_report(self, target_type: str, target_id: str) -> dict[str, Any]:
        return self._load_json(self._observability_report_path(target_type, target_id))

    def list_run_reports(self, target_type: str | None = None) -> list[dict[str, Any]]:
        if target_type:
            paths = sorted(self._observability_report_dir(target_type).glob("*.json"))
        else:
            paths = sorted(self.observability_reports_dir.rglob("*.json"))
        reports = [self._load_json(path) for path in paths]
        return sorted(reports, key=lambda item: (item.get("created_at") or "", item.get("target_id") or ""))

    def create_trace(self, trace: dict[str, Any]) -> Path:
        trace_id = str(trace.get("trace_id") or uuid.uuid4().hex)
        path = self._observability_trace_path(trace_id)
        if not path.exists():
            payload = dict(trace)
            payload["trace_id"] = trace_id
            payload.setdefault("timestamp", _now())
            payload.setdefault("event_type", "trace_created")
            self._append_jsonl(path, payload)
            self._touch_metadata()
        return path

    def append_trace_event(self, trace_id: str, event: dict[str, Any]) -> Path:
        path = self._observability_trace_path(trace_id)
        payload = dict(event)
        payload["trace_id"] = trace_id
        payload.setdefault("event_id", uuid.uuid4().hex)
        payload.setdefault("timestamp", _now())
        self._append_jsonl(path, payload)
        self._touch_metadata()
        return path

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        path = self._observability_trace_path(trace_id)
        if not path.exists():
            raise FileNotFoundError(trace_id)
        events = self._read_jsonl(path, strict=True)
        return {"trace_id": trace_id, "events": sorted(events, key=lambda item: (item.get("timestamp") or "", item.get("event_id") or ""))}

    def create_export(self, manifest: dict[str, Any]) -> dict[str, Any]:
        export_id = str(manifest.get("export_id") or uuid.uuid4().hex)
        payload = dict(manifest)
        payload["export_id"] = export_id
        payload.setdefault("created_at", _now())
        payload.setdefault("status", "success")
        self._atomic_write_json(self._export_manifest_path(export_id), payload)
        self._touch_metadata()
        return payload

    def get_export(self, export_id: str) -> dict[str, Any]:
        return self._load_json(self._export_manifest_path(export_id))

    def list_exports(self, source_type: str | None = None, source_id: str | None = None) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.exports_manifests_dir.glob("*.json")):
            payload = self._load_json(path)
            if source_type is not None and payload.get("source_type") != source_type:
                continue
            if source_id is not None and payload.get("source_id") != source_id:
                continue
            manifests.append(payload)
        return sorted(manifests, key=lambda item: (item.get("created_at") or "", item.get("export_id") or ""))

    def delete_export(self, export_id: str) -> dict[str, Any]:
        manifest_path = self._export_manifest_path(export_id)
        manifest = self._load_json(manifest_path)
        removed: list[str] = []
        export_path = Path(str(manifest.get("path") or ""))
        if export_path.exists() and export_path.is_file():
            export_path.unlink()
            removed.append(str(export_path))
        manifest_path.unlink()
        removed.append(str(manifest_path))
        self._touch_metadata()
        return {"export_id": export_id, "removed": removed}

    def check_storage(self) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        stats: dict[str, Any] = {
            "spiders": 0,
            "tasks": 0,
            "result_files": 0,
            "hash_files": 0,
            "watermarks": 0,
            "checkpoints": 0,
            "schedules": 0,
            "scheduler_runs": 0,
            "queue_jobs": 0,
            "workers": 0,
            "worker_runs": 0,
            "lifecycle_events": 0,
            "lifecycle_signals": 0,
            "session_profiles": 0,
            "session_cookies": 0,
            "session_storage_states": 0,
            "session_accounts": 0,
            "session_events": 0,
            "observability_logs": 0,
            "observability_metrics": 0,
            "observability_reports": 0,
            "observability_traces": 0,
            "export_files": 0,
            "export_manifests": 0,
            "tmp_files": 0,
            "snapshots": 0,
        }

        for directory in self.required_directories:
            if not directory.exists():
                errors.append(_issue(directory, "required directory is missing"))

        if not self.metadata_path.exists():
            errors.append(_issue(self.metadata_path, "storage_metadata.json is missing"))
        else:
            try:
                metadata = self._load_json(self.metadata_path)
                if metadata.get("storage_version") != STORAGE_VERSION:
                    errors.append(_issue(self.metadata_path, f"unsupported storage_version: {metadata.get('storage_version')}"))
            except CorruptedFileError as exc:
                errors.append(_issue(exc.path or self.metadata_path, str(exc)))

        for path in sorted(self.spiders_dir.glob("*.json")):
            stats["spiders"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.tasks_dir.glob("*.json")):
            stats["tasks"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.results_dir.glob("*.jsonl")):
            stats["result_files"] += 1
            self._check_jsonl_file(path, errors)
        for path in sorted(self.hashes_dir.rglob("*.txt")):
            stats["hash_files"] += 1
            self._check_hash_file(path, errors)
        for path in sorted(self.watermarks_dir.rglob("*.json")):
            stats["watermarks"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.checkpoints_dir.glob("*.json")):
            stats["checkpoints"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.schedules_dir.glob("*.json")):
            stats["schedules"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.scheduler_runs_dir.rglob("*.json")):
            stats["scheduler_runs"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.queue_dir.rglob("*.json")):
            stats["queue_jobs"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.workers_dir.glob("*.json")):
            stats["workers"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.worker_runs_dir.rglob("*.json")):
            stats["worker_runs"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.lifecycle_events_dir.rglob("*.json")):
            stats["lifecycle_events"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.lifecycle_signals_dir.rglob("*.json")):
            stats["lifecycle_signals"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.session_profiles_dir.glob("*.json")):
            stats["session_profiles"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.session_cookies_dir.glob("*.json")):
            stats["session_cookies"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.session_storage_states_dir.glob("*.json")):
            stats["session_storage_states"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.session_accounts_dir.glob("*.json")):
            stats["session_accounts"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.session_events_dir.rglob("*.json")):
            stats["session_events"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.observability_logs_dir.rglob("*.jsonl")):
            stats["observability_logs"] += 1
            self._check_jsonl_file(path, errors)
        for path in sorted(self.observability_metrics_dir.rglob("*.jsonl")):
            stats["observability_metrics"] += 1
            self._check_jsonl_file(path, errors)
        for path in sorted(self.observability_reports_dir.rglob("*.json")):
            stats["observability_reports"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.observability_traces_dir.glob("*.jsonl")):
            stats["observability_traces"] += 1
            self._check_jsonl_file(path, errors)
        for path in sorted(self.exports_files_dir.glob("*")):
            if path.is_file():
                stats["export_files"] += 1
        for path in sorted(self.exports_manifests_dir.glob("*.json")):
            stats["export_manifests"] += 1
            self._check_json_file(path, errors)
        for path in sorted(self.tmp_dir.glob("*")):
            if path.is_file():
                stats["tmp_files"] += 1
                warnings.append(_issue(path, "temporary file remains"))
        for path in sorted(self.locks_dir.glob("*.lock")):
            if _is_expired(path):
                warnings.append(_issue(path, "lock file appears expired"))
        for path in sorted(self.snapshots_dir.glob("*")):
            if path.is_dir():
                stats["snapshots"] += 1
        if not self.dead_letters_dir.exists():
            warnings.append(_issue(self.dead_letters_dir, "dead_letters directory is missing"))

        return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}

    def repair_storage(self, dry_run: bool = True) -> dict[str, Any]:
        actions: list[RepairAction] = []
        for directory in self.required_directories:
            if not directory.exists():
                actions.append(RepairAction("mkdir", str(directory), reason="required directory missing"))
                if not dry_run:
                    directory.mkdir(parents=True, exist_ok=True)

        corrupted_dir = self.dead_letters_dir / "corrupted"
        for path in [
            *self.spiders_dir.glob("*.json"),
            *self.tasks_dir.glob("*.json"),
            *self.schedules_dir.glob("*.json"),
            *self.scheduler_runs_dir.rglob("*.json"),
            *self.queue_dir.rglob("*.json"),
            *self.workers_dir.glob("*.json"),
            *self.worker_runs_dir.rglob("*.json"),
            *self.lifecycle_events_dir.rglob("*.json"),
            *self.lifecycle_signals_dir.rglob("*.json"),
            *self.sessions_dir.rglob("*.json"),
            *self.observability_reports_dir.rglob("*.json"),
            *self.exports_manifests_dir.glob("*.json"),
        ]:
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                target = corrupted_dir / f"{path.stem}-{_compact_time()}{path.suffix}"
                actions.append(RepairAction("move_corrupted", str(path), str(target), "invalid JSON"))
                if not dry_run:
                    corrupted_dir.mkdir(parents=True, exist_ok=True)
                    self._replace_file(path, target)

        for path in self.tmp_dir.glob("*"):
            if path.is_file():
                actions.append(RepairAction("remove_tmp", str(path), reason="temporary file remains"))
                if not dry_run:
                    path.unlink(missing_ok=True)

        return {"dry_run": dry_run, "actions": [action.to_dict() for action in actions]}

    def create_snapshot(self, name: str | None = None, include_results: bool = False) -> dict[str, Any]:
        snapshot_id = f"{_compact_time()}-{_safe_name(name or uuid.uuid4().hex[:8])}"
        target = self.snapshots_dir / snapshot_id
        if target.exists():
            raise SnapshotError("snapshot already exists", path=target, snapshot_id=snapshot_id)
        target.mkdir(parents=True)
        included = [
            "spiders",
            "tasks",
            "hashes",
            "watermarks",
            "checkpoints",
            "schedules",
            "queue",
            "workers",
            "lifecycle_events",
            "lifecycle_signals",
            "sessions",
            "observability",
            "exports",
            "storage_metadata.json",
        ]
        if include_results:
            included.append("results")

        for relative in included:
            source = self.root / relative
            destination = target / relative
            if source.is_dir():
                shutil.copytree(source, destination)
            elif source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        file_count, total_bytes = _tree_stats(target)
        manifest = {
            "snapshot_id": snapshot_id,
            "name": name,
            "created_at": _now(),
            "included_paths": included,
            "observability": {
                "logs": "jsonl",
                "metrics": "jsonl",
                "reports": "json",
                "traces": "jsonl",
            },
            "exports": {
                "files": "json/jsonl/csv/xlsx",
                "manifests": "json",
            },
            "file_count": file_count,
            "total_bytes": total_bytes,
        }
        self._atomic_write_json(target / "manifest.json", manifest)
        return manifest

    def list_snapshots(self) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for manifest_path in sorted(self.snapshots_dir.glob("*/manifest.json")):
            try:
                snapshots.append(self._load_json(manifest_path))
            except CorruptedFileError:
                snapshots.append({"snapshot_id": manifest_path.parent.name, "corrupted": True})
        return snapshots

    def restore_snapshot(self, snapshot_id: str, dry_run: bool = True) -> dict[str, Any]:
        source = self.snapshots_dir / _safe_name(snapshot_id)
        manifest_path = source / "manifest.json"
        if not manifest_path.exists():
            raise SnapshotError("snapshot manifest not found", path=manifest_path, snapshot_id=snapshot_id)
        manifest = self._load_json(manifest_path)
        actions: list[dict[str, Any]] = []
        for relative in manifest.get("included_paths", []):
            if relative == "storage_metadata.json":
                destination = self.metadata_path
            else:
                destination = self.root / relative
            snapshot_path = source / relative
            actions.append({"action": "restore", "source": str(snapshot_path), "target": str(destination)})
            if not dry_run and snapshot_path.exists():
                if destination.exists():
                    backup = self.dead_letters_dir / "restore_backups" / f"{destination.name}-{_compact_time()}"
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    self._replace_file(destination, backup) if destination.is_file() else shutil.move(str(destination), str(backup))
                if snapshot_path.is_dir():
                    shutil.copytree(snapshot_path, destination)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snapshot_path, destination)
        return {"dry_run": dry_run, "snapshot_id": manifest["snapshot_id"], "actions": actions}

    def read_storage_metadata(self) -> dict[str, Any]:
        metadata = self._load_json(self.metadata_path)
        if metadata.get("storage_version") != STORAGE_VERSION:
            raise CorruptedFileError("unsupported storage version", path=self.metadata_path, storage_version=metadata.get("storage_version"))
        return metadata

    def _read_hashes(self, spider_id: str) -> set[str]:
        return self.load_hashes(spider_id)

    def _ensure_directories(self) -> None:
        for directory in self.required_directories:
            directory.mkdir(parents=True, exist_ok=True)

    def _ensure_metadata(self) -> None:
        if self.metadata_path.exists():
            return
        now = _now()
        metadata = {
            "storage_version": STORAGE_VERSION,
            "created_at": now,
            "updated_at": now,
            "platform": platform.platform(),
            "features": {
                "atomic_write": True,
                "file_lock": True,
                "snapshot": True,
                "incremental": True,
                "scheduler": True,
                "worker_queue": True,
                "lifecycle": True,
                "session": True,
                "observability": True,
                "exporter": True,
            },
        }
        self._atomic_write_json(self.metadata_path, metadata)

    def _touch_metadata(self) -> None:
        with self._file_lock(self.metadata_path):
            try:
                metadata = self.read_storage_metadata()
            except StorageError:
                return
            metadata["updated_at"] = _now()
            self._atomic_write_json(self.metadata_path, metadata)

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        self._atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _atomic_write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        temp = self.tmp_dir / f"{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_file(temp, path)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            if isinstance(exc, StorageError):
                raise
            raise AtomicWriteError(str(exc), path=path) from exc

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with self._file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def _replace_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)

    @contextmanager
    def _file_lock(self, target: Path) -> Iterator[None]:
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.locks_dir / _lock_name(target)
        deadline = time.time() + self.lock_timeout_seconds
        fd: int | None = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {_now()} {target}\n".encode("utf-8"))
            except (FileExistsError, PermissionError):
                if _is_expired(lock_path):
                    lock_path.unlink(missing_ok=True)
                    continue
                if time.time() >= deadline:
                    raise FileLockError("timed out waiting for file lock", path=lock_path)
                time.sleep(0.01)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
            lock_path.unlink(missing_ok=True)

    def _load_json(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorruptedFileError(str(exc), path=path, line=exc.lineno, column=exc.colno) from exc

    def _iter_jsonl(self, path: Path, *, strict: bool) -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    if strict:
                        raise CorruptedFileError(str(exc), path=path, line=line_number) from exc

    def _read_jsonl(self, path: Path, *, strict: bool) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for record in self._iter_jsonl(path, strict=strict):
            records.append(record)
        return records

    def _hash_path(self, dataset: str, scope: str) -> Path:
        return self.hashes_dir / _safe_name(scope) / f"{_safe_name(dataset)}.txt"

    def _watermark_path(self, spider_id: str, dataset: str) -> Path:
        return self.watermarks_dir / _safe_name(spider_id) / f"{_safe_name(dataset)}.json"

    def _checkpoint_path(self, task_id: str) -> Path:
        return self.checkpoints_dir / f"{_safe_name(task_id)}.json"

    @contextmanager
    def _queue_lock(self) -> Iterator[None]:
        with self._file_lock(self.queue_dir / "queue-state"):
            yield

    def _queue_status_dir(self, status: str) -> Path:
        key = _job_status_key(status)
        return {
            "queued": self.queue_queued_dir,
            "leased": self.queue_leased_dir,
            "running": self.queue_running_dir,
            "paused": self.queue_paused_dir,
            "retrying": self.queue_retrying_dir,
            "cancelling": self.queue_cancelling_dir,
            "succeeded": self.queue_succeeded_dir,
            "failed": self.queue_failed_dir,
            "cancelled": self.queue_cancelled_dir,
            "dead_letter": self.queue_dead_letters_dir,
        }[key]

    def _job_path(self, job_id: str, status: str) -> Path:
        return self._queue_status_dir(status) / f"{_safe_name(job_id)}.json"

    def _find_job_path(self, job_id: str) -> Path | None:
        safe = f"{_safe_name(job_id)}.json"
        for status in _QUEUE_STATUS_DIRS:
            path = self._queue_status_dir(status) / safe
            if path.exists():
                return path
        return None

    def _load_job_with_path(self, job_id: str) -> tuple[WorkerJob, Path]:
        path = self._find_job_path(job_id)
        if path is None:
            raise FileNotFoundError(job_id)
        return WorkerJob.from_dict(self._load_json(path)), path

    def _write_job(self, job: WorkerJob, path: Path) -> None:
        self._atomic_write_json(path, job.to_dict())

    def _lifecycle_event_root(self, target_type: str) -> Path:
        key = _lifecycle_target_key(target_type)
        return {
            "task": self.lifecycle_task_events_dir,
            "job": self.lifecycle_job_events_dir,
            "scheduler_run": self.lifecycle_scheduler_run_events_dir,
        }[key]

    def _lifecycle_event_dir(self, target_type: str, target_id: str) -> Path:
        return self._lifecycle_event_root(target_type) / _safe_name(target_id)

    def _lifecycle_signal_path(self, target_type: str, target_id: str) -> Path:
        key = _lifecycle_target_key(target_type)
        root = {
            "task": self.lifecycle_task_signals_dir,
            "job": self.lifecycle_job_signals_dir,
        }.get(key)
        if root is None:
            raise StorageError("unsupported lifecycle signal target", target_type=target_type)
        return root / f"{_safe_name(target_id)}.json"

    def _session_profile_path(self, profile_id: str) -> Path:
        return self.session_profiles_dir / f"{_safe_name(profile_id)}.json"

    def _session_cookies_path(self, profile_id: str) -> Path:
        return self.session_cookies_dir / f"{_safe_name(profile_id)}.json"

    def _session_storage_state_path(self, profile_id: str) -> Path:
        return self.session_storage_states_dir / f"{_safe_name(profile_id)}.json"

    def _session_account_path(self, profile_id: str) -> Path:
        return self.session_accounts_dir / f"{_safe_name(profile_id)}.json"

    def _session_events_dir(self, profile_id: str | None) -> Path:
        return self.session_events_dir / _safe_name(profile_id or "default")

    def _record_lifecycle_observability(self, event: LifecycleEvent) -> None:
        try:
            from .observability import log_event

            trace_id = _trace_id_for_lifecycle_event(self, event)
            log_event(
                self,
                None,
                level="INFO",
                component="lifecycle",
                event_type=f"lifecycle_{event.event_type}",
                message=f"{event.target_type} {event.target_id} {event.event_type}",
                trace_id=trace_id,
                task_id=event.target_id if _lifecycle_target_key(event.target_type) == "task" else None,
                job_id=event.target_id if _lifecycle_target_key(event.target_type) == "job" else None,
                scheduler_run_id=event.target_id if _lifecycle_target_key(event.target_type) == "scheduler_run" else None,
                metadata=event.to_dict(),
            )
        except Exception:
            return

    def _observability_log_dir(self, scope: str) -> Path:
        return {
            "tasks": self.observability_log_tasks_dir,
            "jobs": self.observability_log_jobs_dir,
            "scheduler": self.observability_log_scheduler_dir,
            "system": self.observability_log_system_dir,
        }[_observability_scope_key(scope)]

    def _observability_metric_dir(self, scope: str) -> Path:
        return {
            "tasks": self.observability_metric_tasks_dir,
            "jobs": self.observability_metric_jobs_dir,
            "scheduler": self.observability_metric_scheduler_dir,
            "system": self.observability_metric_system_dir,
        }[_observability_scope_key(scope)]

    def _observability_report_dir(self, target_type: str) -> Path:
        return {
            "task": self.observability_report_tasks_dir,
            "job": self.observability_report_jobs_dir,
            "scheduler": self.observability_report_scheduler_dir,
        }[_observability_report_key(target_type)]

    def _observability_log_path(self, scope: str, target_id: str) -> Path:
        return self._observability_log_dir(scope) / f"{_safe_name(target_id)}.jsonl"

    def _observability_metric_path(self, scope: str, target_id: str) -> Path:
        return self._observability_metric_dir(scope) / f"{_safe_name(target_id)}.jsonl"

    def _observability_report_path(self, target_type: str, target_id: str) -> Path:
        return self._observability_report_dir(target_type) / f"{_safe_name(target_id)}.json"

    def _observability_trace_path(self, trace_id: str) -> Path:
        return self.observability_traces_dir / f"{_safe_name(trace_id)}.jsonl"

    def _export_manifest_path(self, export_id: str) -> Path:
        return self.exports_manifests_dir / f"{_safe_name(export_id)}.json"

    def _require_lease_owner(self, job: WorkerJob, worker_id: str) -> None:
        if job.lease_owner != worker_id:
            raise StorageError("worker does not own job lease", job_id=job.job_id, worker_id=worker_id)

    def _requeue_expired_leases_unlocked(self, now: datetime) -> list[WorkerJob]:
        recovered: list[WorkerJob] = []
        for status in ("leased", "running"):
            for path in sorted(self._queue_status_dir(status).glob("*.json")):
                job = WorkerJob.from_dict(self._load_json(path))
                expires_at = _optional_datetime(job.lease_expires_at)
                if expires_at is None or expires_at > now:
                    continue
                job.status = "queued"
                job.lease_owner = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                job.started_at = None
                job.updated_at = _format_datetime(now)
                job.warnings.append({"type": "lease_recovered", "recovered_at": _format_datetime(now)})
                target = self._job_path(job.job_id, job.status)
                self._write_job(job, target)
                path.unlink(missing_ok=True)
                recovered.append(job)
        return recovered

    def _load_hashes_from_path(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        hashes: set[str] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                value = line.strip()
                if not value:
                    continue
                if re.search(r"\s", value):
                    raise CorruptedFileError("hash line contains whitespace", path=path, line=line_number)
                hashes.add(value)
        return hashes

    def _check_json_file(self, path: Path, errors: list[dict[str, Any]]) -> None:
        try:
            self._load_json(path)
        except CorruptedFileError as exc:
            errors.append(_issue(exc.path or path, str(exc)))

    def _check_jsonl_file(self, path: Path, errors: list[dict[str, Any]]) -> None:
        try:
            self._read_jsonl(path, strict=True)
        except CorruptedFileError as exc:
            errors.append(_issue(exc.path or path, str(exc), line=exc.context.get("line")))

    def _check_hash_file(self, path: Path, errors: list[dict[str, Any]]) -> None:
        try:
            self._load_hashes_from_path(path)
        except CorruptedFileError as exc:
            errors.append(_issue(exc.path or path, str(exc), line=exc.context.get("line")))


def _validate_task_transition(current: TaskStatus, target: TaskStatus, *, task_id: str, path: Path) -> None:
    if target not in TASK_TRANSITIONS[current]:
        raise InvalidTaskTransitionError("invalid task status transition", path=path, task_id=task_id, current=current.value, target=target.value)


def _task_status(value: TaskStatus | str) -> TaskStatus:
    return value if isinstance(value, TaskStatus) else TaskStatus(value)


def _issue(path: str | Path, message: str, **extra: Any) -> dict[str, Any]:
    payload = {"path": str(path), "message": message}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


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


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _coerce_datetime(value)


def _sort_datetime(value: Any) -> str:
    parsed = _optional_datetime(value)
    return _format_datetime(parsed) if parsed else ""


def _job_status_key(status: str | None) -> str:
    text = str(status or "queued")
    if text == "dead_letters":
        return "dead_letter"
    if text not in _QUEUE_STATUS_DIRS:
        raise StorageError("unsupported worker job status", status=text)
    return text


def _lifecycle_target_key(target_type: str) -> str:
    text = str(target_type)
    aliases = {
        "tasks": "task",
        "task": "task",
        "worker_job": "job",
        "worker_jobs": "job",
        "jobs": "job",
        "job": "job",
        "scheduler_runs": "scheduler_run",
        "scheduler_run": "scheduler_run",
    }
    key = aliases.get(text)
    if key is None:
        raise StorageError("unsupported lifecycle target type", target_type=target_type)
    return key


def _observability_scope_key(scope: str) -> str:
    text = str(scope or "system")
    aliases = {
        "task": "tasks",
        "tasks": "tasks",
        "job": "jobs",
        "jobs": "jobs",
        "worker_job": "jobs",
        "worker_jobs": "jobs",
        "schedule": "scheduler",
        "scheduler": "scheduler",
        "scheduler_run": "scheduler",
        "scheduler_runs": "scheduler",
        "system": "system",
    }
    key = aliases.get(text)
    if key is None:
        raise StorageError("unsupported observability scope", scope=scope)
    return key


def _observability_report_key(target_type: str) -> str:
    text = str(target_type or "task")
    aliases = {
        "task": "task",
        "tasks": "task",
        "job": "job",
        "jobs": "job",
        "worker_job": "job",
        "worker_jobs": "job",
        "scheduler": "scheduler",
        "scheduler_run": "scheduler",
        "scheduler_runs": "scheduler",
    }
    key = aliases.get(text)
    if key is None:
        raise StorageError("unsupported run report target type", target_type=target_type)
    return key


def _trace_id_for_lifecycle_event(store: FileStore, event: LifecycleEvent) -> str | None:
    metadata_trace = event.metadata.get("trace_id") if isinstance(event.metadata, dict) else None
    if metadata_trace:
        return str(metadata_trace)
    try:
        key = _lifecycle_target_key(event.target_type)
        if key == "task":
            return store.load_task(event.target_id).metadata.get("trace_id")
        if key == "job":
            return store.get_job(event.target_id).metadata.get("trace_id")
        if key == "scheduler_run":
            for run in store.list_scheduler_runs():
                if run.get("id") == event.target_id:
                    value = run.get("summary", {}).get("trace_id")
                    return str(value) if value else None
    except Exception:
        return None
    return None


def _compact_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip(".-")
    return cleaned or "item"


def _lock_name(target: Path) -> str:
    safe_target = _safe_name(str(target))
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
    prefix = safe_target[:48].strip(".-") or "lock"
    return f"{prefix}-{digest}.lock"


def _is_expired(path: Path, *, seconds: int = 3600) -> bool:
    try:
        return time.time() - path.stat().st_mtime > seconds
    except FileNotFoundError:
        return False


def _tree_stats(path: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            total_bytes += item.stat().st_size
    return file_count, total_bytes
