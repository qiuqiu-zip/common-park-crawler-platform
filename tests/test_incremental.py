import json

import pytest

from crawler_platform.config_loader import load_spider_config
from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FakeFetcher, FetchError
from crawler_platform.models import DetailOptions, FieldRule, PaginationOptions, RequestOptions, SpiderConfig, TaskStatus
from crawler_platform.storage import FileStore


def _api_spider(**overrides):
    spider = SpiderConfig(
        id=overrides.pop("id", "incremental-api"),
        name="Incremental API",
        type="api",
        start_urls=overrides.pop("start_urls", ["https://example.test/api"]),
        items_json_path="items",
        fields=[
            FieldRule(name="id", type="json_path", json_path="id"),
            FieldRule(name="title", type="json_path", json_path="title"),
            FieldRule(name="published_at", type="json_path", json_path="published_at", default=None),
        ],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _run(spider, responses, store, task_id="task"):
    return CrawlerEngine(store=store, fetcher=FakeFetcher(responses)).run(spider, task_id=task_id)


def test_dedup_disabled_writes_duplicate_records(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": False})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"},{"id":1,"title":"A again"}]}'}, store)

    assert task.saved_records == 2
    assert task.skipped_duplicates == 0
    assert "_dedup" not in store.read_records("task")[0]


def test_dedup_enabled_generates_sha256_hash(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["id"], "hash_method": "sha256"})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)
    record = store.read_records("task")[0]

    assert task.saved_records == 1
    assert len(record["_dedup"]["hash"]) == 64
    assert record["_dedup"]["is_duplicate"] is False


def test_dedup_md5_hash_method(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["id"], "hash_method": "md5"})
    store = FileStore(workspace_tmp_path)

    _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)

    assert len(store.read_records("task")[0]["_dedup"]["hash"]) == 32


def test_dedup_global_scope_skips_across_spiders(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    dedup = {"enabled": True, "dataset": "shared", "keys": ["id"], "scope": "global"}
    first = _api_spider(id="one", dedup=dedup)
    second = _api_spider(id="two", dedup=dedup)
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(first, {"https://example.test/api": response}, store, task_id="one")
    task = _run(second, {"https://example.test/api": response}, store, task_id="two")

    assert task.saved_records == 0
    assert task.skipped_duplicates == 1


def test_dedup_spider_scope_isolates_spiders(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    dedup = {"enabled": True, "dataset": "shared", "keys": ["id"], "scope": "spider"}
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(_api_spider(id="one", dedup=dedup), {"https://example.test/api": response}, store, task_id="one")
    task = _run(_api_spider(id="two", dedup=dedup), {"https://example.test/api": response}, store, task_id="two")

    assert task.saved_records == 1


def test_dedup_task_scope_isolates_tasks(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "dataset": "shared", "keys": ["id"], "scope": "task"})
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(spider, {"https://example.test/api": response}, store, task_id="one")
    task = _run(spider, {"https://example.test/api": response}, store, task_id="two")

    assert task.saved_records == 1


def test_include_source_url_changes_hash_across_start_urls(workspace_tmp_path):
    spider = _api_spider(
        start_urls=["https://example.test/a", "https://example.test/b"],
        dedup={
            "enabled": True,
            "dataset": "source-aware",
            "keys": ["id"],
            "scope": "spider",
            "include_source_url": True,
        },
    )
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    task = _run(spider, {"https://example.test/a": response, "https://example.test/b": response}, store, task_id="source-aware")
    records = store.read_records("source-aware")

    assert task.saved_records == 2
    assert records[0]["_dedup"]["hash"] != records[1]["_dedup"]["hash"]
    assert records[0]["_dedup"]["dataset"] == "source-aware"
    assert records[0]["_dedup"]["scope"] == "spider"
    assert records[0]["_dedup"]["is_duplicate"] is False
    assert records[0]["_dedup"]["keys"] == ["id"]


def test_without_include_source_url_same_keys_skip_across_start_urls(workspace_tmp_path):
    spider = _api_spider(
        start_urls=["https://example.test/a", "https://example.test/b"],
        dedup={
            "enabled": True,
            "dataset": "source-agnostic",
            "keys": ["id"],
            "scope": "spider",
            "include_source_url": False,
        },
    )
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    task = _run(spider, {"https://example.test/a": response, "https://example.test/b": response}, store, task_id="source-agnostic")

    assert task.saved_records == 1
    assert task.skipped_duplicates == 1
    assert len(store.load_hashes("source-agnostic", scope=f"spider-{spider.id}")) == 1


def test_repeated_run_skips_existing_records(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["id"], "scope": "spider"})
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(spider, {"https://example.test/api": response}, store, task_id="first")
    task = _run(spider, {"https://example.test/api": response}, store, task_id="second")

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 0
    assert task.duplicate_records == 1


def test_skip_existing_false_writes_duplicate_with_marker(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["id"], "scope": "spider", "skip_existing": False})
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(spider, {"https://example.test/api": response}, store, task_id="first")
    task = _run(spider, {"https://example.test/api": response}, store, task_id="second")

    record = store.read_records("second")[0]
    assert task.saved_records == 1
    assert record["_dedup"]["is_duplicate"] is True


def test_dedup_keys_support_nested_field_paths(workspace_tmp_path):
    spider = SpiderConfig(
        id="nested",
        name="Nested",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article",
        fields=[FieldRule(name="title", type="css", selector="a"), FieldRule(name="detail_path", type="attr", selector="a", attribute="href")],
        detail=DetailOptions(
            enabled=True,
            url_field="detail_path",
            merge_strategy="namespace",
            namespace="detail",
            fields=[FieldRule(name="id", type="css", selector=".id")],
        ),
        dedup={"enabled": True, "keys": ["detail.id"]},
    )
    store = FileStore(workspace_tmp_path)
    responses = {
        "https://example.test/list": '<article><a href="/detail">A</a></article>',
        "https://example.test/detail": '<span class="id">detail-1</span>',
    }

    _run(spider, responses, store)

    assert store.read_records("task")[0]["_dedup"]["keys"] == ["detail.id"]


def test_missing_key_policy_error_fails_task(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["missing"], "missing_key_policy": "error"})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)

    assert task.status == TaskStatus.FAILED
    assert task.error_type == "engine"


def test_missing_key_policy_warn_saves_record(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["missing"], "missing_key_policy": "warn"})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 1
    assert task.warnings
    assert task.warnings[0]["type"] == "dedup_missing_key"
    assert task.warnings[0]["error_type"] == "field_quality"
    assert task.warnings[0]["missing_keys"] == ["missing"]


def test_missing_key_policy_skip_skips_record(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["missing"], "missing_key_policy": "skip"})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 0
    assert task.skipped_records == 1


def test_missing_key_policy_allow_empty_saves_record(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["missing"], "missing_key_policy": "allow_empty"})
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'}, store)
    record = store.read_records("task")[0]

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 1
    assert task.skipped_records == 0
    assert record["_dedup"]["hash"]


def test_watermark_int_max_updates_state(workspace_tmp_path):
    spider = _api_spider(
        dedup={"enabled": False},
        watermark={"enabled": True, "dataset": "items", "field": "id", "strategy": "max", "type": "int"},
    )
    store = FileStore(workspace_tmp_path)

    task = _run(spider, {"https://example.test/api": '{"items":[{"id":2,"title":"B"},{"id":5,"title":"E"}]}'}, store)

    assert task.watermark_updates == 1
    assert store.get_watermark(spider.id, "items")["value"] == 5


def test_watermark_datetime_max_updates_state(workspace_tmp_path):
    spider = _api_spider(
        dedup={"enabled": False},
        watermark={
            "enabled": True,
            "dataset": "articles",
            "field": "published_at",
            "strategy": "max",
            "type": "datetime",
            "format": "%Y-%m-%d",
        },
    )
    store = FileStore(workspace_tmp_path)

    _run(spider, {"https://example.test/api": '{"items":[{"id":1,"title":"A","published_at":"2026-05-01"},{"id":2,"title":"B","published_at":"2026-06-02"}]}'}, store)

    assert store.get_watermark(spider.id, "articles")["value"] == "2026-06-02"


