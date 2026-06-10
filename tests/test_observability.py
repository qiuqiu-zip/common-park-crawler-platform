import json
import re
from pathlib import Path

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FakeFetcher, FetchError, HttpResponse
from crawler_platform.lifecycle import LifecycleSignal, TaskLifecycleService
from crawler_platform.models import (
    DetailOptions,
    FieldRule,
    ObservabilityConfig,
    PaginationOptions,
    RateLimitConfig,
    RequestOptions,
    RetryConfig,
    SchedulerOptions,
    SessionConfig,
    SpiderConfig,
)
from crawler_platform.scheduler import SchedulerService
from crawler_platform.storage import FileStore
from crawler_platform.validation import validate_spider_config
from crawler_platform.worker import WorkerService


class SequenceFetcher:
    def __init__(self, responses):
        self.responses = {url: list(values) for url, values in responses.items()}
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        values = self.responses.get(request.url)
        if not values:
            raise FetchError(f"No fake response for {request.url}", "network", url=request.url)
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, HttpResponse):
            return value
        return HttpResponse(url=request.url, final_url=request.url, status_code=200, body=value)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _api_spider(spider_id="obs-api", **overrides):
    spider = SpiderConfig(
        id=spider_id,
        name="Observability API",
        type="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        unique_fields=["id"],
        fields=[
            FieldRule(name="id", type="json_path", json_path="id"),
            FieldRule(name="title", type="json_path", json_path="title"),
            FieldRule(name="token", type="json_path", json_path="token", default=""),
        ],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _html_spider(spider_id="obs-html", **overrides):
    spider = SpiderConfig(
        id=spider_id,
        name="Observability HTML",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        fields=[
            FieldRule(name="title", type="css", selector="a.title"),
            FieldRule(name="detail_path", type="attr", selector="a.title", attribute="href"),
        ],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def test_observability_config_validate_and_invalid_log_level():
    valid = _api_spider(observability=ObservabilityConfig(log_level="DEBUG")).to_dict()
    invalid = _api_spider(observability=ObservabilityConfig()).to_dict()
    invalid["observability"]["log_level"] = "TRACE"

    assert validate_spider_config(valid).valid is True
    result = validate_spider_config(invalid)
    assert result.valid is False
    assert result.issues[0].path == "observability.log_level"


def test_filestore_observability_logs_metrics_reports_traces_and_health(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.append_log("tasks", "task-1", {"level": "INFO", "component": "test", "event_type": "started", "message": "ok"})
    store.record_metric("tasks", "task-1", {"name": "requests_total", "kind": "counter", "value": 2})
    store.create_run_report("task", "task-1", {"task_id": "task-1", "status": "success"})
    store.create_trace({"trace_id": "trace-1", "event_type": "trace_created"})
    store.append_trace_event("trace-1", {"event_type": "fetch_started"})

    assert store.iter_logs(scope="tasks", target_id="task-1")[0]["event_type"] == "started"
    assert store.summarize_metrics(scope="tasks", target_id="task-1")["counters"]["requests_total"] == 2
    assert store.get_run_report("task", "task-1")["status"] == "success"
    assert [event["event_type"] for event in store.get_trace("trace-1")["events"]] == ["trace_created", "fetch_started"]
    assert store.check_storage()["stats"]["observability_logs"] == 1
    assert "observability" in store.create_snapshot(name="obs")["included_paths"]


def test_task_run_generates_logs_metrics_report_trace_and_redacts_samples(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A","token":"secret-token"}]}'})
    store = FileStore(workspace_tmp_path)
    task = CrawlerEngine(store=store, fetcher=fetcher).run(_api_spider(), task_id="task-obs")

    logs = store.iter_logs(scope="tasks", target_id=task.id)
    metrics = store.summarize_metrics(scope="tasks", target_id=task.id)
    report = store.get_run_report("task", task.id)
    trace = store.get_trace(report["trace_id"])
    event_types = {event["event_type"] for event in trace["events"]}

    assert any(item["event_type"] == "task_finished" for item in logs)
    assert metrics["counters"]["requests_total"] == 1
    assert metrics["counters"]["records_saved"] == 1
    assert report["trace_id"]
    assert report["session_loads"] == 0
    assert report["request_governance"]["session"]["enabled"] is False
    assert report["record_samples"][0]["token"] == "***REDACTED***"
    assert {"request_built", "fetch_started", "fetch_finished", "parse_started", "parse_finished", "extract_started", "extract_finished", "result_saved"} <= event_types


def test_pagination_detail_checkpoint_and_watermark_trace_events(workspace_tmp_path):
    spider = _html_spider(
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/page-2"], max_pages=2),
        detail=DetailOptions(enabled=True, url_field="detail_path", fields=[FieldRule(name="body", type="css", selector=".body")]),
        watermark={"enabled": True, "field": "title", "dataset": "obs", "strategy": "max"},
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/a">A</a></article>',
            "https://example.test/page-2": '<article class="item"><a class="title" href="/detail/b">B</a></article>',
            "https://example.test/detail/a": '<div class="body">Detail A</div>',
            "https://example.test/detail/b": '<div class="body">Detail B</div>',
        }
    )
    store = FileStore(workspace_tmp_path)
    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="task-trace")

    report = store.get_run_report("task", task.id)
    event_types = {event["event_type"] for event in store.get_trace(report["trace_id"])["events"]}
    metrics = store.summarize_metrics(scope="tasks", target_id=task.id)["counters"]

    assert {"pagination_next", "detail_started", "detail_finished", "checkpoint_saved", "watermark_updated"} <= event_types
    assert metrics["checkpoint_saves"] >= 1
    assert metrics["watermark_updates"] == 1


def test_retry_rate_limit_session_and_failed_task_report(workspace_tmp_path):
    clock = FakeClock()
    spider = _api_spider(
        retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"], backoff="none"),
        rate_limit=RateLimitConfig(enabled=True, requests_per_second=1),
        session=SessionConfig(enabled=True, profile="obs-session"),
    )
    fetcher = SequenceFetcher(
        {
            "https://example.test/api": [
                FetchError("down", "network", url="https://example.test/api"),
                HttpResponse(url="https://example.test/api", status_code=200, body='{"items":[{"id":1,"title":"A"}]}', headers={"Set-Cookie": "sid=abc"}),
            ]
        }
    )
    store = FileStore(workspace_tmp_path)
    task = CrawlerEngine(store=store, fetcher=fetcher, sleep=clock.sleep, clock=clock.clock).run(spider, task_id="task-governed")
    metrics = store.summarize_metrics(scope="tasks", target_id=task.id)["counters"]

    assert metrics["retry_attempts"] == 1
    assert metrics["retry_successes"] == 1
    assert metrics["rate_limit_wait_seconds"] == 1
    assert metrics["session_loads"] >= 1
    assert metrics["session_saves"] >= 1

    failed = CrawlerEngine(store=store, fetcher=FakeFetcher({"https://example.test/api": FetchError("boom", "network")})).run(
        _api_spider("failed-obs"), task_id="failed-task"
    )
    report = store.get_run_report("task", failed.id)
    assert report["status"] == "failed"
    assert report["errors_count"] >= 1
    assert report["top_errors"][0]["key"] == "network"


def test_task_report_marks_zero_record_quality_as_unknown(workspace_tmp_path):
    spider = _html_spider("obs-empty", item_selector=".missing")
    store = FileStore(workspace_tmp_path)
    task = CrawlerEngine(store=store, fetcher=FakeFetcher({"https://example.test/list": "<main><p>still loading</p></main>"})).run(
        spider,
        task_id="obs-empty-task",
    )

    report = store.get_run_report("task", task.id)
    assert report["saved_records"] == 0
    assert report["record_quality_status"] == "unknown"
    assert report["field_quality"][0]["status"] == "unknown"
    assert report["field_quality"][0]["hint"] == "no records were extracted; field completeness was not evaluated"


def test_cancelled_task_and_lifecycle_events_are_observable(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.write_lifecycle_signal("task", "cancel-task", {"cancel_requested": True, "reason": "test"})
    signal = LifecycleSignal(store, task_id="cancel-task")
    task = CrawlerEngine(store=store, fetcher=FakeFetcher({"https://example.test/api": '{"items":[]}'})).run(
        _api_spider(), task_id="cancel-task", lifecycle_signal=signal
    )
    TaskLifecycleService(store=store).retry_task(task.id, reason="verify lifecycle log")

    report = store.get_run_report("task", task.id)
    logs = store.iter_logs(target_id=task.id)

    assert report["status"] == "cancelled"
    assert any(item["event_type"].startswith("lifecycle_") for item in logs)


def test_worker_job_reports_success_and_failure(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    worker = WorkerService(store=store, fetcher=FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}))
    worker.enqueue_spider_run(_api_spider(), job_id="job-ok")
    success = worker.run_once(worker_id="worker-ok")

    assert success.status == "succeeded"
    assert store.get_run_report("job", "job-ok")["status"] == "succeeded"
    assert store.summarize_metrics(scope="jobs", target_id="job-ok")["counters"]["jobs_succeeded"] == 1

    failing = WorkerService(store=store, fetcher=FakeFetcher({"https://example.test/api": FetchError("down", "network")}))
    failing.enqueue_spider_run(_api_spider("worker-fail"), job_id="job-fail", max_attempts=1)
    failed = failing.run_once(worker_id="worker-fail")

    assert failed.status == "dead_letter"
    assert store.get_run_report("job", "job-fail")["status"] == "dead_letter"


def test_scheduler_reports_and_enqueue_trace_link(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    engine = CrawlerEngine(store=store, fetcher=FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}))
    scheduler = SchedulerService(store=store, engine=engine)
    spider = _api_spider("sched-direct", scheduler=SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"))
    scheduler.register_spider_schedule(spider)
    direct = scheduler.run_due_jobs(now="2026-06-03T00:00:00Z")
    direct_report = store.get_run_report("scheduler", direct[0]["id"])

    assert direct_report["status"] == "success"
    assert direct_report["trace_id"]

    enqueue_spider = _api_spider("sched-enqueue", scheduler=SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"))
    scheduler.register_spider_schedule(enqueue_spider)
    enqueued = scheduler.enqueue_due_jobs(now="2026-06-03T00:00:00Z")[0]
    scheduler_run = enqueued["scheduler_run"]

    assert enqueued["job"]["metadata"]["trace_id"] == scheduler_run["summary"]["trace_id"]
    assert store.get_run_report("scheduler", scheduler_run["id"])["job_id"] == enqueued["job"]["job_id"]


def test_cli_observability_commands(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)
    assert main(["run", "examples/local_api_json.json", "--task-id", "cli-task", "--data-dir", data_dir]) == 0
    report = FileStore(workspace_tmp_path).get_run_report("task", "cli-task")

    assert main(["observability", "logs", "--task-id", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["observability", "metrics", "--task-id", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["observability", "report", "task", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["observability", "trace", report["trace_id"], "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "task_finished" in output
    assert "records_saved" in output
    assert "Trace" in output


def test_cli_observability_report_json_escapes_non_ascii(workspace_tmp_path, capsys):
    store = FileStore(workspace_tmp_path)
    store.create_run_report("task", "unicode-task", {"task_id": "unicode-task", "sample": {"price": "£51.77"}})

    assert main(["observability", "report", "task", "unicode-task", "--data-dir", str(workspace_tmp_path), "--json"]) == 0

    output = capsys.readouterr().out
    assert "\\u00a3" in output
    assert json.loads(output)["sample"]["price"] == "£51.77"


def test_fastapi_observability_endpoints(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'})
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    logs_route = next(route for route in app.routes if getattr(route, "path", None) == "/observability/logs")
    metrics_route = next(route for route in app.routes if getattr(route, "path", None) == "/observability/metrics")
    report_route = next(route for route in app.routes if getattr(route, "path", None) == "/observability/reports/tasks/{task_id}")
    trace_route = next(route for route in app.routes if getattr(route, "path", None) == "/observability/traces/{trace_id}")

    run_route.endpoint({"spider": _api_spider().to_dict(), "task_id": "api-obs"})
    report = report_route.endpoint("api-obs")

    assert logs_route.endpoint(task_id="api-obs", job_id=None, schedule_id=None, scheduler_run_id=None, level=None, limit=None, offset=0)
    assert metrics_route.endpoint(task_id="api-obs", job_id=None, schedule_id=None, scheduler_run_id=None)["counters"]["records_saved"] == 1
    assert trace_route.endpoint(report["trace_id"])["trace_id"] == report["trace_id"]


def test_observability_runtime_keeps_dependency_boundary():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    paths = list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]

    matches = [str(path) for path in paths if pattern.search(path.read_text(encoding="utf-8"))]

    assert matches == []
