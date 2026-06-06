import json
from pathlib import Path

from scripts import quality_gate


def test_feature_matrix_covers_feature_01_to_18():
    payload = json.loads(Path("tests/feature_matrix.json").read_text(encoding="utf-8"))
    features = payload["features"]

    assert [feature["feature_id"] for feature in features] == [f"F{number:02d}" for number in range(1, 19)]
    for feature in features:
        assert feature["feature_name"]
        assert feature["offline_only"] is True
        assert feature["database_free"] is True
        assert feature["required_commands"]
        assert feature["unit_tests"] or feature["integration_tests"] or feature["cli_tests"] or feature["api_tests"] or feature.get("not_applicable")
        for field in ("unit_tests", "integration_tests", "examples", "docs"):
            for value in feature[field]:
                if value.startswith(("tests/", "examples/", "docs/", "README.md")):
                    assert Path(value).exists(), value


def test_feature_matrix_is_validated_by_quality_gate():
    result = quality_gate.validate_feature_matrix()

    assert result["status"] == "passed"
    assert result["details"]["count"] == 18
    assert result["details"]["errors"] == []
