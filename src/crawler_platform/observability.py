from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .models import SchedulerRun, SpiderConfig, TaskRecord, WorkerJob, WorkerRunResult

LOG_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
REDACTED = "***REDACTED***"
SENSITIVE_TOKENS = ("password", "secret", "token", "authorization", "cookie")
SENSITIVE_EXACT_KEYS = {"session_id", "sessionid", "sid"}


def new_trace_id() -> str:
    return uuid.uuid4().hex


def ensure_task_trace(task: TaskRecord, trace_id: str | None = None) -> str:
    selected = trace_id or task.metadata.get("trace_id") or new_trace_id()
    task.metadata["trace_id"] = selected
    return str(selected)


def trace_id_from_task(task: TaskRecord | None) -> str | None:
    if task is None:
        return None
    value = task.metadata.get("trace_id")
    return str(value) if value else None


def trace_id_from_job(job: WorkerJob | None) -> str | None:
    if job is None:
        return None
    value = job.metadata.get("trace_id")
    return str(value) if value else None


def trace_id_from_scheduler_run(run: SchedulerRun | None) -> str | None:
    if run is None:
        return None
    value = run.summary.get("trace_id")
    return str(value) if value else None


def start_trace(store: Any, trace_id: str, *, metadata: dict[str, Any] | None = None) -> None:
    safe_observe(
        store.create_trace,
        {
            "trace_id": trace_id,
            "timestamp": _now(),
            "event_type": "trace_created",
            "metadata": redact_sensitive(metadata or {}),
        },
    )


def log_event(
    store: Any,
    spider: SpiderConfig | None = None,
    *,
    level: str = "INFO",
    component: str,
    event_type: str,
    message: str,
    trace_id: str | None = None,
    task_id: str | None = None,
    job_id: str | None = None,
    schedule_id: str | None = None,
    scheduler_run_id: str | None = None,
    spider_id: str | None = None,
    request_id: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    scope: str | None = None,
    target_id: str | None = None,
) -> dict[str, Any] | None:
    if not observability_enabled(spider) or not level_enabled(spider, level):
        return None
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": _now(),
        "level": level.upper(),
        "component": component,
        "event_type": event_type,
        "message": message,
        "trace_id": trace_id,
        "task_id": task_id,
        "job_id": job_id,
        "schedule_id": schedule_id,
        "scheduler_run_id": scheduler_run_id,
        "spider_id": spider_id or (spider.id if spider else None),
        "request_id": request_id,
        "url": url,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "error_type": error_type,
        "metadata": _maybe_redact(spider, metadata or {}),
    }
    selected_scope = scope or _scope_from_ids(task_id=task_id, job_id=job_id, schedule_id=schedule_id, scheduler_run_id=scheduler_run_id)
    selected_target = target_id or task_id or job_id or scheduler_run_id or schedule_id or "system"
    safe_observe(store.append_log, selected_scope, selected_target, event)
    if trace_id:
        safe_observe(
            store.append_trace_event,
            trace_id,
            {
                "timestamp": event["timestamp"],
                "event_type": event_type,
                "component": component,
                "level": event["level"],
                "message": message,
                "task_id": task_id,
                "job_id": job_id,
                "schedule_id": schedule_id,
                "scheduler_run_id": scheduler_run_id,
                "spider_id": event["spider_id"],
                "request_id": request_id,
                "url": url,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "error_type": error_type,
                "metadata": event["metadata"],
            },
        )
    return event


