from __future__ import annotations

import argparse
import compileall
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FEATURE_MATRIX_PATH = ROOT / "tests" / "feature_matrix.json"
CLI_MATRIX_PATH = ROOT / "tests" / "cli_matrix.json"
API_MATRIX_PATH = ROOT / "tests" / "api_matrix.json"
DEFAULT_REPORT_DIR = ROOT / "test-output" / "feature19-quality"

DB_DEPENDENCY_PATTERN = re.compile(
    r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b",
    re.IGNORECASE,
)
EXTERNAL_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
SENSITIVE_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "access_key",
    "private_key",
)
SAFE_VALUE_MARKERS = (
    "redacted",
    "placeholder",
    "example",
    "sample",
    "dummy",
    "fake",
    "test",
    "local",
    "fixture",
    "demo",
    "task-",
    "login-",
    "refresh-",
    "public-",
    "storage-state",
)
REQUIRED_FEATURE_IDS = [f"F{number:02d}" for number in range(1, 19)]
REQUIRED_CLI_IDS = {
    "help",
    "validate-examples",
    "run-local-example",
    "storage-check",
    "examples-list",
    "examples-validate",
    "examples-smoke",
    "scheduler-list",
    "scheduler-register",
    "scheduler-run-due",
    "worker-enqueue",
    "worker-run-once",
    "worker-jobs",
    "worker-stats",
    "session-list",
    "session-events",
    "observability-logs",
    "observability-metrics",
    "observability-report",
    "export-task",
    "export-list",
    "export-show",
}
REQUIRED_API_PATHS = {
    "/health",
    "/runtime/info",
    "/spiders",
    "/tasks",
    "/storage/health",
    "/scheduler/schedules",
    "/worker/jobs",
    "/sessions",
    "/observability/logs",
    "/exports",
    "/examples",
    "/admin",
}
REQUIRED_SMOKE_TAGS = {"http", "api", "pagination", "detail", "incremental", "scheduler", "worker", "session", "observability", "export"}


def run_quality_gate(*, mode: str = "quick", json_report: str | Path | None = None) -> dict[str, Any]:
    started_at = _utc_now()
    started = time.perf_counter()
    checks: list[dict[str, Any]] = []
    checks.append(_run_check("compileall", check_compileall))
    checks.append(_run_check("feature_matrix", validate_feature_matrix))
    checks.append(_run_check("cli_matrix", validate_cli_matrix))
    checks.append(_run_check("api_matrix", validate_api_matrix))
    checks.append(_run_check("examples_matrix", validate_examples_matrix))
    checks.append(_run_check("examples_validate", check_examples_validate))
    checks.append(_run_check("examples_smoke", lambda: check_examples_smoke(mode=mode)))
    checks.append(_run_check("database_dependency_scan", check_database_dependencies))
    checks.append(_run_check("external_network_guard", check_external_network_guard))
    checks.append(_run_check("sensitive_value_scan", check_sensitive_values))
    checks.append(_run_check("openapi_generation", check_openapi_generation))
    checks.append(_run_check("web_admin_static_assets", check_web_admin_static_assets))
    checks.append(_run_check("docs_required", check_required_docs))
    if mode == "full":
        checks.append(_run_check("pytest", check_pytest_full))
    else:
        checks.append(_skipped("pytest", "quick mode skips the full pytest subprocess; run --full for that gate"))
    return _build_report(started, checks, started_at=started_at, json_report=json_report)


def check_compileall() -> dict[str, Any]:
    paths = [ROOT / "src", ROOT / "tests", ROOT / "scripts"]
    failed = [str(path.relative_to(ROOT)) for path in paths if path.exists() and not compileall.compile_dir(str(path), quiet=1)]
    return {"status": "failed" if failed else "passed", "details": {"paths": [str(path.relative_to(ROOT)) for path in paths], "failed": failed}}