def test_watermark_stop_when_older_stops_pagination(workspace_tmp_path):
    spider = _api_spider(
        start_urls=["https://example.test/api?page=1"],
        dedup={"enabled": False},
        watermark={"enabled": True, "dataset": "items", "field": "id", "strategy": "max", "type": "int", "stop_when_older": True},
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/api?page=2", "https://example.test/api?page=3"], max_pages=3),
    )
    store = FileStore(workspace_tmp_path)
    store.update_watermark(spider.id, "items", 10)
    fetcher = FakeFetcher(
        {
            "https://example.test/api?page=1": '{"items":[{"id":11,"title":"New"}]}',
            "https://example.test/api?page=2": '{"items":[{"id":9,"title":"Old"}]}',
            "https://example.test/api?page=3": '{"items":[{"id":12,"title":"Should not fetch"}]}',
        }
    )

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="wm-stop")

    assert task.skipped_by_watermark == 1
    assert [request.url for request in fetcher.requests] == ["https://example.test/api?page=1", "https://example.test/api?page=2"]


def test_checkpoint_is_saved_after_each_page(workspace_tmp_path):
    spider = _api_spider(
        start_urls=["https://example.test/api?page=1"],
        dedup={"enabled": True, "keys": ["id"], "scope": "task"},
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/api?page=2"], max_pages=2),
    )
    store = FileStore(workspace_tmp_path)

    task = _run(
        spider,
        {
            "https://example.test/api?page=1": '{"items":[{"id":1,"title":"A"}]}',
            "https://example.test/api?page=2": '{"items":[{"id":2,"title":"B"}]}',
        },
        store,
        task_id="checkpoint-task",
    )

    checkpoint = store.load_checkpoint("checkpoint-task")
    assert task.checkpoint_saves >= 2
    assert checkpoint["completed"] is True


def test_failed_task_keeps_checkpoint_and_resume_continues(workspace_tmp_path):
    spider = _api_spider(
        start_urls=["https://example.test/api?page=1"],
        request=RequestOptions(fail_fast=True),
        dedup={"enabled": True, "keys": ["id"], "scope": "task"},
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/api?page=2"], max_pages=2),
    )
    store = FileStore(workspace_tmp_path)
    first_fetcher = FakeFetcher(
        {
            "https://example.test/api?page=1": '{"items":[{"id":1,"title":"A"}]}',
            "https://example.test/api?page=2": FetchError("down", "network", url="https://example.test/api?page=2"),
        }
    )

    failed = CrawlerEngine(store=store, fetcher=first_fetcher).run(spider, task_id="resume-task")
    checkpoint = store.load_checkpoint("resume-task")
    resumed = CrawlerEngine(
        store=store,
        fetcher=FakeFetcher({"https://example.test/api?page=2": '{"items":[{"id":2,"title":"B"}]}'}),
    ).resume_task("resume-task")

    assert failed.status == TaskStatus.FAILED
    assert checkpoint["next_url"] == "https://example.test/api?page=2"
    assert resumed.status == TaskStatus.SUCCESS
    assert resumed.resume_count == 1
    assert [record["id"] for record in store.read_records("resume-task")] == [1, 2]


