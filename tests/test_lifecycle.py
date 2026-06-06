import json
import re

import pytest

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FakeFetcher
from crawler_platform.lifecycle import InvalidLifecycleTransitionError, LifecycleSignal, TaskLifecycleService, WorkerLifecycleService
from crawler_platform.models import FieldRule, PaginationOptions, SchedulerOptions, SpiderConfig, TaskRecord, TaskStatus
from crawler_platform.scheduler import SchedulerService
from crawler_platform.storage import FileStore
from crawler_platform.worker import WorkerService


def _api_spider(spider_id="life-api") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Lifecycle API",
        type="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        unique_fields=["id"],
        fields=[FieldRule(name="id", type="json_path", json_path="id"), FieldRule(name="title", type="json_path", json_path="title")],
    )


def _paged_spider(spider_id="life-paged") -> SpiderConfig:
    spider = _api_spider(spider_id)
    spider.start_urls = ["https://example.test/api?page=1"]
    spider.pagination = PaginationOptions(type="page", page_param="page", max_pages=2)
    return spider


def _html_detail_spider(spider_id="life-detail") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Lifecycle Detail",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        fields=[FieldRule(name="title", type="css", selector="a.title"), FieldRule(name="url", type="attr", selector="a.title", attribute="href")],
    )


def test_task_pause_resume_cancel_pending_and_events(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="task-1", spider_id="demo"))
    service = TaskLifecycleService(store, operator="test")

    paused = service.pause_task("task-1", reason="hold")
    resumed = service.resume_task("task-1", reason="go")
    cancelled = service.cancel_task("task-1", reason="stop")
    events = service.list_task_events("task-1")

    assert paused.status == TaskStatus.PAUSED
    assert resumed.status == TaskStatus.PENDING
    assert cancelled.status == TaskStatus.CANCELLED
    assert [event["event_type"] for event in events] == ["paused", "resumed", "cancelled"]


def test_task_retry_failed_and_cancelled_and_rerun_success(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = TaskLifecycleService(store)
    store.save_task(TaskRecord(id="failed", spider_id="demo", status=TaskStatus.FAILED))
    store.save_task(TaskRecord(id="cancelled", spider_id="demo", status=TaskStatus.CANCELLED))
    store.save_task(TaskRecord(id="success", spider_id="demo", status=TaskStatus.SUCCESS))

    retry_failed = service.retry_task("failed")
    retry_cancelled = service.retry_task("cancelled")
    rerun_success = service.rerun_task("success")

    assert retry_failed.status == TaskStatus.PENDING
    assert retry_failed.source_task_id == "failed"
    assert retry_cancelled.source_task_id == "cancelled"
    assert rerun_success.source_task_id == "success"
    assert store.read_lifecycle_signal("task", "failed") == {}


def test_success_task_rejects_pause_and_cancel_without_force(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="success", spider_id="demo", status=TaskStatus.SUCCESS))
    service = TaskLifecycleService(store)

    with pytest.raises(InvalidLifecycleTransitionError):
        service.pause_task("success")
    with pytest.raises(InvalidLifecycleTransitionError):
        service.cancel_task("success")


def test_invalid_task_transition_is_clear(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="running", spider_id="demo", status=TaskStatus.RUNNING))

    with pytest.raises(InvalidLifecycleTransitionError, match="cannot resume task running in running state"):
        TaskLifecycleService(store).resume_task("running")