def validate_feature_matrix() -> dict[str, Any]:
    payload = _read_json(FEATURE_MATRIX_PATH)
    features = payload.get("features", [])
    errors: list[dict[str, Any]] = []
    if payload.get("version") != "1.0":
        errors.append({"path": "version", "message": "must be 1.0"})
    ids = [feature.get("feature_id") for feature in features if isinstance(feature, dict)]
    if ids != REQUIRED_FEATURE_IDS:
        errors.append({"path": "features", "message": f"must contain {REQUIRED_FEATURE_IDS}"})
    for index, feature in enumerate(features):
        errors.extend(_validate_feature_entry(feature, index))
    return {"status": "failed" if errors else "passed", "details": {"count": len(features), "errors": errors}}


def validate_cli_matrix() -> dict[str, Any]:
    payload = _read_json(CLI_MATRIX_PATH)
    commands = payload.get("commands", [])
    errors: list[dict[str, Any]] = []
    ids = {entry.get("id") for entry in commands if isinstance(entry, dict)}
    missing = sorted(REQUIRED_CLI_IDS - ids)
    if missing:
        errors.append({"path": "commands", "message": f"missing required CLI ids: {missing}"})
    for index, entry in enumerate(commands):
        if not isinstance(entry, dict):
            errors.append({"path": f"commands[{index}]", "message": "must be an object"})
            continue
        if not entry.get("id"):
            errors.append({"path": f"commands[{index}].id", "message": "required"})
        if not isinstance(entry.get("argv"), list) or not entry["argv"]:
            errors.append({"path": f"commands[{index}].argv", "message": "must be a non-empty list"})
        if entry.get("mode") not in {"quick", "full"}:
            errors.append({"path": f"commands[{index}].mode", "message": "must be quick or full"})
    return {"status": "failed" if errors else "passed", "details": {"count": len(commands), "errors": errors}}


def validate_api_matrix() -> dict[str, Any]:
    payload = _read_json(API_MATRIX_PATH)
    endpoints = payload.get("endpoints", [])
    errors: list[dict[str, Any]] = []
    paths = {entry.get("path") for entry in endpoints if isinstance(entry, dict)}
    missing = sorted(REQUIRED_API_PATHS - paths)
    if missing:
        errors.append({"path": "endpoints", "message": f"missing required API paths: {missing}"})
    for index, entry in enumerate(endpoints):
        if not isinstance(entry, dict):
            errors.append({"path": f"endpoints[{index}]", "message": "must be an object"})
            continue
        if not entry.get("method") or not entry.get("path"):
            errors.append({"path": f"endpoints[{index}]", "message": "method and path are required"})
        if entry.get("envelope") not in {True, False}:
            errors.append({"path": f"endpoints[{index}].envelope", "message": "must be a boolean"})
    schema_paths = _openapi_paths()
    for path in sorted(paths):
        if path == "/admin":
            if not (ROOT / "src" / "crawler_platform" / "web" / "admin" / "index.html").exists():
                errors.append({"path": path, "message": "admin index.html is missing"})
        elif path == "/openapi.json":
            continue
        elif path not in schema_paths:
            errors.append({"path": path, "message": "path missing from OpenAPI schema"})
    return {"status": "failed" if errors else "passed", "details": {"count": len(endpoints), "errors": errors}}


def validate_examples_matrix() -> dict[str, Any]:
    from crawler_platform.examples import load_examples_index, validate_examples

    validation = validate_examples()
    payload = load_examples_index()
    smoke_entries = [
        entry
        for entry in payload.get("examples", [])
        if entry.get("smoke") and entry.get("runnable") and not entry.get("template")
    ]
    tags = {tag for entry in smoke_entries for tag in entry.get("tags", [])}
    missing_tags = sorted(REQUIRED_SMOKE_TAGS - tags)
    errors = [] if validation.get("valid") else list(validation.get("errors") or [])
    if missing_tags:
        errors.append({"path": "examples.smoke.tags", "message": f"missing smoke coverage tags: {missing_tags}"})
    if any(entry.get("requires_external_network") for entry in smoke_entries):
        errors.append({"path": "examples.smoke", "message": "smoke entries cannot require external network"})
    return {
        "status": "failed" if errors else "passed",
        "details": {"smoke_count": len(smoke_entries), "smoke_tags": sorted(tags), "errors": errors},
    }


def check_examples_validate() -> dict[str, Any]:
    from crawler_platform.examples import validate_examples

    payload = validate_examples()
    return {"status": "passed" if payload.get("valid") else "failed", "details": payload}


