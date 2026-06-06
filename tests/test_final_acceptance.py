import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.http_client import FakeFetcher
from crawler_platform.models import FieldRule, SchedulerOptions, SpiderConfig
from scripts import quality_gate, run_test_matrix


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


def _ok(response, status=200):
    assert response.status_code == status
    payload = response.json()
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["meta"]["request_id"]
    assert payload["meta"]["trace_id"]
    return payload["data"], payload["meta"]


def _final_spider(spider_id="final-api"):
    return SpiderConfig(
        id=spider_id,
        name="Final API Smoke",
        type="api",
        start_urls=["https://example.test/final-api"],
        items_json_path="items",
        unique_fields=["id"],
        fields=[
            FieldRule(name="id", type="json_path", json_path="id"),
            FieldRule(name="title", type="json_path", json_path="title"),
        ],
        scheduler=SchedulerOptions(
            enabled=True,
            type="interval",
            interval_seconds=60,
            start_at="2026-06-04T00:00:00Z",
            misfire_policy="run_once",
        ),
    )


def test_final_acceptance_docs_and_status_are_present():
    final_acceptance = Path("docs/final_acceptance.md").read_text(encoding="utf-8")
    delivery = Path("docs/delivery_checklist.md").read_text(encoding="utf-8")
    status = Path("docs/feature_status.md").read_text(encoding="utf-8")

    for feature_number in range(1, 22):
        assert f"Feature {feature_number:02d}" in status
    for feature_number in range(1, 21):
        assert f"Feature {feature_number:02d}" in final_acceptance

    assert "Feature 21 Final Acceptance" in status
    assert "Acceptance Commands" in final_acceptance
    assert "Known Boundaries" in final_acceptance
    assert "Delivery Artifacts" in final_acceptance
    assert "Do Not Commit" in delivery
    assert "test-output/" in delivery
    assert "data/" in delivery


def test_final_acceptance_end_to_end_local_smoke(workspace_tmp_path, capsys):
    pytest.importorskip("fastapi")
    data_dir = workspace_tmp_path / "final acceptance data with spaces"
    fetcher = FakeFetcher({"https://example.test/final-api": '{"items":[{"id":"a","title":"A"},{"id":"b","title":"B"}]}'})
    client = AsgiTestClient(create_app(data_dir, fetcher=fetcher))

    assert main(["validate", "examples/local_api_json.json"]) == 0
    capsys.readouterr()

    spider = _final_spider().to_dict()
    created, _ = _ok(client.post("/spiders", json=spider))
    assert created["id"] == "final-api"

    task, _ = _ok(client.post("/tasks/run", json={"spider_id": "final-api", "task_id": "final-task"}))
    assert task["status"] == "success"
    assert task["saved_records"] == 2

    results, result_meta = _ok(client.get("/tasks/final-task/results?limit=10"))
    assert {record["title"] for record in results} == {"A", "B"}
    assert result_meta["pagination"]["total"] == 2

    report, _ = _ok(client.get("/tasks/final-task/report"))
    logs, _ = _ok(client.get("/observability/logs?task_id=final-task"))
    metrics, _ = _ok(client.get("/observability/metrics?task_id=final-task"))
    assert report["task_id"] == "final-task"
    assert logs
    assert "counters" in metrics

    json_export, _ = _ok(client.post("/exports/tasks/final-task", json={"format": "json"}))
    csv_export, _ = _ok(client.post("/exports/tasks/final-task", json={"format": "csv"}))
    exports, _ = _ok(client.get("/exports"))
    assert {item["export_id"] for item in exports} >= {json_export["export_id"], csv_export["export_id"]}
    assert client.get(f"/exports/{json_export['export_id']}/download").status_code == 200
    assert client.get(f"/exports/{csv_export['export_id']}/download").status_code == 200

    schedule, _ = _ok(client.post("/scheduler/schedules", json=spider))
    assert schedule["registered"] is True
    enqueued, _ = _ok(client.post("/scheduler/run-due", json={"now": "2026-06-05T00:00:00Z", "enqueue": True}))
    assert enqueued
    drained, _ = _ok(client.post("/worker/run-until-empty", json={"worker_id": "final-worker", "max_jobs": 1}))
    assert drained["processed"] == 1
    worker_jobs, _ = _ok(client.get("/worker/jobs"))
    assert any(job["status"] == "succeeded" for job in worker_jobs)

    storage, _ = _ok(client.get("/storage/health"))
    repair, _ = _ok(client.post("/storage/repair?dry_run=true"))
    snapshot, _ = _ok(client.post("/storage/snapshots?name=final-acceptance"))
    restore, _ = _ok(client.post(f"/storage/snapshots/{snapshot['snapshot_id']}/restore?dry_run=true"))
    assert storage["ok"] is True
    assert repair["dry_run"] is True
    assert restore["dry_run"] is True

    admin = client.get("/admin")
    app_js = client.get("/admin/assets/app.js")
    assert admin.status_code == 200
    assert app_js.status_code == 200
    assert "/tasks" in app_js.text
    assert "/exports" in app_js.text
    assert "/observability/logs" in app_js.text
    assert "/examples" in app_js.text

    assert quality_gate.scan_database_dependencies()["status"] == "passed"


def test_final_acceptance_quality_and_matrix_reports_are_readable(workspace_tmp_path):
    quality_path = workspace_tmp_path / "feature21-quality.json"
    matrix_path = workspace_tmp_path / "feature21-matrix.json"

    quality_report = quality_gate.run_quality_gate(mode="quick", json_report=quality_path)
    matrix_report = run_test_matrix.run_test_matrix(mode="quick", json_report=matrix_path)

    for report, path in [(quality_report, quality_path), (matrix_report, matrix_path)]:
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["status"] == report["status"] == "passed"
        assert isinstance(persisted["checks"], list)
        assert persisted["checks"]
        assert persisted["summary"]["failed"] == 0


def test_final_acceptance_runtime_source_keeps_database_boundary():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    matches = []
    for path in list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append((str(path), line_number, line))
    assert matches == []
