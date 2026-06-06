from __future__ import annotations

import importlib
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any

from .examples import load_examples_index, project_root, validate_examples
from .storage import FileStore


def run_doctor(data_dir: str | Path = "data") -> dict[str, Any]:
    root = project_root()
    checks = [
        _check_runtime(),
        _check_package_import(),
        _check_cli_import(),
        _check_api_openapi(data_dir),
        _check_examples_index(),
        _check_examples_validation(),
        _check_storage(data_dir),
        _check_data_dir_writable(data_dir),
        _check_quality_gate(root),
        _check_dependency_boundary(root),
        _check_commit_boundary(root),
    ]
    failed = [check for check in checks if check["status"] == "failed"]
    skipped = [check for check in checks if check["status"] == "skipped"]
    return {
        "status": "passed" if not failed else "failed",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(Path(data_dir)),
        "checks": checks,
        "summary": {
            "passed": len([check for check in checks if check["status"] == "passed"]),
            "failed": len(failed),
            "skipped": len(skipped),
        },
    }


def _check_runtime() -> dict[str, Any]:
    return _check(
        "python_runtime",
        "passed",
        f"Python {sys.version.split()[0]} is available.",
        {"executable": sys.executable},
    )


def _check_package_import() -> dict[str, Any]:
    try:
        importlib.import_module("crawler_platform")
        return _check("package_import", "passed", "crawler_platform imports successfully.")
    except Exception as exc:
        return _check("package_import", "failed", str(exc), {"error": type(exc).__name__})


def _check_cli_import() -> dict[str, Any]:
    try:
        module = importlib.import_module("crawler_platform.cli")
        if not callable(getattr(module, "main", None)):
            return _check("cli_entrypoint", "failed", "crawler_platform.cli.main is not callable.")
        return _check("cli_entrypoint", "passed", "CLI entrypoint is importable.")
    except Exception as exc:
        return _check("cli_entrypoint", "failed", str(exc), {"error": type(exc).__name__})


def _check_api_openapi(data_dir: str | Path) -> dict[str, Any]:
    try:
        module = importlib.import_module("crawler_platform.api")
        app = module.create_app(data_dir)
        schema = app.openapi()
        return _check(
            "api_openapi",
            "passed",
            "FastAPI app factory and OpenAPI generation work.",
            {"paths": len(schema.get("paths", {}))},
        )
    except ModuleNotFoundError as exc:
        return _check(
            "api_openapi",
            "skipped",
            f"Optional API package is not installed: {exc.name}.",
            {"command": "pip install -e .[api]"},
        )
    except Exception as exc:
        return _check("api_openapi", "failed", str(exc), {"error": type(exc).__name__})


def _check_examples_index() -> dict[str, Any]:
    try:
        index = load_examples_index()
        return _check("examples_index", "passed", "Examples index is readable.", {"count": len(index.get("examples", []))})
    except Exception as exc:
        return _check("examples_index", "failed", str(exc), {"error": type(exc).__name__})


def _check_examples_validation() -> dict[str, Any]:
    try:
        result = validate_examples()
        return _check(
            "examples_validate",
            "passed" if result.get("valid") else "failed",
            "Bundled examples validate." if result.get("valid") else "Bundled examples have validation errors.",
            {"errors": result.get("errors", [])[:5], "count": result.get("count", 0)},
        )
    except Exception as exc:
        return _check("examples_validate", "failed", str(exc), {"error": type(exc).__name__})


def _check_storage(data_dir: str | Path) -> dict[str, Any]:
    try:
        result = FileStore(data_dir).check_storage()
        return _check(
            "storage_check",
            "passed" if result.get("ok") else "failed",
            "FileStore health check passed." if result.get("ok") else "FileStore health check found problems.",
            {"warnings": result.get("warnings", []), "errors": result.get("errors", [])},
        )
    except Exception as exc:
        return _check("storage_check", "failed", str(exc), {"error": type(exc).__name__})


def _check_data_dir_writable(data_dir: str | Path) -> dict[str, Any]:
    try:
        store = FileStore(data_dir)
        path = store.tmp_dir / "doctor-write-check.json"
        path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        path.unlink(missing_ok=True)
        return _check("data_dir_writable", "passed", "Data directory is writable.", {"path": str(store.root)})
    except Exception as exc:
        return _check("data_dir_writable", "failed", str(exc), {"error": type(exc).__name__})


def _check_quality_gate(root: Path) -> dict[str, Any]:
    script = root / "scripts" / "quality_gate.py"
    if not script.exists():
        return _check("quality_gate_quick", "skipped", "Quality gate script was not found.")
    return _check(
        "quality_gate_quick",
        "skipped",
        "Doctor does not run the quality gate automatically; run the quick gate when you need full evidence.",
        {"command": "python scripts/quality_gate.py --quick --json-report ./test-output/doctor-quality/quick-report.json"},
    )


def _check_dependency_boundary(root: Path) -> dict[str, Any]:
    terms = ["s" + "qlite", "s" + "qlalchemy", "my" + "sql", "post" + "gres", "psyco" + "pg"]
    short = "o" + "rm"
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(term) for term in terms) + r")\b"
        + r"|\b(from|import)\b.*\b" + short + r"\b"
        + r"|\b" + short + r"\b.*\b(from|import)\b",
        re.IGNORECASE,
    )
    paths = list((root / "src").rglob("*.py"))
    config_path = root / "pyproject.toml"
    if config_path.exists():
        paths.append(config_path)
    findings: list[dict[str, Any]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                findings.append({"path": str(path), "line": line_number, "text": line.strip()})
    if findings:
        return _check("dependency_boundary", "failed", "Prohibited data-store runtime dependency found.", {"findings": findings})
    return _check("dependency_boundary", "passed", "No prohibited data-store runtime imports found.", {"scanned": len(paths)})


def _check_commit_boundary(root: Path) -> dict[str, Any]:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return _check("commit_boundary", "skipped", ".gitignore not found; avoid committing generated data directories.")
    text = gitignore.read_text(encoding="utf-8", errors="replace")
    required = ["data/", "test-output/", ".pytest_cache/", "pytest-cache-files-*/", "__pycache__/", ".codex/"]
    missing = [item for item in required if item not in text]
    if "*.pyc" not in text and "*.py[cod]" not in text:
        missing.append("*.pyc")
    if missing:
        return _check("commit_boundary", "failed", "Generated-output ignore patterns are missing.", {"missing": missing})
    return _check("commit_boundary", "passed", "Generated outputs are covered by ignore patterns.")


def _check(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }
