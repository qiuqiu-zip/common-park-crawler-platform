import json
from pathlib import Path

from scripts import quality_gate


def test_cli_matrix_contains_required_command_groups():
    payload = json.loads(Path("tests/cli_matrix.json").read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in payload["commands"]}

    assert quality_gate.REQUIRED_CLI_IDS.issubset(ids)
    assert quality_gate.validate_cli_matrix()["status"] == "passed"


def test_cli_matrix_quick_commands_execute_offline():
    result = quality_gate.run_cli_matrix(mode="quick")

    assert result["status"] == "passed"
    executed = [item for item in result["details"]["results"] if item["status"] != "skipped"]
    assert executed
    assert all(item["details"]["returncode"] == 0 for item in executed)
