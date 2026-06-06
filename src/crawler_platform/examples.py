from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config_loader import load_spider_config, validate_spider_config
from .engine import CrawlerEngine
from .exporter import ExportService
from .models import TaskStatus
from .scheduler import SchedulerService
from .storage import FileStore
from .validation import SpiderConfigValidationError, ValidationIssue, ValidationResult
from .worker import WorkerService
from .url_seed import collect_seed_urls

INDEX_VERSION = "1.0"
INDEX_FILE = "index.json"
QUICKSTART_EXAMPLE_IDS = (
    "local-api-json",
    "local-html-list",
    "pagination-page",
    "detail-follow",
)
TEMPLATE_FILES = [
    "http_basic.json",
    "api_basic.json",
    "playwright_basic.json",
    "pagination.json",
    "detail_follow.json",
    "incremental.json",
    "scheduler.json",
    "worker_job.json",
    "session.json",
    "observability.json",
    "export.json",
]
REQUIRED_ENTRY_FIELDS = ("id", "title", "feature", "path", "tags", "runnable", "requires_external_network")
SENSITIVE_TEMPLATE_PATTERN = re.compile(r"\b(password|token|secret)\b", re.IGNORECASE)


class ExampleError(RuntimeError):
    pass


def project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "examples" / INDEX_FILE).exists() or (cwd / "examples").exists():
        return cwd
    return Path(__file__).resolve().parents[2]


def examples_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    return project_root() / "examples"


def load_examples_index(root: str | Path | None = None) -> dict[str, Any]:
    base = examples_root(root)
    path = base / INDEX_FILE
    if not path.exists():
        raise FileNotFoundError(f"Examples index not found: {path}")
    payload = _read_json(path)
    payload.setdefault("version", INDEX_VERSION)
    payload.setdefault("examples", [])
    return payload


def list_examples(
    root: str | Path | None = None,
    *,
    include_templates: bool = True,
    quickstart_only: bool = False,
) -> list[dict[str, Any]]:
    base = examples_root(root)
    entries = []
    for entry in load_examples_index(base).get("examples", []):
        if quickstart_only and entry.get("id") not in QUICKSTART_EXAMPLE_IDS:
            continue
        if not include_templates and entry.get("template"):
            continue
        detail = _entry_detail(base, entry, include_config=False)
        detail["quickstart"] = detail.get("id") in QUICKSTART_EXAMPLE_IDS
        entries.append(detail)
    return entries


def get_example(example_id: str, root: str | Path | None = None) -> dict[str, Any]:
    base = examples_root(root)
    entry = _find_entry(example_id, base)
    return _entry_detail(base, entry, include_config=True)