def check_examples_smoke(*, mode: str) -> dict[str, Any]:
    from crawler_platform.examples import smoke_examples

    data_dir = DEFAULT_REPORT_DIR / ("examples-smoke-full" if mode == "full" else "examples-smoke-quick")
    payload = smoke_examples(data_dir)
    return {"status": "passed" if payload.get("valid") else "failed", "details": payload}


def check_database_dependencies() -> dict[str, Any]:
    return scan_database_dependencies()


def scan_database_dependencies(paths: list[Path] | None = None) -> dict[str, Any]:
    candidates = paths or sorted((ROOT / "src").rglob("*.py")) + [ROOT / "pyproject.toml"]
    matches: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if DB_DEPENDENCY_PATTERN.search(line):
                matches.append({"path": _rel(path), "line": line_number, "text": line.strip()[:200]})
    return {"status": "failed" if matches else "passed", "details": {"matches": matches}}


def check_external_network_guard() -> dict[str, Any]:
    from crawler_platform.examples import load_examples_index

    errors: list[dict[str, Any]] = []
    payload = load_examples_index()
    for entry in payload.get("examples", []):
        if entry.get("runnable") and entry.get("requires_external_network"):
            errors.append({"path": f"examples[{entry.get('id')}]", "message": "runnable example requires external network"})
    for path in sorted((ROOT / "examples").rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        if EXTERNAL_URL_PATTERN.search(text) and not _is_allowed_real_external_example(path, text):
            errors.append({"path": _rel(path), "message": "external URL found in example JSON"})
    return {"status": "failed" if errors else "passed", "details": {"errors": errors}}


def _is_allowed_real_external_example(path: Path, text: str) -> bool:
    if not path.name.startswith("real_"):
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    if not isinstance(metadata, dict) or metadata.get("real_external_small_run") is not True:
        return False
    crawl_policy = payload.get("crawl_policy") or {}
    rate_limit = payload.get("rate_limit") or {}
    concurrency = payload.get("concurrency") or {}
    if not crawl_policy.get("enabled"):
        return False
    allowed_domains = [str(item).lower() for item in crawl_policy.get("allowed_domains") or []]
    if not allowed_domains or crawl_policy.get("allow_cross_domain") is not False:
        return False
    if int(crawl_policy.get("max_requests") or 0) > 20:
        return False
    if int(crawl_policy.get("max_depth") or 0) > 2:
        return False
    if not rate_limit.get("enabled") or float(rate_limit.get("requests_per_second") or 0) > 1:
        return False
    if not concurrency.get("enabled") or int(concurrency.get("max_concurrent_requests") or 0) > 1:
        return False
    if (payload.get("session") or {}).get("enabled") or (payload.get("proxy") or {}).get("enabled") or (payload.get("anti_bot") or {}).get("enabled"):
        return False
    request_configs = [payload.get("request") or {}, (payload.get("detail") or {}).get("request") or {}]
    if any(config.get("cookies") for config in request_configs):
        return False
    for url in _external_urls_from_payload(payload):
        host = urllib.parse.urlsplit(url).hostname or ""
        if host.lower() not in allowed_domains:
            return False
    return True


def _external_urls_from_payload(payload: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            urls.extend(_external_urls_from_payload(value))
    elif isinstance(payload, list):
        for value in payload:
            urls.extend(_external_urls_from_payload(value))
    elif isinstance(payload, str):
        urls.extend(match.group(0) for match in re.finditer(r"https?://[^\s\"']+", payload, flags=re.IGNORECASE))
    return urls


def check_sensitive_values() -> dict[str, Any]:
    paths = sorted((ROOT / "examples").rglob("*.json"))
    report_dir = DEFAULT_REPORT_DIR
    if report_dir.exists():
        paths.extend(sorted(path for path in report_dir.rglob("*") if path.suffix.lower() in {".json", ".jsonl"}))
    return scan_sensitive_values(paths)


def scan_sensitive_values(paths: list[Path]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                _scan_sensitive_payload(json.loads(path.read_text(encoding="utf-8")), path, "$", findings)
            except json.JSONDecodeError as exc:
                findings.append({"path": _rel(path), "key": "$", "reason": f"invalid json: {exc}"})
        elif suffix == ".jsonl":
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    _scan_sensitive_payload(json.loads(line), path, f"${line_number}", findings)
                except json.JSONDecodeError as exc:
                    findings.append({"path": _rel(path), "key": f"${line_number}", "reason": f"invalid jsonl: {exc}"})
    return {"status": "failed" if findings else "passed", "details": {"findings": findings}}


def check_openapi_generation() -> dict[str, Any]:
    paths = _openapi_paths()
    required = REQUIRED_API_PATHS - {"/admin"}
    missing = sorted(path for path in required if path not in paths)
    return {"status": "failed" if missing else "passed", "details": {"paths": len(paths), "missing": missing}}


def check_web_admin_static_assets() -> dict[str, Any]:
    admin = ROOT / "src" / "crawler_platform" / "web" / "admin"
    files = [admin / "index.html", admin / "assets" / "app.js", admin / "assets" / "api.js", admin / "assets" / "components.js", admin / "assets" / "styles.css"]
    errors: list[dict[str, Any]] = []
    for path in files:
        if not path.exists():
            errors.append({"path": _rel(path), "message": "missing"})
    text = "\n".join(path.read_text(encoding="utf-8") for path in files if path.exists())
    for marker in ("cdn", "unpkg", "jsdelivr", "cdnjs"):
        if marker in text.lower():
            errors.append({"path": "web_admin", "message": f"external asset marker found: {marker}"})
    if EXTERNAL_URL_PATTERN.search(text):
        errors.append({"path": "web_admin", "message": "external URL found in static admin assets"})
    if "/examples" not in text or "Examples" not in text:
        errors.append({"path": "web_admin", "message": "Examples API or section reference missing"})
    return {"status": "failed" if errors else "passed", "details": {"errors": errors}}


def check_required_docs() -> dict[str, Any]:
    required = [
        ROOT / "docs" / "architecture.md",
        ROOT / "docs" / "quick_start.md",
        ROOT / "docs" / "user_guide.md",
        ROOT / "docs" / "developer_guide.md",
        ROOT / "docs" / "cli.md",
        ROOT / "docs" / "config.md",
        ROOT / "docs" / "testing.md",
        ROOT / "docs" / "test_matrix.md",
        ROOT / "docs" / "final_acceptance.md",
        ROOT / "docs" / "delivery_checklist.md",
        ROOT / "docs" / "troubleshooting.md",
        ROOT / "docs" / "codex_workflow.md",
        ROOT / "docs" / "feature_status.md",
        ROOT / "docs" / "examples.md",
        ROOT / "examples" / "README.md",
        ROOT / "README.md",
    ]
    missing = [_rel(path) for path in required if not path.exists()]
    return {"status": "failed" if missing else "passed", "details": {"missing": missing}}


def check_pytest_full() -> dict[str, Any]:
    return run_command([sys.executable, "-m", "pytest", "-q"], name="pytest", timeout=300)


def run_cli_matrix(*, mode: str = "quick") -> dict[str, Any]:
    payload = _read_json(CLI_MATRIX_PATH)
    base_dir = DEFAULT_REPORT_DIR / "cli-matrix"
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    selected = [
        entry
        for entry in payload.get("commands", [])
        if entry.get("mode") == "quick" or mode == "full"
    ]
    results = []
    for entry in selected:
        if not entry.get("execute"):
            results.append({"id": entry.get("id"), "status": "skipped", "reason": "documented only"})
            continue
        argv = [part.replace("{data_dir}", str(base_dir / str(entry.get("id")))) for part in entry["argv"]]
        result = run_command([sys.executable, *argv], name=str(entry.get("id")), timeout=int(entry.get("timeout_seconds", 60)))
        results.append({"id": entry.get("id"), **result})
    failed = [item for item in results if item.get("status") == "failed"]
    return {"status": "failed" if failed else "passed", "details": {"count": len(results), "results": results}}


def run_command(command: list[str], *, name: str, timeout: int = 120) -> dict[str, Any]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) if not existing else str(SRC) + os.pathsep + existing
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return {
            "status": "failed",
            "details": {"command": _command_text(command), "reason": "timeout", "output": stdout[-1000:]},
        }
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "details": {"command": _command_text(command), "returncode": completed.returncode, "output": output[-2000:]},
    }


def _validate_feature_entry(feature: Any, index: int) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(feature, dict):
        return [{"path": f"features[{index}]", "message": "must be an object"}]
    required_fields = [
        "feature_id",
        "feature_name",
        "unit_tests",
        "integration_tests",
        "cli_tests",
        "api_tests",
        "examples",
        "docs",
        "required_commands",
        "offline_only",
        "database_free",
    ]
    for field in required_fields:
        if field not in feature:
            errors.append({"path": f"features[{index}].{field}", "message": "required"})
    if feature.get("offline_only") is not True:
        errors.append({"path": f"features[{index}].offline_only", "message": "must be true"})
    if feature.get("database_free") is not True:
        errors.append({"path": f"features[{index}].database_free", "message": "must be true"})
    test_refs = list(feature.get("unit_tests") or []) + list(feature.get("integration_tests") or []) + list(feature.get("cli_tests") or []) + list(feature.get("api_tests") or [])
    if not test_refs and not feature.get("not_applicable"):
        errors.append({"path": f"features[{index}]", "message": "must include tests or not_applicable reasons"})
    for field in ("unit_tests", "integration_tests", "examples", "docs"):
        for value in feature.get(field) or []:
            if isinstance(value, str) and value.startswith(("tests/", "examples/", "docs/", "README.md")):
                path = ROOT / value
                if not path.exists():
                    errors.append({"path": f"features[{index}].{field}", "message": f"missing path: {value}"})
    if not feature.get("required_commands"):
        errors.append({"path": f"features[{index}].required_commands", "message": "must not be empty"})
    return errors


def _scan_sensitive_payload(payload: Any, path: Path, location: str, findings: list[dict[str, Any]]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_location = f"{location}.{key}"
            if _is_sensitive_key(str(key)) and isinstance(value, (str, int, float, bool)) and not _is_safe_sensitive_value(value):
                findings.append({"path": _rel(path), "key": key_location, "reason": "sensitive-looking value is not fake/redacted/placeholder"})
            _scan_sensitive_payload(value, path, key_location, findings)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _scan_sensitive_payload(item, path, f"{location}[{index}]", findings)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEYS)


def _is_safe_sensitive_value(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"***redacted***", "<redacted>", "redacted", "none", "null"}:
        return True
    if lowered.startswith("${") and lowered.endswith("}"):
        return True
    return any(marker in lowered for marker in SAFE_VALUE_MARKERS)


def _openapi_paths() -> set[str]:
    from crawler_platform.api import create_app

    app = create_app(DEFAULT_REPORT_DIR / "openapi-data")
    return set(app.openapi().get("paths", {}).keys())


def _run_check(name: str, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = fn()
    except Exception as exc:
        return {"name": name, "status": "failed", "details": {"reason": type(exc).__name__, "message": str(exc)}, "duration_ms": _elapsed_ms(started)}
    return {"name": name, "status": payload.get("status", "failed"), "details": payload.get("details", {}), "duration_ms": _elapsed_ms(started)}


def _skipped(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "skipped", "details": {"reason": reason}, "duration_ms": 0}


def _build_report(started: float, checks: list[dict[str, Any]], *, started_at: str, json_report: str | Path | None) -> dict[str, Any]:
    status = "failed" if any(check["status"] == "failed" for check in checks) else "passed"
    report = {
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_ms": _elapsed_ms(started),
        "checks": checks,
        "summary": {
            "passed": sum(1 for check in checks if check["status"] == "passed"),
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "skipped": sum(1 for check in checks if check["status"] == "skipped"),
        },
    }
    if json_report:
        path = Path(json_report)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_text(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run crawler platform quality gates.")
    parser.add_argument("--quick", action="store_true", help="Run lightweight gates and skip the full pytest subprocess.")
    parser.add_argument("--full", action="store_true", help="Run all gates, including the full pytest subprocess.")
    parser.add_argument("--json-report", help="Write a JSON report to this path.")
    args = parser.parse_args(argv)
    if args.quick and args.full:
        parser.error("--quick and --full cannot be combined")
    mode = "full" if args.full else "quick"
    report = run_quality_gate(mode=mode, json_report=args.json_report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
