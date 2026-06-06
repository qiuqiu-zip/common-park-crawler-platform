import json
from pathlib import Path

import pytest

from crawler_platform.cli import main
from crawler_platform.config_loader import dump_spider_config, load_spider_config, validate_spider_config
from crawler_platform.validation import ensure_valid_spider_config, spider_config_json_schema


def _valid_config():
    return {
        "id": "demo",
        "name": "Demo",
        "version": "1.0",
        "type": "api",
        "start_urls": ["https://example.test/api"],
        "items_json_path": "items",
        "fields": [
            {"name": "id", "type": "json_path", "json_path": "id"},
            {"name": "title", "type": "json_path", "json_path": "title"},
        ],
        "unique_fields": ["id"],
        "scheduler": {"type": "manual"},
    }


def test_validate_valid_config_and_dump_round_trip(workspace_tmp_path):
    spider = ensure_valid_spider_config(_valid_config())
    dumped = dump_spider_config(spider, workspace_tmp_path / "spider.json")

    assert validate_spider_config(spider).valid
    payload = json.loads(dumped)
    assert payload["version"] == "1.0"
    assert payload["type"] == "api"
    assert "mode" not in payload
    assert payload["scheduler"]["type"] == "manual"
    assert "schedule" not in payload
    assert load_spider_config(workspace_tmp_path / "spider.json").id == "demo"


def test_new_type_field_loads_and_dumps_canonical(workspace_tmp_path):
    path = workspace_tmp_path / "typed.json"
    path.write_text(json.dumps(_valid_config()), encoding="utf-8")

    spider = load_spider_config(path)
    payload = spider.to_dict()

    assert spider.type == "api"
    assert spider.mode == "api"
    assert payload["type"] == "api"
    assert "mode" not in payload


def test_new_scheduler_field_loads_and_dumps_canonical(workspace_tmp_path):
    path = workspace_tmp_path / "scheduled.json"
    config = _valid_config()
    config["scheduler"] = {"type": "interval", "interval_seconds": 60}
    path.write_text(json.dumps(config), encoding="utf-8")

    spider = load_spider_config(path)
    payload = spider.to_dict()

    assert spider.scheduler.type == "interval"
    assert spider.schedule.type == "interval"
    assert payload["scheduler"]["type"] == "interval"
    assert "schedule" not in payload


def test_validate_reports_missing_required_fields():
    result = validate_spider_config({"name": "Missing id"})

    assert not result.valid
    assert any(issue.path == "id" for issue in result.issues)
    assert any(issue.path == "start_urls" for issue in result.issues)


def test_validate_reports_illegal_values():
    config = _valid_config()
    config["type"] = "database"
    config["fields"][0]["type"] = "unknown"

    result = validate_spider_config(config)

    assert not result.valid
    assert any(issue.path == "type" for issue in result.issues)
    assert any(issue.path == "fields[0].type" for issue in result.issues)


def test_legacy_schema_version_is_accepted():
    config = _valid_config()
    config.pop("version")
    config["schema_version"] = "1.0"

    spider = ensure_valid_spider_config(config)

    assert spider.version == "1.0"


def test_legacy_mode_is_canonicalized():
    config = _valid_config()
    config["mode"] = config.pop("type")

    spider = ensure_valid_spider_config(config)
    payload = spider.to_dict()

    assert spider.type == "api"
    assert spider.mode == "api"
    assert payload["type"] == "api"
    assert "mode" not in payload


def test_legacy_schedule_is_canonicalized():
    config = _valid_config()
    config["schedule"] = config.pop("scheduler")

    spider = ensure_valid_spider_config(config)
    payload = spider.to_dict()

    assert payload["scheduler"]["type"] == "manual"
    assert "schedule" not in payload


def test_validate_reports_invalid_pagination_and_export():
    config = _valid_config()
    config["pagination"] = {"type": "database", "max_pages": 1}
    config["export"] = {"formats": ["json", "sql"]}

    result = validate_spider_config(config)

    assert not result.valid
    assert any(issue.path == "pagination.type" for issue in result.issues)
    assert any(issue.path == "export.formats" for issue in result.issues)


def test_json_schema_document_exists_and_describes_protocol():
    schema = spider_config_json_schema()
    on_disk = json.loads(Path("docs/spider_config.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["type"]["enum"] == ["http", "api", "playwright"]
    assert schema["properties"]["pagination"]["properties"]["type"]["enum"] == [
        "none",
        "page",
        "offset",
        "url_list",
        "next_button",
        "cursor",
    ]
    assert schema["properties"]["export"]["properties"]["formats"]["items"]["enum"] == ["json", "jsonl", "csv", "xlsx"]
    assert schema["properties"]["scheduler"]["properties"]["type"]["enum"] == ["manual", "cron", "interval"]
    assert "mode" not in schema["properties"]
    assert "schedule" not in schema["properties"]
    assert on_disk["title"] == schema["title"]


def test_cli_validate_examples(capsys):
    paths = [path for path in sorted(Path("examples").glob("*.json")) if path.name != "index.json"]
    exit_code = main(["validate", *[str(path) for path in paths]])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"valid": true' in captured.out


def test_cli_validate_invalid_config(workspace_tmp_path):
    invalid = workspace_tmp_path / "invalid.json"
    invalid.write_text('{"id": "", "start_urls": []}', encoding="utf-8")

    assert main(["validate", str(invalid)]) == 1


def test_api_validate_spider_endpoint(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    app = create_app(workspace_tmp_path / "data")
    route = next(route for route in app.routes if getattr(route, "path", None) == "/validate/spider")

    ok = route.endpoint(_valid_config())
    bad = route.endpoint({"type": "bad"})

    assert ok["valid"] is True
    assert ok["errors"] == []
    assert bad["valid"] is False
    assert bad["errors"]


def test_all_examples_use_canonical_type_and_scheduler():
    paths = [path for path in sorted(Path("examples").glob("*.json")) if path.name != "index.json"]

    assert {path.name for path in paths} >= {
        "html_list.json",
        "api_json.json",
        "playwright_page.json",
        "pagination_page.json",
        "pagination_offset.json",
        "detail_follow.json",
        "dedup.json",
        "scheduled_spider.json",
        "proxy_antibot.json",
    }
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == "1.0"
        assert "type" in payload
        assert "mode" not in payload
        assert "schedule" not in payload
        assert validate_spider_config(payload).valid, path.name
