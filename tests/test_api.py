import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from crawler_platform.api import create_app
from crawler_platform.examples import get_example
from crawler_platform.http_client import FakeFetcher
from crawler_platform.models import FieldRule, SchedulerOptions, SpiderConfig, TaskRecord, TaskStatus
from crawler_platform.observability import log_event, start_trace
from crawler_platform.storage import FileStore
from crawler_platform.worker import WorkerService


def _test_client(app):
    pytest.importorskip("fastapi")
    return AsgiTestClient(app)


class AsgiResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.content = body
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class AsgiTestClient:
    def __init__(self, app):
        self.app = app

    def get(self, url, *, headers=None):
        return asyncio.run(self._request("GET", url, headers=headers))

    def post(self, url, *, json=None, headers=None):
        return asyncio.run(self._request("POST", url, json_body=json, headers=headers))

    def put(self, url, *, json=None, headers=None):
        return asyncio.run(self._request("PUT", url, json_body=json, headers=headers))

    def delete(self, url, *, headers=None):
        return asyncio.run(self._request("DELETE", url, headers=headers))

    async def _request(self, method, url, *, json_body=None, headers=None):
        parsed = urlsplit(url)
        path = parsed.path or "/"
        body = b""
        request_headers = [(b"accept", b"application/json")]
        for key, value in (headers or {}).items():
            request_headers.append((key.lower().encode("ascii"), str(value).encode("utf-8")))
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers.append((b"content-type", b"application/json"))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": request_headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }
        sent = False
        status_code = None
        response_headers = {}
        chunks = []

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(status_code, response_headers, b"".join(chunks))


def _api_spider(spider_id="api-demo", *, scheduler=None):
    return SpiderConfig(
        id=spider_id,
        name="API Demo",
        type="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        unique_fields=["id"],
        fields=[
            FieldRule(name="id", type="json_path", json_path="id"),
            FieldRule(name="title", type="json_path", json_path="title"),
        ],
        scheduler=scheduler or SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"),
    )


def _html_spider(spider_id="html-demo"):
    return SpiderConfig(
        id=spider_id,
        name="HTML Demo",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        fields=[FieldRule(name="title", type="css", selector=".title")],
    )


def _client_and_store(workspace_tmp_path):
    fetcher = FakeFetcher(
        {
            "https://example.test/api": '{"items":[{"id":1,"title":"A"},{"id":2,"title":"B"}]}',
            "https://example.test/api-alt": '{"items":[{"id":3,"title":"C"}]}',
            "https://example.test/list": '<article class="item"><span class="title">HTML A</span></article>',
        }
    )
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    return _test_client(app), FileStore(workspace_tmp_path)


def _ok(response, status=200):
    assert response.status_code == status
    payload = response.json()
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["meta"]["request_id"]
    assert payload["meta"]["trace_id"]
    assert payload["meta"]["timestamp"]
    return payload["data"], payload["meta"]