def validate_examples(root: str | Path | None = None) -> dict[str, Any]:
    base = examples_root(root)
    errors: list[dict[str, Any]] = []
    example_results: list[dict[str, Any]] = []
    template_results: list[dict[str, Any]] = []
    payload = load_examples_index(base)
    if str(payload.get("version")) != INDEX_VERSION:
        errors.append({"path": "version", "message": f"must be {INDEX_VERSION}"})
    entries = payload.get("examples", [])
    if not isinstance(entries, list):
        return {"valid": False, "errors": [{"path": "examples", "message": "must be a list"}], "examples": [], "templates": []}

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        entry_path = f"examples[{index}]"
        entry_errors = _validate_entry_shape(entry, entry_path, seen, base)
        errors.extend(entry_errors)
        if entry_errors:
            example_results.append({"id": entry.get("id"), "valid": False, "errors": entry_errors})
            continue

        path = _resolve_entry_path(base, entry)
        discovered_fixtures = _discover_fixture_paths(path)
        template = bool(entry.get("template"))
        if template:
            template_errors = _validate_template_file(path)
            config_result = _validate_spider_file(path)
            template_errors.extend(config_result.get("errors", []))
            template_results.append({"id": entry["id"], "path": entry["path"], "valid": not template_errors, "errors": template_errors})
            errors.extend(template_errors)
            continue

        if entry.get("example_type", "spider") == "spider":
            config_result = _validate_spider_file(path)
        else:
            config_result = _validate_json_artifact(path)
        if entry.get("runnable") and entry.get("requires_external_network"):
            config_result["errors"].append({"path": entry["path"], "message": "runnable examples cannot require external network"})
            config_result["valid"] = False
        if not entry.get("requires_external_network") and _has_external_urls(path):
            config_result["errors"].append({"path": entry["path"], "message": "example uses external URLs"})
            config_result["valid"] = False
        if entry.get("smoke") and not isinstance(entry.get("expected"), dict):
            config_result["errors"].append({"path": entry["path"], "message": "smoke examples require expected"})
            config_result["valid"] = False
        config_result["fixture_paths"] = discovered_fixtures
        example_results.append(config_result)
        if not config_result["valid"]:
            errors.extend(config_result["errors"])

    indexed_paths = {str(entry.get("path", "")).replace("\\", "/") for entry in entries if isinstance(entry, dict)}
    for template_file in TEMPLATE_FILES:
        relative = f"examples/templates/{template_file}"
        path = project_root() / relative
        if not path.exists():
            error = {"path": relative, "message": "required template is missing"}
            errors.append(error)
            template_results.append({"id": template_file.removesuffix(".json"), "path": relative, "valid": False, "errors": [error]})
        elif relative not in indexed_paths:
            error = {"path": relative, "message": "required template is not indexed"}
            errors.append(error)

    return {
        "valid": not errors,
        "version": payload.get("version"),
        "count": len(entries),
        "errors": errors,
        "examples": example_results,
        "templates": template_results,
    }


