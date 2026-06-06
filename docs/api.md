# FastAPI Management API

Feature 16 completes the file-backed FastAPI management layer. The API exposes
runtime status, spider configuration, task execution, storage maintenance,
scheduler, worker queue, lifecycle, session, observability, and export
operations. Runtime state remains in `FileStore`; this feature does not add a
database, Web UI, permission system, external object storage, or deployment
control plane.

## Response Envelope

All JSON HTTP responses are wrapped at the API boundary:

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "req-1",
    "trace_id": "req-1",
    "timestamp": "2026-06-03T00:00:00+00:00"
  }
}
```

Errors use the same shape:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": []
  },
  "meta": {
    "request_id": "req-1",
    "trace_id": "req-1",
    "timestamp": "2026-06-03T00:00:00+00:00"
  }
}
```

Supported error codes are `VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`,
`INVALID_STATE`, `STORAGE_ERROR`, `ENGINE_ERROR`, `EXPORT_ERROR`, and
`INTERNAL_ERROR`. Python tracebacks are not returned in API bodies.

File downloads, `/`, and OpenAPI documentation routes are not wrapped.

## Pagination, Filtering, And Sorting

List responses accept common query parameters where the field exists:

- `limit`
- `offset`
- `status`
- `spider_id`
- `task_id`
- `job_id`
- `schedule_id`
- `created_after`
- `created_before`
- `sort_by`
- `sort_order`

Wrapped list responses include:

```json
{
  "pagination": {
    "total": 100,
    "limit": 20,
    "offset": 0,
    "has_more": true
  }
}
```

## Endpoint Groups

Runtime:

- `GET /health`
- `GET /runtime/info`
- `GET /runtime/capabilities`
- `GET /runtime/storage`

Spiders:

- `GET /spiders`
- `POST /spiders`
- `GET /spiders/{spider_id}`
- `PUT /spiders/{spider_id}`
- `DELETE /spiders/{spider_id}`
- `POST /spiders/validate`
- `POST /validate/spider`

Tasks and results:

- `POST /tasks/run`
- `POST /tasks/run/{spider_id}`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/results`
- `GET /tasks/{task_id}/report`
- `GET /tasks/{task_id}/logs`
- `GET /tasks/{task_id}/metrics`

Storage:

- `GET /storage/health`
- `POST /storage/repair`
- `POST /storage/snapshots`
- `GET /storage/snapshots`
- `POST /storage/snapshots/{snapshot_id}/restore`

Scheduler:

- `GET /scheduler/schedules`
- `POST /scheduler/schedules`
- `GET /scheduler/schedules/{schedule_id}`
- `POST /scheduler/schedules/{schedule_id}/trigger`
- `POST /scheduler/schedules/{schedule_id}/pause`
- `POST /scheduler/schedules/{schedule_id}/resume`
- `POST /scheduler/schedules/{schedule_id}/disable`
- `POST /scheduler/run-due`
- `POST /scheduler/enqueue-due`
- `GET /scheduler/runs`

Worker:

- `POST /worker/jobs`
- `GET /worker/jobs`
- `GET /worker/jobs/{job_id}`
- `POST /worker/run-once`
- `POST /worker/run-until-empty`
- `POST /worker/recover`
- `GET /worker/stats`
- `GET /worker/dead-letters`

Lifecycle:

- `POST /tasks/{task_id}/pause`
- `POST /tasks/{task_id}/resume`
- `POST /tasks/{task_id}/cancel`
- `POST /tasks/{task_id}/retry`
- `POST /tasks/{task_id}/rerun`
- `GET /tasks/{task_id}/events`
- `POST /worker/jobs/{job_id}/pause`
- `POST /worker/jobs/{job_id}/resume`
- `POST /worker/jobs/{job_id}/cancel`
- `POST /worker/jobs/{job_id}/retry`
- `GET /worker/jobs/{job_id}/events`

Sessions:

- `GET /sessions`
- `GET /sessions/{profile_id}`
- `POST /sessions/{profile_id}/clear`
- `DELETE /sessions/{profile_id}`
- `GET /sessions/events`

Observability:

- `GET /observability/logs`
- `GET /observability/metrics`
- `GET /observability/reports/tasks/{task_id}`
- `GET /observability/reports/jobs/{job_id}`
- `GET /observability/reports/scheduler/{scheduler_run_id}`
- `GET /observability/traces/{trace_id}`

Exports:

- `POST /exports/tasks/{task_id}`
- `POST /exports/jobs/{job_id}`
- `POST /exports/scheduler/{scheduler_run_id}`
- `POST /exports/observability/logs`
- `GET /exports`
- `GET /exports/{export_id}`
- `GET /exports/{export_id}/download`
- `DELETE /exports/{export_id}`

Examples:

- `GET /examples`
- `GET /examples/{example_id}`
- `POST /examples/validate`
- `POST /examples/smoke`

Incremental storage helpers remain available:

- `GET /incremental/watermarks`
- `GET /incremental/checkpoints`
- `POST /incremental/checkpoints/{task_id}/resume`

## create_app Injection

Use `create_app` to inject deterministic dependencies in tests or local tools:

```python
from crawler_platform.api import create_app
from crawler_platform.http_client import FakeFetcher

app = create_app(
    data_dir="data",
    fetcher=FakeFetcher({"https://example.test/api": "{\"items\": []}"}),
    playwright_fetcher=None,
)
```

The injected `fetcher` and `playwright_fetcher` flow into `CrawlerEngine`,
`SchedulerService`, and `WorkerService`. The API does not require external
network access when tests use fixtures or fake fetchers.

## OpenAPI

`GET /openapi.json` returns the raw FastAPI OpenAPI schema. Major tags are:
`runtime`, `spiders`, `tasks`, `storage`, `scheduler`, `worker`, `lifecycle`,
`sessions`, `observability`, `exports`, and `examples`. Shared schema components include
`ApiResponse`, `ApiMeta`, `PaginationMeta`, and `ApiErrorPayload`.

## Boundary

The API does not include authentication, RBAC, tenant isolation, external
deployment control, or database-backed persistence. Those concerns are outside
the current runtime contract.

## Testing

Run the API-specific tests:

```powershell
pytest -q tests/test_api.py -p no:cacheprovider
```

Run the full verification:

```powershell
python -m compileall -q src tests
pytest -q
python scripts/quality_gate.py --quick --json-report ./test-output/feature20-docs/quick-report.json
python -c "from crawler_platform.api import create_app; app = create_app(); print(type(app).__name__); print(bool(app.openapi()))"
```

Runtime code remains file-backed and does not import or execute database
drivers. Database schema notes remain limited to `docs/schema.sql`.