def trace_event(
    store: Any,
    spider: SpiderConfig | None,
    trace_id: str | None,
    event_type: str,
    *,
    task_id: str | None = None,
    job_id: str | None = None,
    schedule_id: str | None = None,
    scheduler_run_id: str | None = None,
    spider_id: str | None = None,
    request_id: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not trace_id or not capture_timeline_enabled(spider):
        return
    safe_observe(
        store.append_trace_event,
        trace_id,
        {
            "timestamp": _now(),
            "event_type": event_type,
            "task_id": task_id,
            "job_id": job_id,
            "schedule_id": schedule_id,
            "scheduler_run_id": scheduler_run_id,
            "spider_id": spider_id or (spider.id if spider else None),
            "request_id": request_id,
            "url": url,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "error_type": error_type,
            "metadata": _maybe_redact(spider, metadata or {}),
        },
    )


def record_metric(
    store: Any,
    spider: SpiderConfig | None,
    *,
    scope: str,
    target_id: str,
    name: str,
    value: int | float,
    kind: str = "counter",
    unit: str | None = None,
    tags: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> None:
    if not metrics_enabled(spider):
        return
    metric = {
        "metric_id": uuid.uuid4().hex,
        "timestamp": _now(),
        "scope": scope,
        "target_id": target_id,
        "name": name,
        "kind": kind,
        "value": value,
        "unit": unit,
        "trace_id": trace_id,
        "tags": _maybe_redact(spider, tags or {}),
    }
    safe_observe(store.record_metric, scope, target_id, metric)


def record_task_metrics(store: Any, spider: SpiderConfig, task: TaskRecord) -> None:
    if not metrics_enabled(spider):
        return
    trace_id = trace_id_from_task(task)
    metrics = {
        "requests_total": task.total_requests,
        "requests_success": task.success_requests,
        "requests_failed": task.failed_requests,
        "records_extracted": task.total_records,
        "records_saved": task.saved_records or task.saved_count,
        "records_skipped": task.skipped_records,
        "duplicates_skipped": task.skipped_duplicates,
        "retry_attempts": task.retry_attempts,
        "retry_successes": task.retry_successes,
        "proxy_failures": _proxy_failures(task),
        "rate_limit_wait_seconds": task.rate_limit_wait_seconds,
        "session_loads": task.session_loads,
        "session_saves": task.session_saves,
        "auth_check_failures": task.auth_check_failures,
        "tasks_started": 1 if task.started_at else 0,
        "tasks_succeeded": 1 if _status_value(task.status) == "success" else 0,
        "tasks_failed": 1 if _status_value(task.status) == "failed" else 0,
        "duration_ms": _duration_ms(task.started_at, task.finished_at) or 0,
        "watermark_updates": task.watermark_updates,
        "checkpoint_saves": task.checkpoint_saves,
    }
    for name, value in metrics.items():
        kind = "timer" if name == "duration_ms" else "counter"
        unit = "ms" if name == "duration_ms" else ("seconds" if name.endswith("_seconds") else None)
        record_metric(store, spider, scope="tasks", target_id=task.id, name=name, value=value, kind=kind, unit=unit, trace_id=trace_id)


def record_worker_metrics(store: Any, job: WorkerJob, run: WorkerRunResult) -> None:
    trace_id = trace_id_from_job(job)
    values = {
        "jobs_claimed": 1,
        "jobs_succeeded": 1 if run.status == "succeeded" else 0,
        "jobs_failed": 1 if run.status in {"failed", "dead_letter", "retried"} else 0,
        "duration_ms": _duration_ms(run.started_at, run.finished_at) or 0,
    }
    for name, value in values.items():
        kind = "timer" if name == "duration_ms" else "counter"
        safe_observe(
            store.record_metric,
            "jobs",
            job.job_id,
            {
                "metric_id": uuid.uuid4().hex,
                "timestamp": _now(),
                "scope": "jobs",
                "target_id": job.job_id,
                "name": name,
                "kind": kind,
                "value": value,
                "unit": "ms" if kind == "timer" else None,
                "trace_id": trace_id,
                "tags": {"worker_id": run.worker_id, "status": run.status},
            },
        )


def record_scheduler_metrics(store: Any, run: SchedulerRun) -> None:
    trace_id = trace_id_from_scheduler_run(run)
    values = {
        "scheduler_runs_total": 1,
        "duration_ms": _duration_ms(run.started_at, run.finished_at) or 0,
    }
    for name, value in values.items():
        kind = "timer" if name == "duration_ms" else "counter"
        safe_observe(
            store.record_metric,
            "scheduler",
            run.id,
            {
                "metric_id": uuid.uuid4().hex,
                "timestamp": _now(),
                "scope": "scheduler",
                "target_id": run.id,
                "name": name,
                "kind": kind,
                "value": value,
                "unit": "ms" if kind == "timer" else None,
                "trace_id": trace_id,
                "tags": {"schedule_id": run.schedule_id, "status": run.status},
            },
        )


def record_task_report(store: Any, spider: SpiderConfig, task: TaskRecord) -> dict[str, Any]:
    if not run_report_enabled(spider):
        return {}
    report = build_task_report(store, spider, task)
    safe_observe(store.create_run_report, "task", task.id, report)
    return report


def build_task_report(store: Any, spider: SpiderConfig, task: TaskRecord) -> dict[str, Any]:
    from .debugging import summarize_field_quality

    warnings = list(task.warnings or [])
    errors = []
    if task.error_type or task.error_message:
        errors.append({"error_type": task.error_type, "message": task.error_message})
    record_samples: list[dict[str, Any]] = []
    if spider.observability.capture_record_samples:
        try:
            limit = max(0, int(spider.observability.record_sample_limit))
            record_samples = store.read_records(task.id, strict=False)[:limit]
        except Exception:
            record_samples = []
    quality_records = _quality_records(store, task.id)
    field_quality = [asdict(item) for item in summarize_field_quality(quality_records, spider.fields, sample_size=spider.observability.record_sample_limit)]
    required_missing = [
        {
            "field": item["field"],
            "missing": item["empty_count"],
            "total_records": item["total_records"],
            "missing_rate": item["missing_rate"],
            "status": item["status"],
            "hint": item.get("hint"),
        }
        for item in field_quality
        if item.get("required") and item.get("empty_count")
    ]
    transform_errors = _transform_error_summary(warnings)
    record_quality_status = _record_quality_status(field_quality, transform_errors)
    report = {
        "task_id": task.id,
        "spider_id": task.spider_id,
        "status": _status_value(task.status),
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "duration_ms": _duration_ms(task.started_at, task.finished_at),
        "total_requests": task.total_requests,
        "success_requests": task.success_requests,
        "failed_requests": task.failed_requests,
        "total_records": task.total_records,
        "saved_records": task.saved_records or task.saved_count,
        "skipped_records": task.skipped_records,
        "skipped_duplicates": task.skipped_duplicates,
        "retry_attempts": task.retry_attempts,
        "session_loads": task.session_loads,
        "warnings_count": len(warnings),
        "errors_count": len(errors) or task.failed_count,
        "top_errors": _top_summary(errors),
        "top_warnings": _top_summary(warnings),
        "output_result_path": str(store.results_dir / f"{_safe_name(task.id)}.jsonl"),
        "trace_id": trace_id_from_task(task),
        "error_summary": errors,
        "warning_summary": warnings,
        "record_samples": record_samples,
        "field_quality": field_quality,
        "required_missing_stats": required_missing,
        "duplicate_rate": _duplicate_rate(task),
        "transform_error_summary": transform_errors,
        "record_quality_status": record_quality_status,
        "crawl_policy": task.request_governance.get("crawl_policy", {}) if isinstance(task.request_governance, dict) else {},
        "request_governance": task.request_governance,
    }
    return _maybe_redact(spider, report)


def record_worker_report(store: Any, job: WorkerJob, run: WorkerRunResult) -> dict[str, Any]:
    report = build_worker_report(job, run)
    safe_observe(store.create_run_report, "job", job.job_id, report)
    return report


def build_worker_report(job: WorkerJob, run: WorkerRunResult) -> dict[str, Any]:
    trace_id = trace_id_from_job(job)
    return redact_sensitive(
        {
            "job_id": job.job_id,
            "status": run.status,
            "worker_id": run.worker_id,
            "task_id": run.task_id or job.task_id,
            "attempt": job.attempt,
            "duration_ms": _duration_ms(run.started_at, run.finished_at),
            "error": run.error or job.error,
            "trace_id": trace_id,
            "summary": run.summary,
        }
    )


def record_scheduler_report(store: Any, run: SchedulerRun) -> dict[str, Any]:
    report = build_scheduler_report(run)
    safe_observe(store.create_run_report, "scheduler", run.id, report)
    return report


def build_scheduler_report(run: SchedulerRun) -> dict[str, Any]:
    return redact_sensitive(
        {
            "scheduler_run_id": run.id,
            "schedule_id": run.schedule_id,
            "status": run.status,
            "task_id": run.task_id,
            "job_id": run.summary.get("job_id"),
            "trigger": run.trigger,
            "duration_ms": _duration_ms(run.started_at, run.finished_at),
            "trace_id": trace_id_from_scheduler_run(run),
            "error": {"type": run.error_type, "message": run.error_message} if run.error_type or run.error_message else None,
            "warnings": run.warnings,
            "summary": run.summary,
        }
    )


def safe_observe(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def observability_enabled(spider: SpiderConfig | None) -> bool:
    return True if spider is None else bool(spider.observability.enabled)


def metrics_enabled(spider: SpiderConfig | None) -> bool:
    return observability_enabled(spider) and (True if spider is None else bool(spider.observability.metrics_enabled))


def run_report_enabled(spider: SpiderConfig | None) -> bool:
    return observability_enabled(spider) and (True if spider is None else bool(spider.observability.run_report_enabled))


def capture_timeline_enabled(spider: SpiderConfig | None) -> bool:
    return observability_enabled(spider) and (True if spider is None else bool(spider.observability.capture_request_timeline))


def level_enabled(spider: SpiderConfig | None, level: str) -> bool:
    configured = "INFO" if spider is None else str(spider.observability.log_level or "INFO").upper()
    return LOG_LEVEL_ORDER.get(level.upper(), 20) >= LOG_LEVEL_ORDER.get(configured, 20)


def _maybe_redact(spider: SpiderConfig | None, value: Any) -> Any:
    if spider is None or spider.observability.redact_sensitive:
        return redact_sensitive(value)
    return value


def _scope_from_ids(*, task_id: str | None, job_id: str | None, schedule_id: str | None, scheduler_run_id: str | None) -> str:
    if task_id:
        return "tasks"
    if job_id:
        return "jobs"
    if schedule_id or scheduler_run_id:
        return "scheduler"
    return "system"


def _duration_ms(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (finished - started).total_seconds() * 1000)


def _top_summary(items: list[Any]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for item in items:
        if isinstance(item, dict):
            key = str(item.get("error_type") or item.get("type") or item.get("message") or item)
        else:
            key = str(item)
        counter[key] += 1
    return [{"key": key, "count": count} for key, count in counter.most_common(5)]


def _proxy_failures(task: TaskRecord) -> int:
    states = task.request_governance.get("proxy", {}).get("states", []) if isinstance(task.request_governance, dict) else []
    return sum(int(item.get("failures", 0) or 0) for item in states if isinstance(item, dict))


def _quality_records(store: Any, task_id: str) -> list[dict[str, Any]]:
    try:
        return list(store.iter_records(task_id, strict=False, limit=1000))
    except Exception:
        return []


def _duplicate_rate(task: TaskRecord) -> float:
    denominator = task.total_seen or task.total_records
    return round(task.duplicate_records / denominator, 4) if denominator else 0.0


def _transform_error_summary(warnings: list[Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for warning in warnings:
        text = jsonish_text(warning)
        if "transform" in text.lower():
            matches.append({"message": text[:300]})
    return matches[:5]


def _record_quality_status(field_quality: list[dict[str, Any]], transform_errors: list[dict[str, Any]]) -> str:
    if not field_quality:
        return "unknown"
    statuses = {str(item.get("status")) for item in field_quality}
    if statuses == {"unknown"}:
        return "unknown"
    if "failed" in statuses or transform_errors:
        return "error"
    if "warning" in statuses:
        return "warning"
    if "unknown" in statuses:
        return "warning"
    return "ok"


def jsonish_text(value: Any) -> str:
    try:
        return str(value) if isinstance(value, str) else repr(value)
    except Exception:
        return ""


def _status_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_EXACT_KEYS or any(token in lowered for token in SENSITIVE_TOKENS)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value)).strip(".-") or "item"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