def smoke_examples(
    data_dir: str | Path,
    *,
    ids: list[str] | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = examples_root(root)
    selected = _select_smoke_entries(base, ids)
    results: list[dict[str, Any]] = []
    for entry in selected:
        if entry.get("requires_playwright"):
            results.append({"id": entry["id"], "status": "skipped", "reason": "requires_playwright"})
            continue
        if entry.get("requires_external_network"):
            results.append({"id": entry["id"], "status": "skipped", "reason": "requires_external_network"})
            continue
        results.append(_run_smoke_entry(base, entry, Path(data_dir) / _safe_name(entry["id"])))
    failed = [item for item in results if item.get("status") not in {"success", "skipped"}]
    return {"valid": not failed, "count": len(results), "results": results}


def copy_example(example_id: str, target: str | Path, root: str | Path | None = None) -> dict[str, Any]:
    base = examples_root(root)
    entry = _find_entry(example_id, base)
    source = _resolve_entry_path(base, entry)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {"id": entry["id"], "source": str(source), "target": str(destination), "copied": True}


def _entry_detail(base: Path, entry: dict[str, Any], *, include_config: bool) -> dict[str, Any]:
    detail = dict(entry)
    path = _resolve_entry_path(base, entry)
    detail["path_exists"] = path.exists()
    detail["fixture_paths"] = sorted(set(entry.get("fixture_paths") or []) | set(_discover_fixture_paths(path)))
    if include_config and path.suffix.lower() == ".json":
        detail["config"] = _read_json(path)
    return detail


def _find_entry(example_id: str, base: Path) -> dict[str, Any]:
    normalized = example_id.strip()
    candidates = {normalized, normalized.replace("_", "-")}
    if not normalized.startswith("template-"):
        candidates.add(f"template-{normalized}")
    for entry in load_examples_index(base).get("examples", []):
        if entry.get("id") in candidates:
            return entry
    raise FileNotFoundError(f"Example not found: {example_id}")


def _select_smoke_entries(base: Path, ids: list[str] | None) -> list[dict[str, Any]]:
    if ids:
        return [_find_entry(example_id, base) for example_id in ids]
    return [
        entry
        for entry in load_examples_index(base).get("examples", [])
        if entry.get("smoke") and entry.get("runnable") and not entry.get("template")
    ]


def _run_smoke_entry(base: Path, entry: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    path = _resolve_entry_path(base, entry)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    store = FileStore(data_dir)
    runner = entry.get("runner", "engine")
    try:
        if runner == "worker":
            payload = _run_worker_smoke(path, store, entry)
        elif runner == "scheduler":
            payload = _run_scheduler_smoke(path, store, entry)
        elif runner == "export":
            payload = _run_export_smoke(path, store, entry)
        else:
            payload = _run_engine_smoke(path, store, entry)
    except Exception as exc:
        return {"id": entry["id"], "status": "failed", "error": type(exc).__name__, "message": str(exc)}
    expected = entry.get("expected", {})
    check = _check_expected(payload, expected)
    return {"id": entry["id"], **payload, "status": "success" if check["valid"] else "failed", "checks": check}


def _run_engine_smoke(path: Path, store: FileStore, entry: dict[str, Any]) -> dict[str, Any]:
    spider = load_spider_config(path)
    task = CrawlerEngine(store=store).run(spider, task_id=f"smoke-{_safe_name(entry['id'])}")
    return {"runner": "engine", "task_id": task.id, "task_status": task.status.value, "records_count": task.saved_records or task.saved_count}


def _run_scheduler_smoke(path: Path, store: FileStore, entry: dict[str, Any]) -> dict[str, Any]:
    spider = load_spider_config(path)
    scheduler = SchedulerService(store=store, engine=CrawlerEngine(store=store))
    job = scheduler.register_spider_schedule(spider)
    if job is None:
        raise ExampleError(f"schedule was not registered for {spider.id}")
    outcome = scheduler.trigger_schedule_now(job.id)
    task_id = outcome.get("task_id")
    records_count = len(store.read_records(task_id, strict=False)) if task_id else 0
    return {"runner": "scheduler", "schedule_id": job.id, "task_id": task_id, "task_status": outcome.get("status"), "records_count": records_count}


def _run_worker_smoke(path: Path, store: FileStore, entry: dict[str, Any]) -> dict[str, Any]:
    spider = load_spider_config(path)
    worker = WorkerService(store=store, engine=CrawlerEngine(store=store))
    job = worker.enqueue_spider_run(spider, source="examples-smoke", job_id=f"smoke-{_safe_name(entry['id'])}")
    run = worker.run_once(worker_id="examples-smoke")
    task_id = run.task_id
    records_count = len(store.read_records(task_id, strict=False)) if task_id else 0
    return {"runner": "worker", "job_id": job.job_id, "task_id": task_id, "task_status": run.status, "records_count": records_count}


def _run_export_smoke(path: Path, store: FileStore, entry: dict[str, Any]) -> dict[str, Any]:
    spider = load_spider_config(path)
    task = CrawlerEngine(store=store).run(spider, task_id=f"smoke-{_safe_name(entry['id'])}")
    manifest = ExportService(store).export_task(task.id, fmt="jsonl")
    return {
        "runner": "export",
        "task_id": task.id,
        "task_status": task.status.value,
        "records_count": task.saved_records or task.saved_count,
        "export_id": manifest.get("export_id"),
        "export_rows_count": manifest.get("rows_count"),
    }


def _check_expected(payload: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    expected_status = expected.get("status")
    if expected_status and payload.get("task_status") != expected_status:
        errors.append({"path": "expected.status", "message": f"expected {expected_status}, got {payload.get('task_status')}"})
    min_records = expected.get("min_records")
    if min_records is not None and int(payload.get("records_count") or 0) < int(min_records):
        errors.append({"path": "expected.min_records", "message": f"expected at least {min_records} records"})
    return {"valid": not errors, "errors": errors}


def _validate_entry_shape(entry: Any, entry_path: str, seen: set[str], base: Path) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(entry, dict):
        return [{"path": entry_path, "message": "must be an object"}]
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            errors.append({"path": f"{entry_path}.{field}", "message": "required"})
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        errors.append({"path": f"{entry_path}.id", "message": "must be a non-empty string"})
    elif entry_id in seen:
        errors.append({"path": f"{entry_path}.id", "message": f"duplicate id: {entry_id}"})
    else:
        seen.add(entry_id)
    tags = entry.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
        errors.append({"path": f"{entry_path}.tags", "message": "must be a list of non-empty strings"})
    for field in ("runnable", "requires_external_network"):
        if field in entry and not isinstance(entry[field], bool):
            errors.append({"path": f"{entry_path}.{field}", "message": "must be a boolean"})
    if "requires_playwright" in entry and not isinstance(entry["requires_playwright"], bool):
        errors.append({"path": f"{entry_path}.requires_playwright", "message": "must be a boolean"})
    if "fixture_paths" in entry and (
        not isinstance(entry["fixture_paths"], list)
        or not all(isinstance(path, str) and path.strip() for path in entry["fixture_paths"])
    ):
        errors.append({"path": f"{entry_path}.fixture_paths", "message": "must be a list of non-empty strings"})
    if isinstance(entry.get("path"), str):
        path = _resolve_entry_path(base, entry)
        if not path.exists():
            errors.append({"path": f"{entry_path}.path", "message": f"does not exist: {entry.get('path')}"})
    for fixture in entry.get("fixture_paths") or []:
        if not (project_root() / fixture).exists():
            errors.append({"path": f"{entry_path}.fixture_paths", "message": f"does not exist: {fixture}"})
    return errors


def _validate_spider_file(path: Path) -> dict[str, Any]:
    try:
        spider = load_spider_config(path)
        result = validate_spider_config(spider)
    except SpiderConfigValidationError as exc:
        result = exc.result
    except Exception as exc:
        result = ValidationResult(valid=False, issues=[ValidationIssue(str(path), str(exc))])
    return {"id": _safe_name(path.stem), "path": str(path), **result.to_dict()}


def _validate_json_artifact(path: Path) -> dict[str, Any]:
    try:
        _read_json(path)
        return {"id": _safe_name(path.stem), "path": str(path), "valid": True, "errors": []}
    except Exception as exc:
        return {"id": _safe_name(path.stem), "path": str(path), "valid": False, "errors": [{"path": str(path), "message": str(exc)}]}


def _validate_template_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if SENSITIVE_TEMPLATE_PATTERN.search(text):
        return [{"path": str(path), "message": "template contains password/token/secret text"}]
    return []


def _has_external_urls(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    payload = _read_json(path)
    return any(_is_external_url(url) for url in _config_urls(payload))


def _config_urls(payload: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    urls.extend(str(item) for item in payload.get("start_urls", []) if isinstance(item, str))
    request_url = payload.get("request", {}).get("url") if isinstance(payload.get("request"), dict) else None
    if isinstance(request_url, str):
        urls.append(request_url)
    seed = payload.get("seed")
    if seed is not None:
        try:
            urls.extend(collect_seed_urls(seed))
        except Exception:
            pass
    pagination = payload.get("pagination", {})
    if isinstance(pagination, dict):
        urls.extend(str(item) for item in pagination.get("urls", []) if isinstance(item, str))
    session = payload.get("session", {})
    if isinstance(session, dict):
        for flow_name in ("login_flow", "refresh_flow"):
            flow = session.get(flow_name, {})
            for step in flow.get("steps", []) if isinstance(flow, dict) else []:
                if isinstance(step, dict) and isinstance(step.get("url"), str):
                    urls.append(step["url"])
    return urls


def _is_external_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _discover_fixture_paths(path: Path) -> list[str]:
    if not path.exists() or path.suffix.lower() != ".json":
        return []
    root = project_root()
    payload = _read_json(path)
    fixtures: set[str] = set()
    for url in _config_urls(payload):
        candidate = (root / url).resolve()
        try:
            relative = candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if "fixtures" in relative.parts and candidate.exists():
            fixtures.add(relative.as_posix())
    return sorted(fixtures)


def _resolve_entry_path(base: Path, entry: dict[str, Any]) -> Path:
    relative = Path(str(entry["path"]))
    if relative.is_absolute():
        return relative
    return project_root() / relative


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "example"
