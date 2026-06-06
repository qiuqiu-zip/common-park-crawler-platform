# Observability

Feature 14 adds local, file-backed observability for crawler runs. It covers structured logs, metrics, trace timelines, and run reports for tasks, worker jobs, and scheduler runs.

This feature is intentionally local only. It does not add Web UI pages, Exporter output, external observability services, Prometheus, OpenTelemetry collectors, ELK, Loki, Grafana, permissions, or runtime database storage.

## Config

Every spider may define `observability`:

```json
{
  "observability": {
    "enabled": true,
    "log_level": "INFO",
    "structured_logs": true,
    "capture_request_timeline": true,
    "capture_response_metadata": true,
    "capture_record_samples": true,
    "record_sample_limit": 10,
    "redact_sensitive": true,
    "metrics_enabled": true,
    "run_report_enabled": true
  }
}
```

Supported `log_level` values are `DEBUG`, `INFO`, `WARNING`, and `ERROR`.

Defaults keep basic metrics and run reports enabled. Sensitive redaction is enabled by default.

## Structured Logs

Logs are JSONL events. Each event includes stable fields such as:

- `event_id`
- `timestamp`
- `level`
- `component`
- `event_type`
- `message`
- `trace_id`
- `task_id`
- `job_id`
- `schedule_id`
- `scheduler_run_id`
- `spider_id`
- `request_id`
- `url`
- `status_code`
- `duration_ms`
- `error_type`
- `metadata`

Sensitive fields containing words such as `password`, `token`, `authorization`, `cookie`, `secret`, or `session` are redacted as `***REDACTED***`.

## Metrics

Metrics are JSONL records with `counter`, `gauge`, or `timer` kinds. The built-in task summary records values such as:

- `requests_total`
- `requests_success`
- `requests_failed`
- `records_extracted`
- `records_saved`
- `records_skipped`
- `duplicates_skipped`
- `retry_attempts`
- `retry_successes`
- `proxy_failures`
- `rate_limit_wait_seconds`
- `session_loads`
- `session_saves`
- `auth_check_failures`
- `tasks_started`
- `tasks_succeeded`
- `tasks_failed`
- `checkpoint_saves`
- `watermark_updates`
- `duration_ms`

Worker jobs add `jobs_claimed`, `jobs_succeeded`, `jobs_failed`, and `duration_ms`. Scheduler runs add `scheduler_runs_total` and `duration_ms`.

## Trace Timeline

Each task run receives a local `trace_id`. Worker and scheduler flows propagate the same trace into their child task when they trigger one.

Timeline events are JSONL and can include:

- `request_built`
- `fetch_started`
- `fetch_finished`
- `parse_started`
- `parse_finished`
- `extract_started`
- `extract_finished`
- `detail_started`
- `detail_finished`
- `pagination_next`
- `result_saved`
- `checkpoint_saved`
- `watermark_updated`
- lifecycle events such as cancellation, retry, or rerun

This is local tracing only; no distributed tracing service is required.

## Run Reports

Task reports are JSON files with task status, request counts, record counts, retry/session counters, warning and error summaries, result path, record samples, field non-empty rates, required missing stats, duplicate rate, transform error summary, `record_quality_status`, crawl policy summary, and `trace_id`.

Worker job reports include job status, worker id, task id, attempt, duration, error, summary, and `trace_id`.

Scheduler reports include scheduler run id, schedule id, status, task id, job id, trigger, duration, error/warnings, summary, and `trace_id`.

## FileStore Layout

Observability data is stored under:

```text
data/observability/
  logs/
    tasks/
    jobs/
    scheduler/
    system/
  metrics/
    tasks/
    jobs/
    scheduler/
    system/
  reports/
    tasks/
    jobs/
    scheduler/
  traces/
```

Logs, metrics, and traces use JSONL. Reports use JSON.

`storage check` validates observability files. Snapshots include the `observability` directory and record the storage formats in the manifest.

## CLI

```bash
python -m crawler_platform.cli observability logs --data-dir ./data
python -m crawler_platform.cli observability logs --task-id <task_id> --data-dir ./data
python -m crawler_platform.cli observability metrics --data-dir ./data
python -m crawler_platform.cli observability metrics --task-id <task_id> --data-dir ./data
python -m crawler_platform.cli observability report task <task_id> --data-dir ./data
python -m crawler_platform.cli observability report job <job_id> --data-dir ./data
python -m crawler_platform.cli observability report scheduler <scheduler_run_id> --data-dir ./data
python -m crawler_platform.cli observability trace <trace_id> --data-dir ./data
```

Use `--json` to print the complete JSON payload.

## FastAPI

```http
GET /observability/logs
GET /observability/metrics
GET /observability/reports/tasks/{task_id}
GET /observability/reports/jobs/{job_id}
GET /observability/reports/scheduler/{scheduler_run_id}
GET /observability/traces/{trace_id}
```

`/observability/logs` and `/observability/metrics` accept optional query filters such as `task_id`, `job_id`, `schedule_id`, `scheduler_run_id`, and `level` for logs.

## Boundaries

Observability write failures are best effort and do not fail the main crawl path. Core task status and result writes remain the source of truth. Runtime storage stays FileStore-based; SQL remains documentation-only in `docs/schema.sql`.
