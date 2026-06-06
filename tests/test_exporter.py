import csv
import json
import zipfile
from pathlib import Path

import pytest

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.exporter import ExportError, ExportService, export_records, prepare_records
from crawler_platform.models import ExportConfig, SchedulerRun, TaskRecord, TaskStatus, WorkerJob
from crawler_platform.observability import log_event
from crawler_platform.storage import FileStore
from crawler_platform.validation import validate_spider_config


def _records():
    return [
        {
            "id": "a",
            "title": "A",
            "score": 1,
            "token": "secret-token",
            "detail": {"author": {"name": "Ada"}, "tags": ["x", "y"]},
            "items": [1, 2],
            "_dedup": {"hash": "abc", "keys": ["id"]},
            "_metadata": {"source_url": "https://example.test/a"},
        }
    ]


def _valid_export_spider(export):
    return {
        "id": "export-demo",
        "name": "Export Demo",
        "version": "1.0",
        "type": "api",
        "start_urls": ["file://examples/fixtures/export_task_page.json"],
        "items_json_path": "items",
        "fields": [
            {"name": "id", "type": "json_path", "json_path": "id"},
            {"name": "title", "type": "json_path", "json_path": "title"},
        ],
        "export": export,
    }


def test_export_config_validate_success_and_errors():
    ok = validate_spider_config(_valid_export_spider({"formats": ["json", "csv"], "default_format": "csv"}))
    bad_format = validate_spider_config(_valid_export_spider({"formats": ["json", "sql"]}))
    bad_default = validate_spider_config(_valid_export_spider({"formats": ["csv"], "default_format": "json"}))

    assert ok.valid
    assert any(issue.path == "export.formats" for issue in bad_format.issues)
    assert any(issue.path == "export.default_format" for issue in bad_default.issues)


def test_export_records_to_json_jsonl_csv_and_xlsx(workspace_tmp_path):
    records = [{"title": "A", "score": 1}, {"title": "B", "score": 2}]

    json_path = export_records(records, workspace_tmp_path / "out.json")
    jsonl_path = export_records(records, workspace_tmp_path / "out.jsonl")
    csv_path = export_records(records, workspace_tmp_path / "out.csv")
    xlsx_path = export_records(records, workspace_tmp_path / "out.xlsx")

    assert json.loads(json_path.read_text(encoding="utf-8"))[0]["title"] == "A"
    assert jsonl_path.read_text(encoding="utf-8").splitlines()[0].startswith('{"score": 1')
    assert list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))[1]["title"] == "B"
    with zipfile.ZipFile(xlsx_path) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()


def test_empty_export_generates_valid_files(workspace_tmp_path):
    csv_path = export_records([], workspace_tmp_path / "empty.csv", config=ExportConfig(include_fields=["id", "title"]))
    xlsx_path = export_records([], workspace_tmp_path / "empty.xlsx", config=ExportConfig(include_fields=["id"]))

    assert "id,title" in csv_path.read_text(encoding="utf-8-sig")
    with zipfile.ZipFile(xlsx_path) as archive:
        assert "sheet1.xml" in "\n".join(archive.namelist())


def test_nested_and_list_strategies():
    dot = prepare_records(_records(), ExportConfig(nested_strategy="flatten_dot", list_strategy="json_string"))[0]
    underscore = prepare_records(_records(), ExportConfig(nested_strategy="flatten_underscore", list_strategy="json_string"))[0]
    json_string = prepare_records(_records(), ExportConfig(nested_strategy="json_string", list_strategy="json_string"))[0]
    joined = prepare_records(_records(), ExportConfig(nested_strategy="flatten_dot", list_strategy="join", join_separator="|"))[0]

    assert dot["detail.author.name"] == "Ada"
    assert underscore["detail_author_name"] == "Ada"
    assert json.loads(json_string["detail"])["author"]["name"] == "Ada"
    assert joined["items"] == "1|2"


def test_include_exclude_alias_metadata_dedup_and_redaction():
    row = prepare_records(
        _records(),
        ExportConfig(
            include_fields=["id", "title", "token", "_dedup.hash", "_metadata.source_url"],
            exclude_fields=["title"],
            field_aliases={"id": "ID", "_dedup.hash": "dedup_hash"},
            include_metadata=True,
            redact_sensitive=True,
        ),
    )[0]

    assert row == {
        "ID": "a",
        "token": "***REDACTED***",
        "dedup_hash": "abc",
        "_metadata.source_url": "https://example.test/a",
    }