def test_engine_running_cancel_signal_stops_at_page_boundary(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _paged_spider()
    fetcher = FakeFetcher(
        {
            "https://example.test/api?page=1": '{"items":[{"id":1,"title":"A"}]}',
            "https://example.test/api?page=2": '{"items":[{"id":2,"title":"B"}]}',
        }
    )
    signal = LifecycleSignal(store, task_id="run-cancel")

    class CancellingFetcher:
        def fetch(self, request):
            response = fetcher.fetch(request)
            TaskLifecycleService(store).cancel_task("run-cancel", reason="test cancel")
            return response

    task = CrawlerEngine(store=store, fetcher=CancellingFetcher()).run(spider, task_id="run-cancel", lifecycle_signal=signal)

    assert task.status == TaskStatus.CANCELLED
    assert store.load_checkpoint("run-cancel")["completed"] is False
    assert len(store.read_records("run-cancel")) == 0


def test_worker_job_pause_resume_claim_cancel_retry_and_events(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    worker = WorkerService(store=store)
    worker.enqueue_spider_run(_api_spider(), job_id="job-1")

    paused = worker.pause_job("job-1")
    assert paused.status == "paused"
    assert store.claim_job("worker-a") is None
    resumed = worker.resume_job("job-1")
    claimed = store.claim_job("worker-a")
    cancelled = WorkerLifecycleService(store).cancel_job("job-1", reason="cancel running")
    marked = WorkerLifecycleService(store).mark_job_cancelled("job-1", worker_id="worker-a")
    retried = worker.retry_job("job-1")
    events = worker.list_job_events("job-1")

    assert resumed.status == "queued"
    assert claimed.job_id == "job-1"
    assert cancelled.status == "cancelling"
    assert marked.status == "cancelled"
    assert retried.status == "queued"
    assert {event["event_type"] for event in events} >= {"paused", "resumed", "cancel_requested", "cancelled", "retried"}


def test_failed_and_dead_letter_job_retry_requeues(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    worker = WorkerService(store=store)
    worker.enqueue_spider_run(_api_spider(), job_id="dead", max_attempts=1)
    claimed = store.claim_job("worker-a")
    store.fail_job(claimed.job_id, "worker-a", {"message": "boom"})

    retried = worker.retry_job("dead")

    assert retried.status == "queued"
    assert retried.attempt == 0
    assert store.claim_job("worker-b").job_id == "dead"


def test_worker_run_once_honors_running_cancel_signal(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    worker = WorkerService(store=store)
    worker.enqueue_spider_run(_api_spider(), job_id="cancel-during-run")

    class CancellingFetcher:
        def fetch(self, request):
            WorkerLifecycleService(store).cancel_job("cancel-during-run", reason="stop job")
            return FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}).fetch(request)

    result = WorkerService(store=store, fetcher=CancellingFetcher()).run_once(worker_id="worker-cancel")

    assert result.status == "cancelled"
    assert store.get_job("cancel-during-run").status == "cancelled"


def test_scheduler_source_job_cancel_updates_scheduler_run(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    scheduler = SchedulerService(store=store)
    spider = _api_spider("scheduled-life")
    spider.scheduler = SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z")
    scheduler.register_spider_schedule(spider)
    scheduler.enqueue_due_jobs(now="2026-06-03T00:00:00Z")
    job = store.list_jobs(source="scheduler")[0]

    WorkerService(store=store).cancel_job(job["job_id"], reason="cancel scheduled job")
    runs = store.list_scheduler_runs(schedule_id="scheduled-life")

    assert runs[0]["status"] == "cancelled"


def test_cli_task_and_worker_job_lifecycle_commands(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="cli-task", spider_id="demo"))
    WorkerService(store=store).enqueue_spider_run(_api_spider(), job_id="cli-job")

    assert main(["task", "pause", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["task", "resume", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["task", "cancel", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["task", "events", "cli-task", "--data-dir", data_dir]) == 0
    assert main(["worker", "job", "pause", "cli-job", "--data-dir", data_dir]) == 0
    assert main(["worker", "job", "resume", "cli-job", "--data-dir", data_dir]) == 0
    assert main(["worker", "job", "cancel", "cli-job", "--data-dir", data_dir]) == 0
    assert main(["worker", "job", "events", "cli-job", "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "cli-task" in output
    assert "cli-job" in output


def test_fastapi_lifecycle_endpoints(workspace_tmp_path):
    pytest.importorskip("fastapi")
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="api-task", spider_id="demo"))
    WorkerService(store=store).enqueue_spider_run(_api_spider(), job_id="api-job")
    app = create_app(workspace_tmp_path)
    task_pause = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/pause")
    task_resume = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/resume")
    task_cancel = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/cancel")
    task_events = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/events")
    job_pause = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs/{job_id}/pause")
    job_resume = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs/{job_id}/resume")
    job_cancel = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs/{job_id}/cancel")
    job_events = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs/{job_id}/events")

    assert task_pause.endpoint("api-task", None)["status"] == "paused"
    assert task_resume.endpoint("api-task", None)["status"] == "pending"
    assert task_cancel.endpoint("api-task")["status"] == "cancelled"
    assert task_events.endpoint("api-task")
    assert job_pause.endpoint("api-job", None)["status"] == "paused"
    assert job_resume.endpoint("api-job", None)["status"] == "queued"
    assert job_cancel.endpoint("api-job")["status"] == "cancelled"
    assert job_events.endpoint("api-job")


def test_storage_check_snapshot_and_runtime_db_boundary_cover_lifecycle(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="life", spider_id="demo"))
    TaskLifecycleService(store).pause_task("life")

    health = store.check_storage()
    manifest = store.create_snapshot(name="life")
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    matches = []
    for path in ["src/crawler_platform/lifecycle.py", "src/crawler_platform/storage.py"]:
        text = __import__("pathlib").Path(path).read_text(encoding="utf-8")
        matches.extend((path, line) for line in text.splitlines() if pattern.search(line))

    assert health["ok"] is True
    assert health["stats"]["lifecycle_events"] == 1
    assert "lifecycle_events" in manifest["included_paths"]
    assert "lifecycle_signals" in manifest["included_paths"]
    assert matches == []


def test_lifecycle_examples_validate(capsys):
    assert main(
        [
            "validate",
            "examples/lifecycle_task_retry.json",
            "examples/lifecycle_task_cancel.json",
            "examples/lifecycle_worker_job.json",
            "examples/lifecycle_scheduler_job.json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(item["valid"] for item in payload)
