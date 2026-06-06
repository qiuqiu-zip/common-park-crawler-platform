from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .models import LifecycleEvent, SchedulerRun, TaskRecord, TaskStatus, WorkerJob
from .storage import FileStore, StorageError


class LifecycleError(RuntimeError):
    pass


class InvalidLifecycleTransitionError(LifecycleError):
    pass


class CancellationRequested(LifecycleError):
    def __init__(self, message: str, *, target_type: str, target_id: str, reason: str | None = None, boundary: str | None = None) -> None:
        self.target_type = target_type
        self.target_id = target_id
        self.reason = reason
        self.boundary = boundary
        super().__init__(message)


class LifecycleSignal:
    def __init__(self, store: FileStore, *, task_id: str | None = None, job_id: str | None = None) -> None:
        self.store = store
        self.task_id = task_id
        self.job_id = job_id

    def bind_task(self, task_id: str) -> None:
        self.task_id = task_id

    def check(self, *, boundary: str) -> None:
        for target_type, target_id in (("task", self.task_id), ("job", self.job_id)):
            if not target_id:
                continue
            signal = self.store.read_lifecycle_signal(target_type, target_id)
            if signal.get("cancel_requested"):
                reason = signal.get("reason")
                raise CancellationRequested(
                    f"{target_type} {target_id} cancellation requested",
                    target_type=target_type,
                    target_id=target_id,
                    reason=reason,
                    boundary=boundary,
                )