def test_cli_checkpoint_resume_continues_without_duplicates(workspace_tmp_path, capsys):
    spider = load_spider_config("examples/resume_checkpoint.json")
    spider.request.fail_fast = True
    store = FileStore(workspace_tmp_path)
    CrawlerEngine(
        store=store,
        fetcher=FakeFetcher(
            {
                "examples/fixtures/resume_page_1.json": '{"items":[{"id":1,"title":"Resume page one"}]}',
                "examples/fixtures/resume_page_2.json": FetchError("down", "network", url="examples/fixtures/resume_page_2.json"),
            }
        ),
    ).run(spider, task_id="cli-resume-task")

    exit_code = main(["--data-dir", str(workspace_tmp_path), "incremental", "checkpoint", "resume", "cli-resume-task"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["task_id"] == "cli-resume-task"
    assert payload["status"] == "success"
    assert [record["id"] for record in store.read_records("cli-resume-task")] == [1, 2]


def test_fastapi_checkpoint_resume_endpoint_resumes_task(workspace_tmp_path):
    pytest.importorskip("fastapi")
    spider = load_spider_config("examples/resume_checkpoint.json")
    spider.request.fail_fast = True
    store = FileStore(workspace_tmp_path)
    CrawlerEngine(
        store=store,
        fetcher=FakeFetcher(
            {
                "examples/fixtures/resume_page_1.json": '{"items":[{"id":1,"title":"Resume page one"}]}',
                "examples/fixtures/resume_page_2.json": FetchError("down", "network", url="examples/fixtures/resume_page_2.json"),
            }
        ),
    ).run(spider, task_id="api-resume-task")
    app = create_app(workspace_tmp_path, fetcher=FakeFetcher({"examples/fixtures/resume_page_2.json": '{"items":[{"id":2,"title":"Resume page two"}]}'}))
    resume_route = next(route for route in app.routes if getattr(route, "path", None) == "/incremental/checkpoints/{task_id}/resume")
    results_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/results")

    response = resume_route.endpoint("api-resume-task")

    assert response["status"] == "success"
    assert response["resume_count"] == 1
    assert [record["id"] for record in results_route.endpoint("api-resume-task")] == [1, 2]


def test_cli_run_resume_checkpoint_example(workspace_tmp_path, capsys):
    exit_code = main(["run", "examples/resume_checkpoint.json", "--data-dir", str(workspace_tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["records_count"] == 2


def test_pagination_detail_dedup_combination(workspace_tmp_path):
    spider = SpiderConfig(
        id="detail-incremental",
        name="Detail incremental",
        type="http",
        start_urls=["https://example.test/list?page=1"],
        item_selector="article",
        fields=[FieldRule(name="title", type="css", selector="a"), FieldRule(name="detail_path", type="attr", selector="a", attribute="href")],
        detail=DetailOptions(
            enabled=True,
            url_field="detail_path",
            merge_strategy="namespace",
            namespace="detail",
            fields=[FieldRule(name="id", type="css", selector=".id")],
        ),
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/list?page=2"], max_pages=2),
        dedup={"enabled": True, "keys": ["detail.id"], "scope": "spider"},
    )
    store = FileStore(workspace_tmp_path)
    responses = {
        "https://example.test/list?page=1": '<article><a href="/detail/a">A</a></article>',
        "https://example.test/list?page=2": '<article><a href="/detail/b">B</a></article>',
        "https://example.test/detail/a": '<span class="id">a</span>',
        "https://example.test/detail/b": '<span class="id">b</span>',
    }

    task = _run(spider, responses, store)

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 2
    assert [record["detail"]["id"] for record in store.read_records("task")] == ["a", "b"]


def test_all_duplicates_still_success_with_zero_saved(workspace_tmp_path):
    spider = _api_spider(dedup={"enabled": True, "keys": ["id"], "scope": "spider"})
    store = FileStore(workspace_tmp_path)
    response = '{"items":[{"id":1,"title":"A"}]}'

    _run(spider, {"https://example.test/api": response}, store, task_id="one")
    task = _run(spider, {"https://example.test/api": response}, store, task_id="two")

    assert task.status == TaskStatus.SUCCESS
    assert task.saved_records == 0


def test_cli_incremental_watermark_and_checkpoint_list(workspace_tmp_path, capsys):
    store = FileStore(workspace_tmp_path)
    store.update_watermark("spider", "dataset", 3)
    store.save_checkpoint("task-1", {"task_id": "task-1", "spider_id": "spider", "completed": False})

    assert main(["--data-dir", str(workspace_tmp_path), "incremental", "watermark", "list"]) == 0
    assert main(["--data-dir", str(workspace_tmp_path), "incremental", "checkpoint", "list"]) == 0
    out = capsys.readouterr().out

    assert '"dataset": "dataset"' in out
    assert '"task_id": "task-1"' in out


def test_fastapi_incremental_endpoints(workspace_tmp_path):
    pytest.importorskip("fastapi")
    app = create_app(workspace_tmp_path)
    store = FileStore(workspace_tmp_path)
    store.update_watermark("spider", "dataset", 3)
    store.save_checkpoint("task-1", {"task_id": "task-1", "spider_id": "spider", "completed": False})

    watermark_route = next(route for route in app.routes if getattr(route, "path", None) == "/incremental/watermarks")
    checkpoint_route = next(route for route in app.routes if getattr(route, "path", None) == "/incremental/checkpoints")

    assert watermark_route.endpoint()[0]["value"] == 3
    assert checkpoint_route.endpoint()[0]["task_id"] == "task-1"
