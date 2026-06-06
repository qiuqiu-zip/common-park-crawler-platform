import json
from pathlib import Path

from crawler_platform.cli import main


def test_doctor_human_and_json(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path / "doctor")

    assert main(["doctor", "--data-dir", data_dir]) == 0
    human = capsys.readouterr().out
    assert "Doctor passed" in human
    assert "dependency_boundary" in human

    assert main(["doctor", "--data-dir", data_dir, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    names = {check["name"] for check in payload["checks"]}
    assert {"package_import", "cli_entrypoint", "storage_check", "dependency_boundary"}.issubset(names)


def test_run_human_summary_and_json_mode(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path / "run-summary")

    assert main(["run", "examples/local_api_json.json", "--task-id", "summary-task", "--data-dir", data_dir]) == 0
    human = capsys.readouterr().out
    assert "Run success: summary-task" in human
    assert "Results:" in human
    assert "Report:" in human
    assert "Next export: python -m crawler_platform.cli export task summary-task" in human

    json_data_dir = str(workspace_tmp_path / "run-summary-json")
    assert main(["run", "examples/local_api_json.json", "--task-id", "summary-json", "--data-dir", json_data_dir, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "summary-json"
    assert payload["status"] == "success"
    assert payload["records_count"] >= 1


def test_examples_quickstart_and_init_spider(workspace_tmp_path, capsys):
    assert main(["examples", "list", "--quickstart", "--json"]) == 0
    examples = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in examples}
    assert {"local-api-json", "local-html-list", "pagination-page"}.issubset(ids)
    assert all(item["quickstart"] for item in examples)

    target = workspace_tmp_path / "starter-api.json"
    assert main(["init", "spider", "--type", "api", "--output", str(target), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert Path(payload["output"]).exists()
    assert main(["validate", str(target)]) == 0


def test_task_show_paths_after_run(workspace_tmp_path, capsys):
    data_dir = str(workspace_tmp_path / "paths")
    assert main(["run", "examples/local_api_json.json", "--task-id", "paths-task", "--data-dir", data_dir]) == 0
    capsys.readouterr()

    assert main(["task", "show", "paths-task", "--paths", "--data-dir", data_dir]) == 0
    output = capsys.readouterr().out
    assert "paths-task" in output
    assert "result_jsonl" in output
    assert "report" in output
    assert "logs" in output
    assert "metrics" in output
    assert "exports" in output