class TaskLifecycleService:
    def __init__(self, store: FileStore | None = None, *, operator: str = "system") -> None:
        self.store = store or FileStore()
        self.operator = operator

    def pause_task(self, task_id: str, reason: str | None = None, *, force: bool = False) -> TaskRecord:
        task = self.store.load_task(task_id)
        if task.status == TaskStatus.PENDING:
            return self._transition_task(task, TaskStatus.PAUSED, "paused", reason=reason, force=force)
        if task.status == TaskStatus.RUNNING:
            task.lifecycle["pause_requested"] = True
            task.warnings.append({"type": "pause_requested", "message": "running pause is recorded but not interrupted"})
            self.store.save_task(task)
            self._event("task", task.id, "pause_requested", from_status=task.status.value, to_status=task.status.value, reason=reason)
            return task
        if force:
            return self._transition_task(task, TaskStatus.PAUSED, "force_transition", reason=reason, force=True)
        raise InvalidLifecycleTransitionError(f"cannot pause task {task_id} in {task.status.value} state")

    def resume_task(self, task_id: str, reason: str | None = None, *, force: bool = False) -> TaskRecord:
        task = self.store.load_task(task_id)
        if task.status == TaskStatus.PAUSED:
            task.lifecycle.pop("pause_requested", None)
            return self._transition_task(task, TaskStatus.PENDING, "resumed", reason=reason, force=force)
        if force:
            return self._transition_task(task, TaskStatus.PENDING, "force_transition", reason=reason, force=True)
        if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise InvalidLifecycleTransitionError(f"use retry_task for {task.status.value} task {task_id}")
        raise InvalidLifecycleTransitionError(f"cannot resume task {task_id} in {task.status.value} state")

    def cancel_task(self, task_id: str, reason: str | None = None, *, force: bool = False) -> TaskRecord:
        task = self.store.load_task(task_id)
        if task.status in {TaskStatus.PENDING, TaskStatus.PAUSED}:
            self.store.write_lifecycle_signal("task", task.id, {"cancel_requested": True, "reason": reason, "created_at": _now()})
            cancelled = self._transition_task(task, TaskStatus.CANCELLED, "cancelled", reason=reason, force=force, finished_at=_now())
            self.store.clear_lifecycle_signal("task", task.id)
            return cancelled
        if task.status in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
            self.store.write_lifecycle_signal("task", task.id, {"cancel_requested": True, "reason": reason, "created_at": _now()})
            if task.status == TaskStatus.CANCELLING:
                self._event("task", task.id, "cancel_requested", from_status=task.status.value, to_status=task.status.value, reason=reason)
                return task
            return self._transition_task(task, TaskStatus.CANCELLING, "cancel_requested", reason=reason, force=force)
        if force:
            return self._transition_task(task, TaskStatus.CANCELLED, "force_transition", reason=reason, force=True, finished_at=_now())
        raise InvalidLifecycleTransitionError(f"cannot cancel task {task_id} in {task.status.value} state")

    def mark_task_cancelled(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self.store.load_task(task_id)
        task.lifecycle.pop("cancel_requested", None)
        cancelled = self._transition_task(task, TaskStatus.CANCELLED, "cancelled", reason=reason, force=True, finished_at=_now())
        self.store.clear_lifecycle_signal("task", task.id)
        return cancelled

    def retry_task(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self.store.load_task(task_id)
        if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise InvalidLifecycleTransitionError(f"can only retry failed or cancelled tasks, got {task.status.value}")
        self._event("task", task.id, "retry_requested", from_status=task.status.value, to_status=TaskStatus.RETRYING.value, reason=reason)
        new_task = _new_task_from_source(task, status=TaskStatus.PENDING, event="retry")
        self.store.save_task(new_task)
        self._event(
            "task",
            new_task.id,
            "retried",
            from_status=task.status.value,
            to_status=new_task.status.value,
            reason=reason,
            metadata={"source_task_id": task.id},
        )
        return new_task

    def rerun_task(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self.store.load_task(task_id)
        if task.status not in {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise InvalidLifecycleTransitionError(f"can only rerun finished tasks, got {task.status.value}")
        self._event("task", task.id, "rerun_requested", from_status=task.status.value, to_status=TaskStatus.RERUNNING.value, reason=reason)
        new_task = _new_task_from_source(task, status=TaskStatus.PENDING, event="rerun")
        self.store.save_task(new_task)
        self._event(
            "task",
            new_task.id,
            "rerun_created",
            from_status=task.status.value,
            to_status=new_task.status.value,
            reason=reason,
            metadata={"source_task_id": task.id},
        )
        return new_task

    def get_task_lifecycle(self, task_id: str) -> dict[str, Any]:
        task = self.store.load_task(task_id)
        return {"task": task.to_dict(), "signal": self.store.read_lifecycle_signal("task", task_id), "events": self.list_task_events(task_id)}

    def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        return self.store.list_lifecycle_events("task", task_id)

    def _transition_task(
        self,
        task: TaskRecord,
        status: TaskStatus,
        event_type: str,
        *,
        reason: str | None = None,
        force: bool = False,
        **updates: Any,
    ) -> TaskRecord:
        from_status = task.status
        if not force:
            _validate_task_lifecycle(from_status, status, task.id)
        task.status = status
        for key, value in updates.items():
            setattr(task, key, value)
        if force:
            task.lifecycle["last_force_transition_at"] = _now()
        self.store.save_task(task)
        self._event(
            "task",
            task.id,
            event_type if not force else "force_transition",
            from_status=from_status.value,
            to_status=status.value,
            reason=reason,
            metadata={"force": force},
        )
        return task

    def _event(self, target_type: str, target_id: str, event_type: str, **kwargs: Any) -> LifecycleEvent:
        event = LifecycleEvent(
            event_id=uuid.uuid4().hex,
            target_type=target_type,
            target_id=target_id,
            event_type=event_type,
            operator=self.operator,
            created_at=_now(),
            **kwargs,
        )
        self.store.record_lifecycle_event(event)
        return event


class WorkerLifecycleService:
    def __init__(self, store: FileStore | None = None, *, operator: str = "system") -> None:
        self.store = store or FileStore()
        self.operator = operator

    def pause_job(self, job_id: str, reason: str | None = None, *, force: bool = False) -> WorkerJob:
        job = self.store.get_job(job_id)
        if job.status == "queued":
            return self._transition_job(job, "paused", "paused", reason=reason, force=force)
        if job.status == "running":
            job.metadata["pause_requested"] = True
            job.warnings.append({"type": "pause_requested", "message": "running pause is recorded but not interrupted"})
            self.store.save_worker_job(job)
            self._event("job", job.job_id, "pause_requested", from_status="running", to_status="running", reason=reason)
            return job
        if force:
            return self._transition_job(job, "paused", "force_transition", reason=reason, force=True)
        raise InvalidLifecycleTransitionError(f"cannot pause job {job_id} in {job.status} state")

    def resume_job(self, job_id: str, reason: str | None = None, *, force: bool = False) -> WorkerJob:
        job = self.store.get_job(job_id)
        if job.status == "paused":
            job.metadata.pop("pause_requested", None)
            return self._transition_job(job, "queued", "resumed", reason=reason, force=force)
        if force:
            return self._transition_job(job, "queued", "force_transition", reason=reason, force=True)
        raise InvalidLifecycleTransitionError(f"cannot resume job {job_id} in {job.status} state")

    def cancel_job(self, job_id: str, reason: str | None = None, *, force: bool = False) -> WorkerJob:
        job = self.store.get_job(job_id)
        if job.status in {"queued", "paused"}:
            self.store.write_lifecycle_signal("job", job.job_id, {"cancel_requested": True, "reason": reason, "created_at": _now()})
            cancelled = self._transition_job(job, "cancelled", "cancelled", reason=reason, force=force)
            self.store.clear_lifecycle_signal("job", job.job_id)
            self._record_scheduler_cancel(cancelled, reason)
            return cancelled
        if job.status in {"running", "cancelling"}:
            self.store.write_lifecycle_signal("job", job.job_id, {"cancel_requested": True, "reason": reason, "created_at": _now()})
            if job.status == "cancelling":
                self._event("job", job.job_id, "cancel_requested", from_status=job.status, to_status=job.status, reason=reason)
                return job
            updated = self._transition_job(job, "cancelling", "cancel_requested", reason=reason, force=force)
            self._record_scheduler_cancel(updated, reason)
            return updated
        if force:
            updated = self._transition_job(job, "cancelled", "force_transition", reason=reason, force=True)
            self._record_scheduler_cancel(updated, reason)
            return updated
        raise InvalidLifecycleTransitionError(f"cannot cancel job {job_id} in {job.status} state")

    def mark_job_cancelled(self, job_id: str, worker_id: str | None = None, reason: str | None = None) -> WorkerJob:
        job = self.store.cancel_claimed_job(job_id, worker_id=worker_id, reason=reason)
        self.store.clear_lifecycle_signal("job", job_id)
        self._event("job", job.job_id, "cancelled", from_status="cancelling", to_status="cancelled", reason=reason)
        self._record_scheduler_cancel(job, reason)
        return job

    def retry_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        job = self.store.get_job(job_id)
        if job.status not in {"failed", "dead_letter", "cancelled"}:
            raise InvalidLifecycleTransitionError(f"can only retry failed, dead_letter, or cancelled jobs, got {job.status}")
        self._event("job", job.job_id, "retry_requested", from_status=job.status, to_status="retrying", reason=reason)
        job.status = "queued"
        job.attempt = 0
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.started_at = None
        job.finished_at = None
        job.error = None
        job.metadata["retry_source_job_id"] = job.job_id
        job.metadata["retried_at"] = _now()
        updated = self.store.save_worker_job(job)
        self._event("job", updated.job_id, "retried", from_status="retrying", to_status=updated.status, reason=reason)
        return updated

    def rerun_job(self, job_id: str, reason: str | None = None) -> WorkerJob:
        job = self.store.get_job(job_id)
        self._event("job", job.job_id, "rerun_requested", from_status=job.status, to_status="rerunning", reason=reason)
        new_job = replace(
            job,
            job_id=f"{job.job_id}-rerun-{uuid.uuid4().hex[:8]}",
            task_id=None,
            schedule_id=None,
            source="manual",
            status="queued",
            attempt=0,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            started_at=None,
            finished_at=None,
            error=None,
            warnings=[],
            metadata={**job.metadata, "source_job_id": job.job_id, "rerun_created_at": _now()},
        )
        queued = self.store.enqueue_job(new_job)
        self._event("job", queued.job_id, "rerun_created", from_status=job.status, to_status=queued.status, reason=reason, metadata={"source_job_id": job.job_id})
        return queued

    def list_job_events(self, job_id: str) -> list[dict[str, Any]]:
        return self.store.list_lifecycle_events("job", job_id)

    def get_job_lifecycle(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        return {"job": job.to_dict(), "signal": self.store.read_lifecycle_signal("job", job_id), "events": self.list_job_events(job_id)}

    def _transition_job(
        self,
        job: WorkerJob,
        status: str,
        event_type: str,
        *,
        reason: str | None = None,
        force: bool = False,
    ) -> WorkerJob:
        from_status = job.status
        if not force:
            _validate_job_lifecycle(from_status, status, job.job_id)
        now = _now()
        job.status = status
        job.updated_at = now
        if status in {"cancelled", "succeeded", "failed", "dead_letter"}:
            job.finished_at = job.finished_at or now
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
        if force:
            job.metadata["last_force_transition_at"] = now
        updated = self.store.save_worker_job(job)
        self._event(
            "job",
            updated.job_id,
            event_type if not force else "force_transition",
            from_status=from_status,
            to_status=status,
            reason=reason,
            metadata={"force": force},
        )
        return updated

    def _record_scheduler_cancel(self, job: WorkerJob, reason: str | None) -> None:
        if not job.schedule_id:
            return
        run_id = str(job.metadata.get("scheduler_run_id") or job.job_id)
        run = SchedulerRun(
            id=run_id,
            schedule_id=job.schedule_id,
            spider_id=job.spider_id,
            task_id=job.task_id,
            status="cancelled",
            trigger="worker_lifecycle",
            scheduled_for=job.metadata.get("scheduled_for"),
            started_at=job.started_at or _now(),
            finished_at=_now(),
            summary={"job_id": job.job_id, "reason": reason},
        )
        self.store.record_scheduler_run(run)
        self._event("scheduler_run", run.id, "cancelled", from_status=None, to_status="cancelled", reason=reason, metadata={"job_id": job.job_id})

    def _event(self, target_type: str, target_id: str, event_type: str, **kwargs: Any) -> LifecycleEvent:
        event = LifecycleEvent(
            event_id=uuid.uuid4().hex,
            target_type=target_type,
            target_id=target_id,
            event_type=event_type,
            operator=self.operator,
            created_at=_now(),
            **kwargs,
        )
        self.store.record_lifecycle_event(event)
        return event


def _new_task_from_source(source: TaskRecord, *, status: TaskStatus, event: str) -> TaskRecord:
    return TaskRecord(
        id=f"{source.id}-{event}-{uuid.uuid4().hex[:8]}",
        spider_id=source.spider_id,
        status=status,
        source_task_id=source.id,
        metadata={"source_task_id": source.id, "lifecycle_action": event},
    )


def _validate_task_lifecycle(current: TaskStatus, target: TaskStatus, task_id: str) -> None:
    allowed = {
        TaskStatus.PENDING: {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED},
        TaskStatus.RUNNING: {TaskStatus.RUNNING, TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLING, TaskStatus.CANCELLED},
        TaskStatus.PAUSED: {TaskStatus.PAUSED, TaskStatus.PENDING, TaskStatus.CANCELLED},
        TaskStatus.CANCELLING: {TaskStatus.CANCELLING, TaskStatus.CANCELLED, TaskStatus.FAILED},
        TaskStatus.FAILED: {TaskStatus.FAILED, TaskStatus.RETRYING},
        TaskStatus.CANCELLED: {TaskStatus.CANCELLED, TaskStatus.RETRYING},
        TaskStatus.SUCCESS: {TaskStatus.SUCCESS, TaskStatus.RERUNNING},
        TaskStatus.RETRYING: {TaskStatus.PENDING},
        TaskStatus.RERUNNING: {TaskStatus.PENDING},
    }
    if target not in allowed[current]:
        raise InvalidLifecycleTransitionError(f"invalid task transition {task_id}: {current.value} -> {target.value}")


def _validate_job_lifecycle(current: str, target: str, job_id: str) -> None:
    allowed = {
        "queued": {"queued", "paused", "cancelled", "running"},
        "paused": {"paused", "queued", "cancelled"},
        "running": {"running", "cancelling", "succeeded", "failed", "cancelled"},
        "cancelling": {"cancelling", "cancelled", "failed"},
        "failed": {"failed", "queued"},
        "dead_letter": {"dead_letter", "queued"},
        "cancelled": {"cancelled", "queued"},
        "succeeded": {"succeeded"},
        "leased": {"leased", "running", "queued", "cancelled"},
        "retrying": {"queued"},
    }
    if target not in allowed.get(current, set()):
        raise InvalidLifecycleTransitionError(f"invalid job transition {job_id}: {current} -> {target}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
