import json
import threading

import pytest

from crawler_platform.cli import main
from crawler_platform.models import FieldRule, SpiderConfig, TaskRecord, TaskStatus
from crawler_platform.storage import AtomicWriteError, CorruptedFileError, FileStore, InvalidTaskTransitionError
from crawler_platform.validation import SpiderConfigValidationError


def _valid_spider_dict():
    return {
        "id": "demo",
        "name": "Demo",
        "version": "1.0",
        "type": "http",
        "start_urls": ["https://example.test"],
        "item_selector": "article",
        "fields": [{"name": "title", "type": "css", "selector": "h2"}],
        "scheduler": {"type": "manual"},
    }


def test_file_store_saves_spiders_tasks_records_and_hashes(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = SpiderConfig(
        id="demo",
        name="Demo",
        start_urls=["https://example.test"],
        item_selector="article",
        fields=[FieldRule(name="title", type="css", selector="h2")],
    )
    task = TaskRecord(id="task-1", spider_id="demo", status=TaskStatus.RUNNING)

    store.save_spider(spider)
    store.save_task(task)
    store.append_record("task-1", {"title": "A"})
    store.add_hash("demo", "abc")

    assert store.load_spider("demo").id == "demo"
    assert store.load_task("task-1").status == TaskStatus.RUNNING
    assert store.read_records("task-1") == [{"title": "A"}]
    assert store.has_hash("demo", "abc")


def test_file_store_initializes_required_directory_structure(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    assert all(path.exists() for path in store.required_directories)
    assert store.metadata_path.exists()


def test_storage_metadata_is_created_and_versioned(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    metadata = store.read_storage_metadata()

    assert metadata["storage_version"] == "1.0"
    assert metadata["features"]["atomic_write"] is True
    assert metadata["features"]["file_lock"] is True


def test_atomic_write_successfully_replaces_json(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    path = store.save_task(TaskRecord(id="task-1", spider_id="demo"))

    store.save_task(TaskRecord(id="task-1", spider_id="demo", total_seen=3))

    assert json.loads(path.read_text(encoding="utf-8"))["total_seen"] == 3


def test_atomic_write_failure_keeps_existing_file(workspace_tmp_path, monkeypatch):
    store = FileStore(workspace_tmp_path)
    path = store.save_task(TaskRecord(id="task-1", spider_id="demo"))
    original = path.read_text(encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(store, "_replace_file", fail_replace)

    with pytest.raises(AtomicWriteError):
        store.save_task(TaskRecord(id="task-1", spider_id="demo", total_seen=9))

    assert path.read_text(encoding="utf-8") == original


def test_task_create_read_update_and_valid_transition(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="task-1", spider_id="demo"))

    updated = store.update_task_status("task-1", TaskStatus.RUNNING, started_at="now")

    assert updated.status == TaskStatus.RUNNING
    assert store.load_task("task-1").started_at == "now"


def test_invalid_task_transition_raises_contextual_error(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_task(TaskRecord(id="task-1", spider_id="demo", status=TaskStatus.SUCCESS))

    with pytest.raises(InvalidTaskTransitionError) as exc:
        store.save_task(TaskRecord(id="task-1", spider_id="demo", status=TaskStatus.RUNNING))

    assert exc.value.context["task_id"] == "task-1"
    assert exc.value.context["current"] == "success"
    assert exc.value.context["target"] == "running"


def test_spider_config_is_validated_before_save(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    with pytest.raises(SpiderConfigValidationError):
        store.save_spider_config({"id": "bad", "start_urls": []})


def test_spider_config_save_canonicalizes_legacy_fields(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    payload = _valid_spider_dict()
    payload["mode"] = payload.pop("type")
    payload["schedule"] = payload.pop("scheduler")

    path = store.save_spider_config(payload)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["type"] == "http"
    assert saved["scheduler"]["type"] == "manual"
    assert "mode" not in saved
    assert "schedule" not in saved


def test_list_spider_configs_filters_enabled_and_delete_moves_to_dead_letters(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    enabled = _valid_spider_dict()
    disabled = {**_valid_spider_dict(), "id": "disabled", "enabled": False}
    store.save_spider_config(enabled)
    store.save_spider_config(disabled)

    deleted = store.delete_spider_config("disabled")

    assert [item["id"] for item in store.list_spider_configs(enabled=True)] == ["demo"]
    assert "dead_letters" in deleted["moved_to"]


def test_jsonl_append_and_read_records(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.append_record("task-1", {"title": "A"})
    store.append_record("task-1", {"title": "B"})

    assert [item["title"] for item in store.read_records("task-1")] == ["A", "B"]


def test_jsonl_strict_false_skips_corrupted_lines(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    path = store.results_dir / "task-1.jsonl"
    path.write_text('{"title":"A"}\nnot-json\n{"title":"B"}\n', encoding="utf-8")

    assert [item["title"] for item in store.read_records("task-1", strict=False)] == ["A", "B"]


def test_jsonl_strict_true_reports_corrupted_line_number(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    path = store.results_dir / "task-1.jsonl"
    path.write_text('{"title":"A"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(CorruptedFileError) as exc:
        store.read_records("task-1")

    assert exc.value.context["line"] == 2


def test_hash_index_add_has_load_iter_and_no_duplicates(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    store.add_hash("demo", "abc")
    store.add_hash("demo", "abc")
    store.add_hashes("demo", ["def", "ghi"])

    assert store.has_hash("demo", "abc")
    assert store.load_hashes("demo") == {"abc", "def", "ghi"}
    assert list(store.iter_hashes("demo")) == ["abc", "def", "ghi"]


def test_hash_index_supports_scopes(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    store.add_hash("demo", "abc", scope="spider")

    assert store.has_hash("demo", "abc", scope="spider")
    assert not store.has_hash("demo", "abc", scope="task")


def test_concurrent_hash_adds_do_not_duplicate_or_corrupt(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    threads = [threading.Thread(target=store.add_hash, args=("demo", f"h{i % 3}")) for i in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.load_hashes("demo") == {"h0", "h1", "h2"}


def test_multithreaded_result_append_does_not_drop_lines(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    threads = [threading.Thread(target=store.append_record, args=("task-1", {"index": index})) for index in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = store.read_records("task-1")
    assert len(records) == 40
    assert {record["index"] for record in records} == set(range(40))


def test_check_storage_returns_ok_for_fresh_store(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)

    result = store.check_storage()

    assert result["ok"] is True
    assert result["errors"] == []


def test_check_storage_detects_corrupted_json(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    (store.spiders_dir / "bad.json").write_text("{bad", encoding="utf-8")

    result = store.check_storage()

    assert result["ok"] is False
    assert any("bad.json" in error["path"] for error in result["errors"])


def test_repair_storage_dry_run_does_not_move_corrupted_files(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    bad = store.spiders_dir / "bad.json"
    bad.write_text("{bad", encoding="utf-8")

    result = store.repair_storage(dry_run=True)

    assert bad.exists()
    assert any(action["action"] == "move_corrupted" for action in result["actions"])


def test_repair_storage_apply_moves_corrupted_files(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    bad = store.tasks_dir / "bad.json"
    bad.write_text("{bad", encoding="utf-8")

    result = store.repair_storage(dry_run=False)

    assert not bad.exists()
    assert any(action["action"] == "move_corrupted" for action in result["actions"])
    assert list((store.dead_letters_dir / "corrupted").glob("bad-*.json"))


def test_create_snapshot_generates_manifest(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_spider_config(_valid_spider_dict())
    manifest = store.create_snapshot(name="daily")

    assert manifest["snapshot_id"]
    assert "spiders" in manifest["included_paths"]
    assert (store.snapshots_dir / manifest["snapshot_id"] / "manifest.json").exists()


def test_restore_snapshot_dry_run_returns_plan(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_spider_config(_valid_spider_dict())
    manifest = store.create_snapshot(name="daily")

    plan = store.restore_snapshot(manifest["snapshot_id"], dry_run=True)

    assert plan["dry_run"] is True
    assert plan["snapshot_id"] == manifest["snapshot_id"]
    assert any(action["action"] == "restore" for action in plan["actions"])


def test_cli_storage_check(workspace_tmp_path, capsys):
    exit_code = main(["--data-dir", str(workspace_tmp_path), "storage", "check"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Storage OK" in captured.out


def test_cli_storage_snapshot_create_and_list(workspace_tmp_path, capsys):
    assert main(["--data-dir", str(workspace_tmp_path), "storage", "snapshot", "create"]) == 0
    assert main(["--data-dir", str(workspace_tmp_path), "storage", "snapshot", "list"]) == 0

    captured = capsys.readouterr()
    assert "Created snapshot" in captured.out


def test_fastapi_storage_health_endpoint(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    app = create_app(workspace_tmp_path)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/storage/health")

    assert route.endpoint()["ok"] is True


def test_fastapi_snapshot_endpoints(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    app = create_app(workspace_tmp_path)
    create_route = next(route for route in app.routes if getattr(route, "path", None) == "/storage/snapshots" and "POST" in route.methods)
    list_route = next(route for route in app.routes if getattr(route, "path", None) == "/storage/snapshots" and "GET" in route.methods)

    manifest = create_route.endpoint()

    assert manifest["snapshot_id"]
    assert list_route.endpoint()[0]["snapshot_id"] == manifest["snapshot_id"]