def _error(response, status, code):
    assert response.status_code == status
    payload = response.json()
    assert payload["ok"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == code
    assert "Traceback" not in response.text
    return payload["error"]


def test_runtime_openapi_envelope_and_error_shape(workspace_tmp_path):
    client, _ = _client_and_store(workspace_tmp_path)

    data, meta = _ok(client.get("/health", headers={"X-Request-ID": "req-1"}))
    assert data["status"] == "ok"
    assert data["database"]["enabled"] is False
    assert meta["request_id"] == "req-1"

    runtime, _ = _ok(client.get("/runtime/info"))
    capabilities, _ = _ok(client.get("/runtime/capabilities"))
    storage, _ = _ok(client.get("/runtime/storage"))
    assert runtime["database"]["runtime_dependency"] is False
    assert capabilities["api"]["response_envelope"] is True
    assert storage["health"]["ok"] is True

    schema = client.get("/openapi.json").json()
    assert "ok" not in schema
    assert "ApiResponse" in schema["components"]["schemas"]
    assert set(["/health", "/runtime/info", "/spiders", "/tasks/run", "/storage/health"]).issubset(schema["paths"])
    tags = {tag for path in schema["paths"].values() for operation in path.values() if isinstance(operation, dict) for tag in operation.get("tags", [])}
    assert {"runtime", "spiders", "tasks", "storage", "scheduler", "worker", "lifecycle", "sessions", "observability", "exports"}.issubset(tags)

    _error(client.get("/tasks/not-found"), 404, "NOT_FOUND")


def test_spider_task_result_and_storage_management_api(workspace_tmp_path):
    client, _ = _client_and_store(workspace_tmp_path)
    spider = _api_spider().to_dict()

    created, _ = _ok(client.post("/spiders", json=spider))
    assert created["id"] == "api-demo"
    listed, list_meta = _ok(client.get("/spiders?limit=1&offset=0&sort_by=id"))
    assert listed[0]["id"] == "api-demo"
    assert list_meta["pagination"]["total"] == 1
    shown, _ = _ok(client.get("/spiders/api-demo"))
    assert shown["id"] == "api-demo"

    updated_payload = {**spider, "name": "API Demo Updated"}
    updated, _ = _ok(client.put("/spiders/api-demo", json=updated_payload))
    assert updated["name"] == "API Demo Updated"
    validation, _ = _ok(client.post("/spiders/validate", json=updated_payload))
    assert validation["valid"] is True
    invalid = _error(client.post("/spiders", json={"id": "bad"}), 422, "VALIDATION_ERROR")
    assert invalid["details"]

    task, _ = _ok(client.post("/tasks/run", json={"spider_id": "api-demo", "task_id": "api-task"}))
    assert task["status"] == "success"
    task_with_route_override, _ = _ok(client.post("/tasks/run/api-demo?start_url=https://example.test/api-alt"))
    assert task_with_route_override["status"] == "success"
    tasks, _ = _ok(client.get("/tasks?status=success"))
    assert {task["id"] for task in tasks} == {"api-task", task_with_route_override["id"]}
    task_detail, _ = _ok(client.get("/tasks/api-task"))
    assert task_detail["saved_records"] == 2
    results, result_meta = _ok(client.get("/tasks/api-task/results?limit=1&offset=1"))
    assert [item["title"] for item in results] == ["B"]
    route_override_results, _ = _ok(client.get(f"/tasks/{task_with_route_override['id']}/results?limit=10"))
    assert [item["title"] for item in route_override_results] == ["C"]
    assert result_meta["pagination"]["total"] == 2
    shown_after_override, _ = _ok(client.get("/spiders/api-demo"))
    assert shown_after_override["start_urls"] == ["https://example.test/api"]
    report, _ = _ok(client.get("/tasks/api-task/report"))
    assert report["task_id"] == "api-task"
    logs, _ = _ok(client.get("/tasks/api-task/logs"))
    metrics, _ = _ok(client.get("/tasks/api-task/metrics"))
    assert isinstance(logs, list)
    assert "counters" in metrics

    storage, _ = _ok(client.get("/storage/health"))
    assert storage["ok"] is True
    repair, _ = _ok(client.post("/storage/repair?dry_run=true"))
    assert repair["dry_run"] is True
    snapshot, _ = _ok(client.post("/storage/snapshots?name=api-test"))
    snapshots, _ = _ok(client.get("/storage/snapshots"))
    restored, _ = _ok(client.post(f"/storage/snapshots/{snapshot['snapshot_id']}/restore?dry_run=true"))
    assert snapshots[0]["snapshot_id"] == snapshot["snapshot_id"]
    assert restored["dry_run"] is True

    deleted, _ = _ok(client.delete("/spiders/api-demo"))
    assert deleted["deleted"] == "api-demo"


def test_scheduler_worker_and_lifecycle_api(workspace_tmp_path):
    client, store = _client_and_store(workspace_tmp_path)
    schedule_spider = _api_spider("scheduled-api").to_dict()

    registered, _ = _ok(client.post("/scheduler/schedules", json={"spider": schedule_spider}))
    assert registered["registered"] is True
    schedules, _ = _ok(client.get("/scheduler/schedules"))
    assert schedules[0]["id"] == "scheduled-api"
    due, _ = _ok(client.post("/scheduler/run-due", json={"now": "2026-06-03T00:00:00Z"}))
    assert due[0]["status"] == "success"
    triggered, _ = _ok(client.post("/scheduler/schedules/scheduled-api/trigger"))
    assert triggered["status"] == "success"
    paused, _ = _ok(client.post("/scheduler/schedules/scheduled-api/pause"))
    resumed, _ = _ok(client.post("/scheduler/schedules/scheduled-api/resume"))
    disabled, _ = _ok(client.post("/scheduler/schedules/scheduled-api/disable"))
    runs, _ = _ok(client.get("/scheduler/runs?schedule_id=scheduled-api"))
    enqueued, _ = _ok(client.post("/scheduler/enqueue-due", json={"now": "2026-06-03T00:01:00Z"}))
    assert paused["status"] == "paused"
    assert resumed["status"] == "enabled"
    assert disabled["status"] == "disabled"
    assert len(runs) == 2
    assert isinstance(enqueued, list)

    queued, _ = _ok(client.post("/worker/jobs", json={"spider": _api_spider("worker-api").to_dict(), "source": "api"}))
    paused_job, _ = _ok(client.post(f"/worker/jobs/{queued['job_id']}/pause"))
    resumed_job, _ = _ok(client.post(f"/worker/jobs/{queued['job_id']}/resume"))
    cancelled_job, _ = _ok(client.post(f"/worker/jobs/{queued['job_id']}/cancel"))
    retried_job, _ = _ok(client.post(f"/worker/jobs/{queued['job_id']}/retry"))
    events, _ = _ok(client.get(f"/worker/jobs/{queued['job_id']}/events"))
    assert paused_job["status"] == "paused"
    assert resumed_job["status"] == "queued"
    assert cancelled_job["status"] == "cancelled"
    assert retried_job["status"] == "queued"
    assert events

    run, _ = _ok(client.post("/worker/run-once", json={"worker_id": "api-worker"}))
    stats, _ = _ok(client.get("/worker/stats"))
    jobs, _ = _ok(client.get("/worker/jobs"))
    job_detail, _ = _ok(client.get(f"/worker/jobs/{queued['job_id']}"))
    recovered, _ = _ok(client.post("/worker/recover"))
    dead_letters, _ = _ok(client.get("/worker/dead-letters"))
    assert run["status"] == "succeeded"
    assert stats["succeeded_jobs"] == 1
    assert jobs[0]["job_id"] == queued["job_id"]
    assert job_detail["status"] == "succeeded"
    assert recovered == []
    assert dead_letters == []

    WorkerService(store=store).enqueue_spider_run(_html_spider("batch-html"), job_id="batch-job")
    drained, _ = _ok(client.post("/worker/run-until-empty", json={"worker_id": "batch", "max_jobs": 1}))
    assert drained["processed"] == 1

    store.save_task(TaskRecord(id="pending-task", spider_id="api-demo", status=TaskStatus.PENDING))
    task_paused, _ = _ok(client.post("/tasks/pending-task/pause"))
    task_resumed, _ = _ok(client.post("/tasks/pending-task/resume"))
    task_cancelled, _ = _ok(client.post("/tasks/pending-task/cancel"))
    task_retried, _ = _ok(client.post("/tasks/pending-task/retry"))
    task_events, _ = _ok(client.get("/tasks/pending-task/events"))
    assert task_paused["status"] == "paused"
    assert task_resumed["status"] == "pending"
    assert task_cancelled["status"] == "cancelled"
    assert task_retried["source_task_id"] == "pending-task"
    assert task_events

    store.save_task(TaskRecord(id="success-task", spider_id="api-demo", status=TaskStatus.SUCCESS))
    rerun, _ = _ok(client.post("/tasks/success-task/rerun"))
    assert rerun["source_task_id"] == "success-task"
    _error(client.post("/tasks/success-task/pause"), 409, "INVALID_STATE")


def test_example_save_as_spider_and_task_report_fallback_api(workspace_tmp_path):
    client, store = _client_and_store(workspace_tmp_path)

    example = get_example("local-html-list")
    created, _ = _ok(client.post("/spiders", json=example["config"]))
    shown, _ = _ok(client.get(f"/spiders/{created['id']}"))
    validation, _ = _ok(client.post("/spiders/validate", json=shown))
    assert created["id"] == "local-html-list-demo"
    assert validation["valid"] is True

    store.save_task(
        TaskRecord(
            id="fallback-task",
            spider_id=created["id"],
            status=TaskStatus.SUCCESS,
            total_requests=3,
            success_requests=3,
            saved_records=1,
        )
    )
    store.append_record("fallback-task", {"id": "sample-1", "title": "Fallback record"})

    report, _ = _ok(client.get("/tasks/fallback-task/report"))
    assert report["task_id"] == "fallback-task"
    assert report["status"] == "success"
    assert report["saved_records"] == 1
    assert report["record_quality_status"] == "unknown"
    assert report["record_samples"][0]["title"] == "Fallback record"


def test_sessions_observability_exports_and_redaction_api(workspace_tmp_path):
    client, store = _client_and_store(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo", "headers": {"Authorization": "Bearer raw-token"}})
    store.save_cookies("demo", {"sid": "secret-cookie"})
    store.record_session_event({"event_id": "session-event", "profile_id": "demo", "event_type": "session_saved"})

    sessions, _ = _ok(client.get("/sessions"))
    shown, _ = _ok(client.get("/sessions/demo"))
    session_events, _ = _ok(client.get("/sessions/events?profile_id=demo"))
    cleared, _ = _ok(client.post("/sessions/demo/clear"))
    assert sessions[0]["profile_id"] == "demo"
    assert shown["cookies"] == {"sid": "***REDACTED***"}
    assert shown["profile"]["headers"]["Authorization"] == "***REDACTED***"
    assert session_events[0]["event_id"] == "session-event"
    assert cleared["profile_id"] == "demo"

    trace_id = "trace-api"
    start_trace(store, trace_id, metadata={"token": "secret"})
    log_event(store, None, component="api", event_type="api_test", message="hello", trace_id=trace_id, task_id="api-task", metadata={"token": "secret"})
    store.record_metric("tasks", "api-task", {"name": "records_saved", "kind": "counter", "value": 2})
    store.create_run_report("task", "api-task", {"task_id": "api-task", "status": "success"})

    logs, _ = _ok(client.get("/observability/logs?task_id=api-task"))
    metrics, _ = _ok(client.get("/observability/metrics?task_id=api-task"))
    report, _ = _ok(client.get("/observability/reports/tasks/api-task"))
    trace, _ = _ok(client.get("/observability/traces/trace-api"))
    assert logs[0]["metadata"]["token"] == "***REDACTED***"
    assert metrics["counters"]["records_saved"] == 2
    assert report["status"] == "success"
    assert trace["events"][0]["metadata"]["token"] == "***REDACTED***"

    store.save_task(TaskRecord(id="export-task", spider_id="api-demo", status=TaskStatus.SUCCESS))
    store.append_record("export-task", {"id": "a", "name": "A", "access_token": "raw"})
    manifest, _ = _ok(client.post("/exports/tasks/export-task", json={"format": "json"}))
    exports, _ = _ok(client.get("/exports"))
    shown_export, _ = _ok(client.get(f"/exports/{manifest['export_id']}"))
    downloaded = client.get(f"/exports/{manifest['export_id']}/download")
    deleted, _ = _ok(client.delete(f"/exports/{manifest['export_id']}"))
    assert exports[0]["export_id"] == manifest["export_id"]
    assert shown_export["rows_count"] == 1
    assert downloaded.status_code == 200
    assert "raw" not in downloaded.text
    assert deleted["export_id"] == manifest["export_id"]


def test_api_runtime_source_keeps_database_dependency_boundary():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    matches = []
    for path in list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append((str(path), line_number, line))

    assert matches == []
