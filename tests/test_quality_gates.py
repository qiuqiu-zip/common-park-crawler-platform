import json
from pathlib import Path

from scripts import quality_gate


def test_quality_gate_quick_passes_and_writes_json_report(workspace_tmp_path):
    report_path = workspace_tmp_path / "quick-report.json"

    report = quality_gate.run_quality_gate(mode="quick", json_report=report_path)
    written = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["status"] == "passed"
    assert written["status"] == "passed"
    assert written["summary"]["failed"] == 0
    assert {check["name"] for check in written["checks"]}.issuperset(
        {
            "compileall",
            "feature_matrix",
            "cli_matrix",
            "api_matrix",
            "examples_validate",
            "examples_smoke",
            "database_dependency_scan",
            "external_network_guard",
            "sensitive_value_scan",
            "openapi_generation",
            "web_admin_static_assets",
            "docs_required",
        }
    )


def test_database_dependency_scan_passes_and_detects_temp_violation(workspace_tmp_path):
    clean = quality_gate.scan_database_dependencies()
    bad = workspace_tmp_path / "bad_runtime.py"
    bad.write_text("import sqlalchemy\n", encoding="utf-8")

    violation = quality_gate.scan_database_dependencies([bad])

    assert clean["status"] == "passed"
    assert clean["details"]["matches"] == []
    assert violation["status"] == "failed"
    assert violation["details"]["matches"][0]["path"].endswith("bad_runtime.py")


def test_sensitive_value_scan_allows_placeholders_and_rejects_cleartext(workspace_tmp_path):
    safe = workspace_tmp_path / "safe.json"
    unsafe = workspace_tmp_path / "unsafe.json"
    safe.write_text('{"api_key":"${API_KEY}","token":"***REDACTED***","cookie":"local-fixture"}', encoding="utf-8")
    unsafe.write_text('{"authorization":"Bearer real-production-secret"}', encoding="utf-8")

    assert quality_gate.scan_sensitive_values([safe])["status"] == "passed"
    failed = quality_gate.scan_sensitive_values([unsafe])
    assert failed["status"] == "failed"
    assert failed["details"]["findings"][0]["key"] == "$.authorization"


def test_external_network_guard_and_docs_checks_pass():
    assert quality_gate.check_external_network_guard()["status"] == "passed"
    assert quality_gate.check_required_docs()["status"] == "passed"
