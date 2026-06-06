import json
from pathlib import Path

from scripts import quality_gate


def test_api_matrix_contains_required_endpoint_groups():
    payload = json.loads(Path("tests/api_matrix.json").read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in payload["endpoints"]}

    assert quality_gate.REQUIRED_API_PATHS.issubset(paths)
    assert "/openapi.json" in paths


def test_api_matrix_matches_openapi_and_static_admin():
    result = quality_gate.validate_api_matrix()

    assert result["status"] == "passed"
    assert result["details"]["errors"] == []