def test_export_service_task_report_observability_lifecycle_manifest_and_delete(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="task-1", spider_id="demo", status=TaskStatus.SUCCESS))
    store.append_record("task-1", _records()[0])
    store.create_run_report("task", "task-1", {"task_id": "task-1", "status": "success", "saved_records": 1})
    log_event(store, None, component="export", event_type="exportable_log", message="log", task_id="task-1")
    store.record_metric("tasks", "task-1", {"name": "records_saved", "kind": "counter", "value": 1})
    store.record_lifecycle_event({"event_id": "event-1", "target_type": "task", "target_id": "task-1", "event_type": "completed", "reason": "done"})

    service = ExportService(store)
    task_manifest = service.export_task("task-1", fmt="json", include_metadata=True)
    report_manifest = service.export_task_report("task-1", fmt="jsonl")
    logs_manifest = service.export_observability_logs(task_id="task-1", fmt="csv")
    lifecycle_manifest = service.export_lifecycle_events("task", "task-1", fmt="json")

    assert task_manifest["rows_count"] == 1
    assert report_manifest["source_type"] == "task_report"
    assert logs_manifest["rows_count"] >= 1
    assert lifecycle_manifest["rows_count"] == 1
    assert store.get_export(task_manifest["export_id"])["path"] == task_manifest["path"]
    assert store.check_storage()["stats"]["export_manifests"] == 4
    deleted = service.delete_export(task_manifest["export_id"])
    assert len(deleted["removed"]) == 2


def test_export_service_job_and_scheduler_reports(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="job-task", spider_id="demo", status=TaskStatus.SUCCESS))
    store.append_record("job-task", {"id": "a"})
    store.enqueue_job(
        WorkerJob(
            job_id="job-1",
            job_type="spider_run",
            spider_id="demo",
            spider_config={"id": "demo", "name": "Demo", "start_urls": ["x"], "fields": [{"name": "title", "type": "css", "selector": "h1"}]},
            task_id="job-task",
        )
    )
    job = store.get_job("job-1")
    job.status = "succeeded"
    store.save_worker_job(job)
    store.create_run_report("job", "job-1", {"job_id": "job-1", "status": "succeeded"})
    store.record_scheduler_run(SchedulerRun(id="run-1", schedule_id="schedule-1", spider_id="demo", task_id="job-task", status="success"))
    store.create_run_report("scheduler", "run-1", {"scheduler_run_id": "run-1", "status": "success"})

    service = ExportService(store)
    job_manifest = service.export_job("job-1", fmt="json")
    scheduler_manifest = service.export_scheduler_run("run-1", fmt="json")

    assert job_manifest["source_type"] == "job"
    assert scheduler_manifest["source_type"] == "scheduler"


def test_cli_export_task_list_show_and_delete(workspace_tmp_path, capsys):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="cli-task", spider_id="demo", status=TaskStatus.SUCCESS))
    store.append_record("cli-task", {"id": "a", "name": "A"})
    data_dir = str(workspace_tmp_path)

    assert main(["export", "task", "cli-task", "--format", "csv", "--data-dir", data_dir]) == 0
    created = capsys.readouterr().out
    export_id = created.split("export_id=", 1)[1].split()[0]
    assert main(["export", "list", "--data-dir", data_dir]) == 0
    assert export_id in capsys.readouterr().out
    assert main(["export", "show", export_id, "--data-dir", data_dir]) == 0
    assert "rows=1" in capsys.readouterr().out
    assert main(["export", "delete", export_id, "--data-dir", data_dir]) == 0
    assert "Deleted export" in capsys.readouterr().out


def test_cli_export_task_limit_and_offset(workspace_tmp_path, capsys):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="window-task", spider_id="demo", status=TaskStatus.SUCCESS))
    store.append_record("window-task", {"id": "a", "name": "A"})
    store.append_record("window-task", {"id": "b", "name": "B"})
    store.append_record("window-task", {"id": "c", "name": "C"})

    assert main([
        "export",
        "task",
        "window-task",
        "--format",
        "json",
        "--limit",
        "1",
        "--offset",
        "1",
        "--data-dir",
        str(workspace_tmp_path),
        "--json",
    ]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["rows_count"] == 1
    assert manifest["metadata"]["window"] == {"limit": 1, "offset": 1}
    exported = json.loads(Path(manifest["path"]).read_text(encoding="utf-8"))
    assert exported == [{"id": "b", "name": "B"}]


def test_fastapi_export_endpoints(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="api-task", spider_id="demo", status=TaskStatus.SUCCESS))
    store.append_record("api-task", {"id": "a", "name": "A"})
    app = create_app(workspace_tmp_path)
    create_route = next(route for route in app.routes if getattr(route, "path", None) == "/exports/tasks/{task_id}")
    list_route = next(route for route in app.routes if getattr(route, "path", None) == "/exports")
    show_route = next(route for route in app.routes if getattr(route, "path", None) == "/exports/{export_id}")
    download_route = next(route for route in app.routes if getattr(route, "path", None) == "/exports/{export_id}/download")
    delete_route = next(route for route in app.routes if getattr(route, "path", None) == "/exports/{export_id}")

    manifest = create_route.endpoint("api-task", {"format": "json"})
    listed = list_route.endpoint()
    shown = show_route.endpoint(manifest["export_id"])
    download = download_route.endpoint(manifest["export_id"])
    deleted = delete_route.endpoint(manifest["export_id"])

    assert listed[0]["export_id"] == manifest["export_id"]
    assert shown["rows_count"] == 1
    assert str(download.path) == manifest["path"]
    assert deleted["export_id"] == manifest["export_id"]


def test_export_runtime_keeps_dependency_boundary():
    import pathlib
    import re

    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    for path in pathlib.Path("src").rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8"))
