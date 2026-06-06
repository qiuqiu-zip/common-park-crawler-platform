from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .engine import CrawlerEngine
from .models import SchedulerJob, SchedulerOptions, SchedulerRun, SpiderConfig, TaskStatus
from .observability import (
    log_event,
    new_trace_id,
    record_scheduler_metrics,
    record_scheduler_report,
    safe_observe,
    start_trace,
    trace_id_from_scheduler_run,
)
from .storage import FileStore
from .validation import ensure_valid_spider_config
from .worker import WorkerService


class SchedulerError(RuntimeError):
    pass


class FakeClock:
    def __init__(self, now: str | datetime) -> None:
        self._now = _coerce_datetime(now, ZoneInfo("UTC"))

    def now(self) -> datetime:
        return self._now

    def set(self, value: str | datetime) -> None:
        self._now = _coerce_datetime(value, ZoneInfo("UTC"))

    def advance(self, **kwargs: Any) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now


class SchedulerService:
    def __init__(
        self,
        store: FileStore | None = None,
        engine: CrawlerEngine | None = None,
        *,
        clock: FakeClock | None = None,
        max_catch_up_runs: int = 5,
    ) -> None:
        self.store = store or FileStore()
        self.engine = engine or CrawlerEngine(store=self.store)
        self.clock = clock
        self.max_catch_up_runs = max(1, max_catch_up_runs)

    def register_spider_schedule(self, spider_config: SpiderConfig | dict[str, Any]) -> SchedulerJob | None:
        spider = ensure_valid_spider_config(spider_config)
        self.store.save_spider(spider)
        scheduler = spider.scheduler
        if not spider.enabled or not scheduler.enabled:
            return None

        now = self._now()
        schedule_id = spider.id
        next_run_at = self.compute_next_run(spider, now=now) if scheduler.type != "manual" else None
        existing = _maybe_get_schedule(self.store, schedule_id)
        created_at = existing.created_at if existing else _format_datetime(now)
        job = SchedulerJob(
            id=schedule_id,
            spider_id=spider.id,
            spider=spider.to_dict(),
            scheduler=asdict(scheduler),
            status=existing.status if existing and existing.status != "disabled" else "enabled",
            next_run_at=next_run_at,
            last_run_at=existing.last_run_at if existing else None,
            running_instances=existing.running_instances if existing else 0,
            created_at=created_at,
            updated_at=_format_datetime(now),
            warnings=list(existing.warnings) if existing else [],
            metadata=dict(existing.metadata) if existing else {},
        )
        self.store.save_schedule(job)
        return job

    def list_schedules(self, enabled: bool | None = None, spider_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_schedules(enabled=enabled, spider_id=spider_id)

    def get_schedule(self, schedule_id: str) -> SchedulerJob:
        return self.store.get_schedule(schedule_id)

    def list_scheduler_runs(self, schedule_id: str | None = None, spider_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_scheduler_runs(schedule_id=schedule_id, spider_id=spider_id)

    def compute_next_run(self, schedule: SchedulerJob | SpiderConfig | SchedulerOptions | dict[str, Any], now: str | datetime | None = None, after: str | datetime | None = None) -> str | None:
        scheduler = _scheduler_payload(schedule)
        if scheduler.get("type", "manual") == "manual":
            return None
        tz = _timezone(str(scheduler.get("timezone") or "UTC"))
        now_dt = _coerce_datetime(now or self._now(), tz)
        start_at = _optional_datetime(scheduler.get("start_at"), tz)
        end_at = _optional_datetime(scheduler.get("end_at"), tz)
        base = _coerce_datetime(after, tz) if after is not None else now_dt
        if start_at and base < start_at:
            base = start_at

        scheduler_type = scheduler.get("type", "manual")
        if scheduler_type == "interval":
            candidate = _next_interval_run(scheduler, base, after is not None, start_at)
        elif scheduler_type == "cron":
            candidate = _next_cron_run(scheduler, base, after is not None)
        else:
            return None

        if candidate is None:
            return None
        candidate = _apply_jitter(candidate, scheduler, _schedule_id(schedule))
        if end_at and candidate > end_at:
            return None
        return _format_datetime(candidate)

    def run_due_jobs(self, now: str | datetime | None = None, *, enqueue: bool = False) -> list[dict[str, Any]]:
        if enqueue:
            return self.enqueue_due_jobs(now=now)
        now_dt = _coerce_datetime(now or self._now(), ZoneInfo("UTC"))
        outcomes: list[dict[str, Any]] = []
        for payload in self.store.list_schedules(enabled=True):
            job = SchedulerJob.from_dict(payload)
            if job.status != "enabled":
                continue
            scheduler = SchedulerOptions.from_dict(job.scheduler)
            if scheduler.type == "manual":
                continue
            if not job.next_run_at:
                job.next_run_at = self.compute_next_run(job, now=now_dt)
                self._save_job(job)
                continue

            due_at = _coerce_datetime(job.next_run_at, _timezone(scheduler.timezone))
            if due_at > now_dt.astimezone(due_at.tzinfo):
                continue
            if scheduler.end_at and _coerce_datetime(scheduler.end_at, due_at.tzinfo) < due_at:
                job.next_run_at = None
                self._save_job(job)
                continue

            due_times, warnings = self._due_times(job, now_dt)
            for warning in warnings:
                job.warnings.append(warning)
            if not due_times:
                job.next_run_at = self.compute_next_run(job, now=now_dt, after=now_dt)
                self._save_job(job)
                outcomes.append(self._record_scheduler_skip(job, "misfire_skip", warnings, scheduled_for=_format_datetime(due_at)))
                continue

            for scheduled_for in due_times:
                if self._running_instances(job) >= max(1, int(job.scheduler.get("max_instances", 1))):
                    warning = f"schedule {job.id} skipped because max_instances is reached"
                    job.warnings.append(warning)
                    outcomes.append(self._record_scheduler_skip(job, "max_instances", [warning], scheduled_for=_format_datetime(scheduled_for)))
                    break
                outcomes.append(self._run_job(job, scheduled_for, trigger="scheduled"))

            job.next_run_at = self.compute_next_run(job, now=now_dt, after=now_dt)
            self._save_job(job)
        return outcomes

    def enqueue_due_jobs(self, now: str | datetime | None = None) -> list[dict[str, Any]]:
        now_dt = _coerce_datetime(now or self._now(), ZoneInfo("UTC"))
        outcomes: list[dict[str, Any]] = []
        for payload in self.store.list_schedules(enabled=True):
            job = SchedulerJob.from_dict(payload)
            if job.status != "enabled":
                continue
            scheduler = SchedulerOptions.from_dict(job.scheduler)
            if scheduler.type == "manual":
                continue
            if not job.next_run_at:
                job.next_run_at = self.compute_next_run(job, now=now_dt)
                self._save_job(job)
                continue

            due_at = _coerce_datetime(job.next_run_at, _timezone(scheduler.timezone))
            if due_at > now_dt.astimezone(due_at.tzinfo):
                continue
            if scheduler.end_at and _coerce_datetime(scheduler.end_at, due_at.tzinfo) < due_at:
                job.next_run_at = None
                self._save_job(job)
                continue

            due_times, warnings = self._due_times(job, now_dt)
            for warning in warnings:
                job.warnings.append(warning)
            if not due_times:
                job.next_run_at = self.compute_next_run(job, now=now_dt, after=now_dt)
                self._save_job(job)
                outcomes.append(self._record_scheduler_skip(job, "misfire_skip", warnings, scheduled_for=_format_datetime(due_at)))
                continue

            for scheduled_for in due_times:
                outcomes.append(self._enqueue_job(job, scheduled_for))

            job.last_run_at = _format_datetime(now_dt)
            job.next_run_at = self.compute_next_run(job, now=now_dt, after=now_dt)
            self._save_job(job)
        return outcomes

    def pause_schedule(self, schedule_id: str) -> SchedulerJob:
        return self._set_status(schedule_id, "paused")

    def resume_schedule(self, schedule_id: str) -> SchedulerJob:
        job = self.get_schedule(schedule_id)
        job.status = "enabled"
        if job.scheduler.get("type") != "manual":
            job.next_run_at = self.compute_next_run(job, now=self._now())
        job.updated_at = _format_datetime(self._now())
        self.store.save_schedule(job)
        return job

    def disable_schedule(self, schedule_id: str) -> SchedulerJob:
        job = self._set_status(schedule_id, "disabled")
        job.next_run_at = None
        self.store.save_schedule(job)
        return job

    def trigger_schedule_now(self, schedule_id: str) -> dict[str, Any]:
        job = self.get_schedule(schedule_id)
        if job.status == "disabled":
            raise SchedulerError(f"schedule {schedule_id} is disabled")
        return self._run_job(job, self._now(), trigger="manual")

    def _due_times(self, job: SchedulerJob, now: datetime) -> tuple[list[datetime], list[str]]:
        scheduler = SchedulerOptions.from_dict(job.scheduler)
        tz = _timezone(scheduler.timezone)
        due_at = _coerce_datetime(job.next_run_at, tz)
        now_local = now.astimezone(tz)
        if due_at > now_local:
            return [], []
        missed = due_at < now_local
        if not missed:
            return [due_at], []
        if scheduler.misfire_policy == "skip":
            return [], [f"schedule {job.id} skipped missed run at {_format_datetime(due_at)}"]
        if scheduler.misfire_policy == "run_once":
            return [due_at], [f"schedule {job.id} collapsed missed runs into one run"]

        due_times = [due_at]
        warnings: list[str] = []
        current = due_at
        while len(due_times) < self.max_catch_up_runs:
            next_run = self.compute_next_run(job, now=now_local, after=current)
            if not next_run:
                break
            current = _coerce_datetime(next_run, tz)
            if current > now_local:
                break
            due_times.append(current)
        if len(due_times) >= self.max_catch_up_runs:
            warnings.append(f"schedule {job.id} catch_up limited to {self.max_catch_up_runs} runs")
        return due_times, warnings

    def _run_job(self, job: SchedulerJob, scheduled_for: datetime, *, trigger: str) -> dict[str, Any]:
        now = self._now()
        task_id = f"sched-{job.id}-{uuid.uuid4().hex[:10]}"
        trace_id = new_trace_id()
        run = SchedulerRun(
            id=uuid.uuid4().hex,
            schedule_id=job.id,
            spider_id=job.spider_id,
            task_id=task_id,
            status="running",
            trigger=trigger,
            scheduled_for=_format_datetime(scheduled_for),
            started_at=_format_datetime(now),
            summary={"trace_id": trace_id},
        )
        safe_observe(start_trace, self.store, trace_id, metadata={"scheduler_run_id": run.id, "schedule_id": job.id, "spider_id": job.spider_id})
        safe_observe(
            log_event,
            self.store,
            None,
            level="INFO",
            component="scheduler",
            event_type="scheduler_run_started",
            message=f"Scheduler run {run.id} started",
            trace_id=trace_id,
            schedule_id=job.id,
            scheduler_run_id=run.id,
            spider_id=job.spider_id,
            metadata={"trigger": trigger, "scheduled_for": run.scheduled_for},
        )
        self.store.record_scheduler_run(run)
        job.running_instances += 1
        job.last_run_at = _format_datetime(now)
        job.updated_at = _format_datetime(now)
        self.store.save_schedule(job)
        try:
            spider = SpiderConfig.from_dict(job.spider)
            task = self.engine.run(spider, task_id=task_id, trace_id=trace_id)
            run.status = "success" if task.status == TaskStatus.SUCCESS else "failed"
            run.records_count = task.saved_records or task.saved_count
            run.error_type = task.error_type
            run.error_message = task.error_message
            run.warnings = list(task.warnings)
            run.summary = {
                "trace_id": trace_id,
                "task_status": task.status.value,
                "total_requests": task.total_requests,
                "success_requests": task.success_requests,
                "failed_requests": task.failed_requests,
                "saved_records": task.saved_records or task.saved_count,
            }
        except Exception as exc:
            run.status = "failed"
            run.error_type = type(exc).__name__
            run.error_message = str(exc)
            run.warnings.append(str(exc))
        finally:
            run.finished_at = _format_datetime(self._now())
            job.running_instances = max(0, job.running_instances - 1)
            job.updated_at = _format_datetime(self._now())
            self.store.record_scheduler_run(run)
            self.store.save_schedule(job)
            self._finalize_scheduler_observability(run)
        return run.to_dict()

    def _record_scheduler_skip(self, job: SchedulerJob, reason: str, warnings: list[str], *, scheduled_for: str | None) -> dict[str, Any]:
        now = _format_datetime(self._now())
        trace_id = new_trace_id()
        run = SchedulerRun(
            id=uuid.uuid4().hex,
            schedule_id=job.id,
            spider_id=job.spider_id,
            status="skipped",
            trigger=reason,
            scheduled_for=scheduled_for,
            started_at=now,
            finished_at=now,
            warnings=warnings,
            summary={"reason": reason, "trace_id": trace_id},
        )
        safe_observe(start_trace, self.store, trace_id, metadata={"scheduler_run_id": run.id, "schedule_id": job.id, "skip_reason": reason})
        self.store.record_scheduler_run(run)
        self._finalize_scheduler_observability(run)
        return run.to_dict()

    def _enqueue_job(self, job: SchedulerJob, scheduled_for: datetime) -> dict[str, Any]:
        scheduled_for_text = _format_datetime(scheduled_for)
        dedupe_key = f"{job.id}:{scheduled_for_text}"
        for existing in self.store.list_jobs(source="scheduler", spider_id=job.spider_id):
            if existing.get("metadata", {}).get("dedupe_key") == dedupe_key:
                return {"status": existing.get("status"), "deduplicated": True, "job": existing, "schedule_id": job.id}

        scheduler_run_id = uuid.uuid4().hex
        trace_id = new_trace_id()
        worker_job_id = f"sched-{hashlib.sha256(dedupe_key.encode('utf-8')).hexdigest()[:16]}"
        worker_job = WorkerService(store=self.store, engine=self.engine, clock=self.clock).enqueue_spider_run(
            SpiderConfig.from_dict(job.spider),
            source="scheduler",
            priority=0,
            run_after=scheduled_for,
            schedule_id=job.id,
            job_id=worker_job_id,
            metadata={
                "dedupe_key": dedupe_key,
                "scheduled_for": scheduled_for_text,
                "scheduler_run_id": scheduler_run_id,
                "trace_id": trace_id,
            },
        )
        now = _format_datetime(self._now())
        safe_observe(start_trace, self.store, trace_id, metadata={"scheduler_run_id": scheduler_run_id, "job_id": worker_job.job_id, "schedule_id": job.id})
        run = SchedulerRun(
            id=scheduler_run_id,
            schedule_id=job.id,
            spider_id=job.spider_id,
            status="queued",
            trigger="worker_enqueue",
            scheduled_for=scheduled_for_text,
            started_at=now,
            summary={"job_id": worker_job.job_id, "trace_id": trace_id},
        )
        self.store.record_scheduler_run(run)
        self._finalize_scheduler_observability(run)
        return {"status": "queued", "job": worker_job.to_dict(), "scheduler_run": run.to_dict()}

    def _running_instances(self, job: SchedulerJob) -> int:
        running = sum(1 for item in self.store.list_scheduler_runs(schedule_id=job.id) if item.get("status") == "running")
        return max(int(job.running_instances or 0), running)

    def _set_status(self, schedule_id: str, status: str) -> SchedulerJob:
        job = self.get_schedule(schedule_id)
        job.status = status
        job.updated_at = _format_datetime(self._now())
        self.store.save_schedule(job)
        return job

    def _save_job(self, job: SchedulerJob) -> None:
        job.updated_at = _format_datetime(self._now())
        self.store.save_schedule(job)

    def _finalize_scheduler_observability(self, run: SchedulerRun) -> None:
        trace_id = trace_id_from_scheduler_run(run)
        safe_observe(
            log_event,
            self.store,
            None,
            level="INFO" if run.status in {"success", "queued"} else "ERROR" if run.status == "failed" else "WARNING",
            component="scheduler",
            event_type="scheduler_run_finished",
            message=f"Scheduler run {run.id} finished with {run.status}",
            trace_id=trace_id,
            schedule_id=run.schedule_id,
            scheduler_run_id=run.id,
            spider_id=run.spider_id,
            error_type=run.error_type,
            metadata=run.to_dict(),
        )
        safe_observe(record_scheduler_metrics, self.store, run)
        safe_observe(record_scheduler_report, self.store, run)

    def _now(self) -> datetime:
        if self.clock is not None:
            return _coerce_datetime(self.clock.now(), ZoneInfo("UTC"))
        return datetime.now(timezone.utc)


def _next_interval_run(scheduler: dict[str, Any], base: datetime, has_after: bool, start_at: datetime | None) -> datetime | None:
    seconds = int(scheduler.get("interval_seconds") or 0)
    if seconds <= 0:
        raise SchedulerError("interval scheduler requires interval_seconds")
    if has_after:
        return base + timedelta(seconds=seconds)
    if start_at is not None:
        return start_at
    return base + timedelta(seconds=seconds)


def _next_cron_run(scheduler: dict[str, Any], base: datetime, has_after: bool) -> datetime | None:
    expression = str(scheduler.get("cron") or "").strip()
    if not expression:
        raise SchedulerError("cron scheduler requires cron")
    cron = _parse_cron(expression)
    current = _round_to_minute(base)
    if has_after or base.second or base.microsecond:
        current += timedelta(minutes=1)
    deadline = current + timedelta(days=366)
    while current <= deadline:
        if _cron_matches(cron, current):
            return current
        current += timedelta(minutes=1)
    return None


def _parse_cron(expression: str) -> dict[str, set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise SchedulerError("cron expression must have 5 fields")
    return {
        "minute": _parse_cron_field(parts[0], 0, 59),
        "hour": _parse_cron_field(parts[1], 0, 23),
        "day": _parse_cron_field(parts[2], 1, 31),
        "month": _parse_cron_field(parts[3], 1, 12),
        "weekday": _parse_cron_field(parts[4], 0, 7),
    }


def _parse_cron_field(value: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise SchedulerError("empty cron field")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step < 1:
                raise SchedulerError("cron step must be positive")
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise SchedulerError("cron value out of range")
        values.update(range(start, end + 1, step))
    if maximum == 7 and 7 in values:
        values.add(0)
    return values


def _cron_matches(cron: dict[str, set[int]], value: datetime) -> bool:
    cron_weekday = value.isoweekday() % 7
    return (
        value.minute in cron["minute"]
        and value.hour in cron["hour"]
        and value.day in cron["day"]
        and value.month in cron["month"]
        and cron_weekday in cron["weekday"]
    )


def _scheduler_payload(schedule: SchedulerJob | SpiderConfig | SchedulerOptions | dict[str, Any]) -> dict[str, Any]:
    if isinstance(schedule, SchedulerJob):
        return dict(schedule.scheduler)
    if isinstance(schedule, SpiderConfig):
        return asdict(schedule.scheduler)
    if isinstance(schedule, SchedulerOptions):
        return asdict(schedule)
    if "scheduler" in schedule and isinstance(schedule["scheduler"], dict):
        return dict(schedule["scheduler"])
    return dict(schedule)


def _schedule_id(schedule: SchedulerJob | SpiderConfig | SchedulerOptions | dict[str, Any]) -> str:
    if isinstance(schedule, SchedulerJob):
        return schedule.id
    if isinstance(schedule, SpiderConfig):
        return schedule.id
    if isinstance(schedule, dict):
        return str(schedule.get("id") or schedule.get("spider_id") or "schedule")
    return "schedule"


def _apply_jitter(value: datetime, scheduler: dict[str, Any], schedule_id: str) -> datetime:
    jitter = int(scheduler.get("jitter_seconds") or 0)
    if jitter <= 0:
        return value
    seed = f"{schedule_id}:{value.isoformat()}".encode("utf-8")
    seconds = int(hashlib.sha256(seed).hexdigest()[:8], 16) % (jitter + 1)
    return value + timedelta(seconds=seconds)


def _optional_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    return _coerce_datetime(value, tz)


def _coerce_datetime(value: str | datetime | None, tz: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).astimezone(tz)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerError(f"unknown scheduler timezone: {name}") from exc


def _round_to_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _maybe_get_schedule(store: FileStore, schedule_id: str) -> SchedulerJob | None:
    try:
        return store.get_schedule(schedule_id)
    except FileNotFoundError:
        return None
