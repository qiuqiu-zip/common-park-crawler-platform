import json
import re
import threading
from pathlib import Path

import pytest

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.http_client import FakeFetcher, FetchError
from crawler_platform.models import FieldRule, PlaywrightOptions, SchedulerOptions, SpiderConfig, WorkerJob
from crawler_platform.playwright_runner import FakeRenderBackend, PlaywrightFetcher
from crawler_platform.scheduler import SchedulerService
from crawler_platform.storage import FileStore
from crawler_platform.worker import FakeClock, WorkerService


def _api_spider(spider_id="worker-api") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Worker API",
        type="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        unique_fields=["id"],
        fields=[FieldRule(name="id", type="json_path", json_path="id"), FieldRule(name="title", type="json_path", json_path="title")],
    )


def _html_spider(spider_id="worker-html") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Worker HTML",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        fields=[FieldRule(name="title", type="css", selector="a.title")],
    )


def _playwright_spider(spider_id="worker-pw") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Worker PW",
        type="playwright",
        start_urls=["https://example.test/rendered"],
        item_selector="article.result",
        unique_fields=["title"],
        fields=[FieldRule(name="title", type="css", selector="a.title")],
        playwright=PlaywrightOptions(enabled=True, browser_pool_size=1),
    )


def _api_fetcher():
    return FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'})


