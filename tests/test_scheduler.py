import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FakeFetcher, FetchError
from crawler_platform.models import FieldRule, PlaywrightOptions, SchedulerOptions, SpiderConfig
from crawler_platform.playwright_runner import FakeRenderBackend, PlaywrightFetcher
from crawler_platform.scheduler import FakeClock, SchedulerService
from crawler_platform.storage import FileStore


def _api_spider(scheduler: SchedulerOptions | None = None, *, spider_id: str = "scheduled-api") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Scheduled API",
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


def _html_spider(scheduler: SchedulerOptions | None = None, *, spider_id: str = "scheduled-html") -> SpiderConfig:
    return SpiderConfig(
        id=spider_id,
        name="Scheduled HTML",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        fields=[FieldRule(name="title", type="css", selector="a.title")],
        scheduler=scheduler or SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"),
    )


def _service(workspace_tmp_path, responses=None, *, clock=None):
    store = FileStore(workspace_tmp_path)
    fetcher = FakeFetcher(responses or {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}', "https://example.test/list": '<article class="item"><a class="title">A</a></article>'})
    engine = CrawlerEngine(store=store, fetcher=fetcher)
    return SchedulerService(store=store, engine=engine, clock=clock), store, fetcher


def test_scheduler_disabled_does_not_register_automatic_task(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path)
    spider = _api_spider(SchedulerOptions(enabled=False, type="interval", interval_seconds=60))

    assert service.register_spider_schedule(spider) is None
    assert store.list_schedules() == []
    assert store.load_spider(spider.id).id == spider.id


def test_manual_schedule_does_not_auto_trigger_but_can_trigger_now(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path)
    spider = _api_spider(SchedulerOptions(enabled=True, type="manual"))
    job = service.register_spider_schedule(spider)

    assert job is not None
    assert job.next_run_at is None
    assert service.run_due_jobs(now="2026-06-03T00:00:00Z") == []

    run = service.trigger_schedule_now(job.id)

    assert run["status"] == "success"
    assert store.load_task(run["task_id"]).status.value == "success"


def test_interval_next_run_and_timezone_default(workspace_tmp_path):
    service, _, _ = _service(workspace_tmp_path)
    spider = _api_spider(SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"))

    job = service.register_spider_schedule(spider)
    next_after = service.compute_next_run(job, now="2026-06-03T00:00:00Z", after="2026-06-03T00:00:00Z")

    assert job.scheduler["timezone"] == "UTC"
    assert job.next_run_at == "2026-06-03T00:00:00+00:00"
    assert next_after == "2026-06-03T00:01:00+00:00"


def test_cron_next_run_calculation(workspace_tmp_path):
    service, _, _ = _service(workspace_tmp_path)
    scheduler = SchedulerOptions(enabled=True, type="cron", cron="*/15 * * * *", timezone="UTC")

    first = service.compute_next_run(scheduler, now="2026-06-03T00:07:00Z")
    exact = service.compute_next_run(scheduler, now="2026-06-03T00:15:00Z")

    assert first == "2026-06-03T00:15:00+00:00"
    assert exact == "2026-06-03T00:15:00+00:00"


def test_start_at_and_end_at_boundaries(workspace_tmp_path):
    service, _, _ = _service(workspace_tmp_path)
    future = _api_spider(SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:10:00Z"))
    expired = _api_spider(
        SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z", end_at="2026-06-02T23:59:00Z"),
        spider_id="expired",
    )

    future_job = service.register_spider_schedule(future)
    expired_job = service.register_spider_schedule(expired)

    assert service.run_due_jobs(now="2026-06-03T00:00:00Z") == []
    assert future_job.next_run_at == "2026-06-03T00:10:00+00:00"
    assert expired_job.next_run_at is None


def test_jitter_is_deterministic(workspace_tmp_path):
    service, _, _ = _service(workspace_tmp_path)
    scheduler = SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z", jitter_seconds=10)

    first = service.compute_next_run({"id": "jittered", "scheduler": asdict(scheduler)}, now="2026-06-03T00:00:00Z")
    second = service.compute_next_run({"id": "jittered", "scheduler": asdict(scheduler)}, now="2026-06-03T00:00:00Z")

    assert first == second
    assert "2026-06-03T00:00:" in first


def test_max_instances_prevents_reentry(workspace_tmp_path):
    service, _, _ = _service(workspace_tmp_path)
    job = service.register_spider_schedule(_api_spider())
    job.running_instances = 1
    service.store.save_schedule(job)

    outcomes = service.run_due_jobs(now="2026-06-03T00:00:00Z")

    assert outcomes[0]["status"] == "skipped"
    assert outcomes[0]["trigger"] == "max_instances"


def test_run_due_jobs_creates_task_records_run_records_and_updates_next_run(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path)
    job = service.register_spider_schedule(_api_spider())

    outcomes = service.run_due_jobs(now="2026-06-03T00:00:00Z")
    updated = store.get_schedule(job.id)

    assert outcomes[0]["status"] == "success"
    assert store.load_task(outcomes[0]["task_id"]).status.value == "success"
    assert store.list_scheduler_runs(schedule_id=job.id)[0]["status"] == "success"
    assert updated.next_run_at == "2026-06-03T00:01:00+00:00"


def test_run_due_jobs_records_failure_without_losing_schedule(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path, {"https://example.test/api": FetchError("down", "network", url="https://example.test/api")})
    job = service.register_spider_schedule(_api_spider())

    outcomes = service.run_due_jobs(now="2026-06-03T00:00:00Z")

    assert outcomes[0]["status"] == "failed"
    assert store.get_schedule(job.id).id == job.id
    assert store.list_scheduler_runs(schedule_id=job.id)[0]["error_type"] == "network"


def test_misfire_skip_run_once_and_catch_up(workspace_tmp_path):
    skip_service, skip_store, _ = _service(workspace_tmp_path / "skip")
    skip_job = skip_service.register_spider_schedule(
        _api_spider(SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z", misfire_policy="skip"))
    )
    skip_outcomes = skip_service.run_due_jobs(now="2026-06-03T00:05:00Z")

    assert skip_outcomes[0]["status"] == "skipped"
    assert skip_store.get_schedule(skip_job.id).next_run_at == "2026-06-03T00:06:00+00:00"

    once_service, _, _ = _service(workspace_tmp_path / "once")
    once_service.register_spider_schedule(
        _api_spider(SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z", misfire_policy="run_once"))
    )
    assert len(once_service.run_due_jobs(now="2026-06-03T00:05:00Z")) == 1

    catch_service, _, _ = _service(workspace_tmp_path / "catch", clock=FakeClock("2026-06-03T00:05:00Z"))
    catch_service.max_catch_up_runs = 2
    catch_service.register_spider_schedule(
        _html_spider(SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z", misfire_policy="catch_up"))
    )
    catch_outcomes = catch_service.run_due_jobs(now="2026-06-03T00:05:00Z")

    assert len(catch_outcomes) == 2
    assert catch_service.store.get_schedule("scheduled-html").warnings


def test_pause_resume_and_disable(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path)
    job = service.register_spider_schedule(_api_spider())

    assert service.pause_schedule(job.id).status == "paused"
    assert service.run_due_jobs(now="2026-06-03T00:00:00Z") == []
    assert service.resume_schedule(job.id).status == "enabled"
    assert service.disable_schedule(job.id).status == "disabled"
    assert store.get_schedule(job.id).next_run_at is None


def test_file_store_schedule_methods_and_storage_check(workspace_tmp_path):
    service, store, _ = _service(workspace_tmp_path)
    job = service.register_spider_schedule(_api_spider())

    assert store.get_schedule(job.id).id == job.id
    assert [item["id"] for item in store.list_schedules(enabled=True)] == [job.id]
    store.update_schedule(job.id, {"status": "paused"})
    assert store.get_schedule(job.id).status == "paused"

    run = service.trigger_schedule_now(job.id)
    health = store.check_storage()

    assert store.list_scheduler_runs(schedule_id=job.id)[0]["id"] == run["id"]
    assert health["ok"] is True
    assert health["stats"]["schedules"] == 1
    assert health["stats"]["scheduler_runs"] == 1


def test_cli_scheduler_register_list_run_due_and_runs(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)

    assert main(["scheduler", "register", "examples/scheduled_interval.json", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "list", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "run-due", "--now", "2026-06-03T00:00:00Z", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "runs", "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "Registered schedule scheduled-interval-demo" in output
    assert "scheduled-interval-demo" in output
    assert "success" in output


def test_cli_scheduler_trigger_pause_resume_disable(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)
    assert main(["scheduler", "register", "examples/scheduled_manual.json", "--data-dir", data_dir]) == 0

    assert main(["scheduler", "trigger", "scheduled-manual-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "pause", "scheduled-manual-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "resume", "scheduled-manual-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "disable", "scheduled-manual-demo", "--data-dir", data_dir]) == 0

    output = capsys.readouterr().out
    assert "scheduled-manual-demo" in output
    assert '"status": "disabled"' in output


def test_cli_pause_resume_disable_gates_run_due(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)
    assert main(["scheduler", "register", "examples/scheduled_interval.json", "--data-dir", data_dir]) == 0

    assert main(["scheduler", "pause", "scheduled-interval-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "run-due", "--now", "2026-06-03T00:00:00Z", "--data-dir", data_dir]) == 0
    paused_output = capsys.readouterr().out
    assert "No scheduler records" in paused_output

    assert main(["scheduler", "resume", "scheduled-interval-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "run-due", "--now", "2026-06-03T00:00:00Z", "--data-dir", data_dir]) == 0
    resumed_output = capsys.readouterr().out
    assert "scheduled-interval-demo" in resumed_output
    assert "success" in resumed_output

    assert main(["scheduler", "disable", "scheduled-interval-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "run-due", "--now", "2026-06-03T00:01:00Z", "--data-dir", data_dir]) == 0
    disabled_output = capsys.readouterr().out
    assert "No scheduler records" in disabled_output


def test_cli_manual_schedule_run_due_does_not_trigger_and_trigger_runs(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path)
    assert main(["scheduler", "register", "examples/scheduled_manual.json", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "run-due", "--now", "2026-06-03T00:00:00Z", "--data-dir", data_dir]) == 0
    auto_output = capsys.readouterr().out

    assert "No scheduler records" in auto_output

    assert main(["scheduler", "trigger", "scheduled-manual-demo", "--data-dir", data_dir]) == 0
    assert main(["scheduler", "runs", "--data-dir", data_dir]) == 0
    manual_output = capsys.readouterr().out

    assert "scheduled-manual-demo" in manual_output
    assert "success" in manual_output


def test_fastapi_scheduler_register_run_due_trigger_controls_and_runs(workspace_tmp_path):
    pytest.importorskip("fastapi")
    fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'})
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    register_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules" and "POST" in route.methods)
    list_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules" and "GET" in route.methods)
    run_due_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/run-due")
    trigger_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules/{schedule_id}/trigger")
    pause_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules/{schedule_id}/pause")
    resume_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules/{schedule_id}/resume")
    disable_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/schedules/{schedule_id}/disable")
    runs_route = next(route for route in app.routes if getattr(route, "path", None) == "/scheduler/runs")

    registered = register_route.endpoint({"spider": _api_spider().to_dict()})
    due = run_due_route.endpoint({"now": "2026-06-03T00:00:00Z"})
    triggered = trigger_route.endpoint("scheduled-api")

    assert registered["registered"] is True
    assert list_route.endpoint()[0]["id"] == "scheduled-api"
    assert due[0]["status"] == "success"
    assert triggered["status"] == "success"
    assert pause_route.endpoint("scheduled-api")["status"] == "paused"
    assert resume_route.endpoint("scheduled-api")["status"] == "enabled"
    assert disable_route.endpoint("scheduled-api")["status"] == "disabled"
    assert len(runs_route.endpoint()) == 2


def test_scheduled_playwright_spider_uses_fake_render_backend(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    backend = FakeRenderBackend({"https://example.test/rendered": '<article class="result"><a class="title">Rendered</a></article>'})
    engine = CrawlerEngine(
        store=store,
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    )
    service = SchedulerService(store=store, engine=engine)
    spider = SpiderConfig(
        id="scheduled-pw",
        name="Scheduled PW",
        type="playwright",
        start_urls=["https://example.test/rendered"],
        item_selector="article.result",
        fields=[FieldRule(name="title", type="css", selector="a.title")],
        playwright=PlaywrightOptions(enabled=True, browser_pool_size=1),
        scheduler=SchedulerOptions(enabled=True, type="interval", interval_seconds=60, start_at="2026-06-03T00:00:00Z"),
    )
    service.register_spider_schedule(spider)

    outcomes = service.run_due_jobs(now="2026-06-03T00:00:00Z")

    assert outcomes[0]["status"] == "success"
    assert backend.rendered_urls == ["https://example.test/rendered"]


def test_runtime_source_keeps_database_dependency_boundary():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    matches = []
    for path in Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append((str(path), line_number, line))

    assert matches == []