def test_enqueue_job_creates_queued_job_and_storage_health_covers_queue(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    job = WorkerService(store=store).enqueue_spider_run(_api_spider(), source="manual", priority=5)

    loaded = store.get_job(job.job_id)
    health = store.check_storage()

    assert loaded.status == "queued"
    assert loaded.priority == 5
    assert health["ok"] is True
    assert health["stats"]["queue_jobs"] == 1


def test_concurrent_claim_only_allows_one_worker(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    WorkerService(store=store).enqueue_spider_run(_api_spider(), job_id="claim-me")
    results = []

    def claim(worker_id):
        results.append(store.claim_job(worker_id, now="2026-06-03T00:00:00Z"))

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    assert store.get_job("claim-me").lease_owner in {f"worker-{index}" for index in range(8)}


def test_priority_and_run_after_control_claim_order(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = WorkerService(store=store)
    service.enqueue_spider_run(_api_spider("low"), priority=1, job_id="low")
    service.enqueue_spider_run(_api_spider("high"), priority=10, job_id="high")
    service.enqueue_spider_run(_api_spider("later"), priority=99, run_after="2026-06-03T01:00:00Z", job_id="later")

    first = store.claim_job("worker-a", now="2026-06-03T00:00:00Z")
    second = store.claim_job("worker-b", now="2026-06-03T00:00:00Z")
    third = store.claim_job("worker-c", now="2026-06-03T01:00:00Z")

    assert first.job_id == "high"
    assert second.job_id == "low"
    assert third.job_id == "later"


def test_heartbeat_and_recover_expired_lease(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    WorkerService(store=store).enqueue_spider_run(_api_spider(), job_id="leased")
    claimed = store.claim_job("worker-a", now="2026-06-03T00:00:00Z", lease_seconds=60)

    heartbeat = store.heartbeat_job(claimed.job_id, "worker-a", now="2026-06-03T00:00:30Z", lease_seconds=60)
    not_recovered = store.requeue_expired_leases(now="2026-06-03T00:00:59Z")
    recovered = store.requeue_expired_leases(now="2026-06-03T00:01:31Z")

    assert heartbeat.heartbeat_at == "2026-06-03T00:00:30+00:00"
    assert not_recovered == []
    assert recovered[0]["status"] == "queued"
    assert store.get_queue_stats()["heartbeat_count"] == 1


def test_fail_requeues_until_max_attempts_then_dead_letters(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = WorkerService(store=store)
    service.enqueue_spider_run(_api_spider(), job_id="retry-me", max_attempts=2)

    first = store.claim_job("worker-a", now="2026-06-03T00:00:00Z")
    requeued = store.fail_job(first.job_id, "worker-a", {"message": "boom"})
    second = store.claim_job("worker-b", now="2026-06-03T00:01:00Z")
    dead = store.fail_job(second.job_id, "worker-b", {"message": "boom"})

    assert requeued.status == "queued"
    assert dead.status == "dead_letter"
    assert store.list_jobs(status="dead_letter")[0]["job_id"] == "retry-me"


def test_cancel_queued_job_and_running_cancel_is_non_destructive(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = WorkerService(store=store)
    service.enqueue_spider_run(_api_spider(), job_id="queued-cancel")
    service.enqueue_spider_run(_api_spider("running-spider"), job_id="running-cancel", priority=10)
    store.claim_job("worker-a", now="2026-06-03T00:00:00Z")

    queued = store.cancel_job("queued-cancel")
    running = store.cancel_job("running-cancel")

    assert queued.status == "cancelled"
    assert running.status == "running"
    assert running.warnings[-1]["type"] == "cancel_ignored"


def test_run_once_executes_api_spider_successfully(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    fetcher = _api_fetcher()
    service = WorkerService(store=store, fetcher=fetcher)
    service.enqueue_spider_run(_api_spider(), job_id="api-job")

    result = service.run_once(worker_id="worker-api")

    assert result.status == "succeeded"
    assert store.get_job("api-job").status == "succeeded"
    assert store.read_records(result.task_id)[0]["title"] == "A"


def test_run_once_executes_html_spider_successfully(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    fetcher = FakeFetcher({"https://example.test/list": '<article class="item"><a class="title">HTML A</a></article>'})
    service = WorkerService(store=store, fetcher=fetcher)
    service.enqueue_spider_run(_html_spider(), job_id="html-job")

    result = service.run_once(worker_id="worker-html")

    assert result.status == "succeeded"
    assert store.read_records(result.task_id)[0]["title"] == "HTML A"


def test_run_once_executes_playwright_spider_with_fake_render_backend(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    backend = FakeRenderBackend({"https://example.test/rendered": '<article class="result"><a class="title">Rendered A</a></article>'})
    service = WorkerService(
        store=store,
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    )
    service.enqueue_spider_run(_playwright_spider(), job_id="pw-job")

    result = service.run_once(worker_id="worker-pw")

    assert result.status == "succeeded"
    assert backend.rendered_urls == ["https://example.test/rendered"]


def test_run_until_empty_processes_multiple_jobs_and_records_stats(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = WorkerService(store=store, fetcher=_api_fetcher(), max_concurrent_jobs=2)
    service.enqueue_spider_run(_api_spider("a"), job_id="job-a")
    service.enqueue_spider_run(_api_spider("b"), job_id="job-b")

    result = service.run_until_empty(worker_id="batch")
    stats = service.stats()

    assert result["processed"] == 2
    assert stats["succeeded_jobs"] == 2
    assert stats["concurrency_peak"] == 2


def test_polling_loop_stops_cleanly_with_max_iterations(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    service = WorkerService(store=store, fetcher=_api_fetcher())
    service.enqueue_spider_run(_api_spider(), job_id="poll-job")

    result = service.start_polling(worker_id="poller", interval_seconds=0, max_iterations=2)

    assert result["processed"] == 1
    assert store.get_worker_state("poller").status == "stopped"


def test_worker_failure_uses_execution_attempts_not_request_retries(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    fetcher = FakeFetcher({"https://example.test/api": FetchError("down", "network", url="https://example.test/api")})
    service = WorkerService(store=store, fetcher=fetcher)
    spider = _api_spider()
    spider.request.max_retries = 0
    service.enqueue_spider_run(spider, job_id="fail-once", max_attempts=2)

    first = service.run_once(worker_id="failing")
    second = service.run_once(worker_id="failing")

    assert first.status == "retried"
    assert second.status == "dead_letter"
    assert store.get_job("fail-once").attempt == 2


def test_scheduler_due_jobs_enqueue_worker_jobs_idempotently(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    scheduler = SchedulerService(store=store)
    spider = _api_spider("scheduled-worker")
    spider.scheduler = SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z")
    scheduler.register_spider_schedule(spider)

    first = scheduler.enqueue_due_jobs(now="2026-06-03T00:00:00Z")
    second = scheduler.enqueue_due_jobs(now="2026-06-03T00:00:30Z")

    assert first[0]["status"] == "queued"
    assert second == []
    assert store.list_jobs(source="scheduler")[0]["schedule_id"] == "scheduled-worker"


def test_worker_executes_scheduler_source_job_and_updates_scheduler_run(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    fetcher = _api_fetcher()
    scheduler = SchedulerService(store=store)
    spider = _api_spider("scheduled-worker-run")
    spider.scheduler = SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z")
    scheduler.register_spider_schedule(spider)
    scheduler.enqueue_due_jobs(now="2026-06-03T00:00:00Z")

    result = WorkerService(store=store, fetcher=fetcher).run_once(worker_id="scheduler-worker")
    scheduler_runs = store.list_scheduler_runs(schedule_id="scheduled-worker-run")

    assert result.status == "succeeded"
    assert scheduler_runs[0]["status"] == "success"
    assert scheduler_runs[0]["task_id"] == result.task_id


def test_cli_worker_commands(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)

    assert main(["worker", "enqueue", "examples/worker_api_job.json", "--data-dir", data_dir]) == 0
    assert main(["worker", "jobs", "--data-dir", data_dir]) == 0
    assert main(["worker", "run-once", "--data-dir", data_dir]) == 0
    assert main(["worker", "stats", "--data-dir", data_dir]) == 0
    assert main(["worker", "enqueue", "examples/worker_html_job.json", "--data-dir", data_dir]) == 0
    assert main(["worker", "run-until-empty", "--data-dir", data_dir]) == 0
    assert main(["worker", "recover", "--data-dir", data_dir]) == 0
    assert main(["worker", "dead-letters", "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "worker-api-job-demo" in output
    assert "succeeded_jobs" in output


def test_cli_scheduler_enqueue_due(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)

    assert main(["scheduler", "register", "examples/scheduled_worker_job.json", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "enqueue-due", "--now", "2026-06-03T00:00:00Z", "--data-dir", data_dir]) == 0
    assert main(["worker", "jobs", "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "scheduled-worker-job-demo" in output
    assert "queued" in output


def test_fastapi_worker_endpoints(workspace_tmp_path):
    pytest.importorskip("fastapi")
    app = create_app(workspace_tmp_path, fetcher=_api_fetcher())
    enqueue_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs" and "POST" in route.methods)
    list_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs" and "GET" in route.methods)
    get_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/jobs/{job_id}" and "GET" in route.methods)
    run_once_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/run-once")
    stats_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/stats")
    dead_route = next(route for route in app.routes if getattr(route, "path", None) == "/worker/dead-letters")

    job = enqueue_route.endpoint({"spider": _api_spider().to_dict(), "source": "api"})
    run = run_once_route.endpoint({"worker_id": "api-worker"})

    assert list_route.endpoint()[0]["job_id"] == job["job_id"]
    assert get_route.endpoint(job["job_id"])["status"] == "succeeded"
    assert run["status"] == "succeeded"
    assert stats_route.endpoint()["succeeded_jobs"] == 1
    assert dead_route.endpoint() == []


def test_storage_snapshot_includes_worker_queue_manifest(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    WorkerService(store=store).enqueue_spider_run(_api_spider(), job_id="snapshot-job")

    manifest = store.create_snapshot(name="worker")

    assert "queue" in manifest["included_paths"]
    assert "workers" in manifest["included_paths"]


def test_worker_runtime_source_keeps_database_dependency_boundary():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    matches = []
    for path in Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append((str(path), line_number, line))

    assert matches == []


def test_worker_examples_validate(capsys):
    assert main(["validate", "examples/worker_api_job.json", "examples/worker_html_job.json", "examples/worker_playwright_job.json", "examples/scheduled_worker_job.json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(item["valid"] for item in payload)
