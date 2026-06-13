from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .extractor import (
    ExtractorError,
    _as_document,
    _is_missing,
    _node_value,
    _scope_text,
    _select_child_scopes,
    _try_json,
    extract_fields,
    extract_value,
    json_path,
)
from .html_query import HTMLDocument, Node
from .http_client import FetchError, Fetcher, HttpFetcher, HttpRequest, HttpResponse, HttpStatusError, RequestContext, build_http_request, parse_response
from .models import FieldRule, SpiderConfig
from .playwright_runner import resolve_playwright_options
from .session import redact_sensitive
from .storage import FileStore
from .url_seed import collect_request_urls


@dataclass(slots=True)
class FieldDiagnostic:
    field: str
    selector_type: str
    selector: str | None
    attr: str | None
    match_count: int
    missing_count: int
    required: bool
    raw_sample: Any = None
    transformed_sample: Any = None
    errors: list[str] = field(default_factory=list)
    warning: str | None = None
    children: list["FieldDiagnostic"] = field(default_factory=list)


@dataclass(slots=True)
class FieldQualitySummary:
    field: str
    total_records: int
    non_empty_count: int
    empty_count: int
    missing_rate: float
    sample_values: list[Any] = field(default_factory=list)
    required: bool = False
    status: str = "ok"
    hint: str | None = None


@dataclass(slots=True)
class ExtractionDiagnostic:
    item_selector: str | None
    items_json_path: str | None
    total_scopes: int
    field_diagnostics: list[FieldDiagnostic] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DryRunReport:
    dry_run_id: str
    spider_id: str
    target_url: str
    final_url: str | None
    status_code: int | None
    response_type: str
    item_count: int
    sample_records: list[dict[str, Any]]
    field_diagnostics: list[FieldDiagnostic]
    field_quality: list[FieldQualitySummary]
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    artifact_path: str | None = None
    report_path: str | None = None
    duration_ms: float = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def debug_selector(input_file: str | Path, *, selector: str, selector_type: str = "css", sample_size: int = 5) -> dict[str, Any]:
    path = Path(input_file)
    content = path.read_text(encoding="utf-8")
    document = HTMLDocument(content)
    if selector_type == "css":
        matches = document.select(selector)
    elif selector_type == "xpath":
        matches = document.xpath(selector)
    else:
        raise ValueError("selector_type must be css or xpath")
    return {
        "input_file": str(path),
        "selector": selector,
        "selector_type": selector_type,
        "matched_count": len(matches),
        "sample_values": [_selector_sample_value(item) for item in matches[: max(0, sample_size)]],
    }


def debug_extract(
    spider: SpiderConfig,
    *,
    input_file: str | Path,
    sample_size: int = 5,
    single_url: str | None = None,
) -> dict[str, Any]:
    path = Path(input_file)
    content = path.read_text(encoding="utf-8")
    response_type = _response_type(spider)
    parsed = json.loads(content) if response_type == "json" else content
    source_url = single_url or str(path)
    scopes = _extract_scopes(parsed, content, spider)
    records = _extract_preview_records(scopes, spider, source_url, max(0, sample_size))
    diagnostic = diagnose_extraction(scopes, spider, source_url=source_url)
    quality = summarize_field_quality(records, spider.fields, sample_size=sample_size)
    return {
        "spider_id": spider.id,
        "input_file": str(path),
        "response_type": response_type,
        "scope_count": len(scopes),
        "sample_records": records,
        "field_diagnostics": [asdict(item) for item in diagnostic.field_diagnostics],
        "field_quality": [asdict(item) for item in quality],
        "missing_required_fields": [
            {"field": item.field, "missing": item.empty_count, "total_records": item.total_records, "missing_rate": item.missing_rate}
            for item in quality
            if item.required and item.empty_count
        ],
        "warnings": diagnostic.warnings,
    }


def run_dry_run(
    spider: SpiderConfig,
    store: FileStore,
    *,
    fetcher: Fetcher | None = None,
    max_pages: int = 1,
    max_records: int = 5,
    sample_size: int = 5,
    save_report: bool = False,
    dry_run_id: str | None = None,
) -> DryRunReport:
    started = time.perf_counter()
    fetcher = fetcher or HttpFetcher()
    dry_run_id = dry_run_id or f"dry-run-{uuid.uuid4().hex[:12]}"
    response_type = _response_type(spider)
    target_url = _first_target_url(spider)
    report = DryRunReport(
        dry_run_id=dry_run_id,
        spider_id=spider.id,
        target_url=target_url,
        final_url=None,
        status_code=None,
        response_type=response_type,
        item_count=0,
        sample_records=[],
        field_diagnostics=[],
        field_quality=[],
    )

    records: list[dict[str, Any]] = []
    scopes: list[Any] = []
    next_urls = [target_url] + list(spider.pagination.urls)
    for page_index, url in enumerate(next_urls[: max(1, max_pages)], start=1):
        if len(records) >= max_records:
            break
        request = _build_dry_run_request(spider, url, response_type, dry_run_id)
        try:
            response = fetcher.fetch(request)
        except HttpStatusError as exc:
            artifact_path = save_debug_artifact(store, dry_run_id, request=request, response=exc.response, error=exc)
            _record_failure(report, request, exc.response, exc, artifact_path)
            break
        except FetchError as exc:
            artifact_path = save_debug_artifact(store, dry_run_id, request=request, error=exc)
            _record_failure(report, request, None, exc, artifact_path)
            break
        except Exception as exc:
            artifact_path = save_debug_artifact(store, dry_run_id, request=request, error=exc)
            _record_failure(report, request, None, exc, artifact_path)
            break
        try:
            parsed = parse_response(response, response_type)
        except Exception as exc:
            artifact_path = save_debug_artifact(store, dry_run_id, request=request, response=response, error=exc)
            _record_failure(report, request, response, exc, artifact_path)
            break

        source_url = response.final_url or response.url
        page_scopes = _extract_scopes(parsed, response.text, spider)
        page_records = _extract_preview_records(page_scopes, spider, source_url, max_records - len(records))
        page_diagnostic = diagnose_extraction(page_scopes, spider, source_url=source_url)
        diagnostic_errors = _diagnostic_errors(page_diagnostic.field_diagnostics)
        if diagnostic_errors:
            artifact_path = save_debug_artifact(store, dry_run_id, request=request, response=response, error=RuntimeError("; ".join(diagnostic_errors)))
            report.artifact_path = report.artifact_path or artifact_path
        page_warnings = list(page_diagnostic.warnings)
        records.extend(page_records)
        scopes.extend(page_scopes)
        report.final_url = source_url
        report.status_code = response.status_code
        report.pages.append(
            {
                "page_index": page_index,
                "url": request.url,
                "final_url": source_url,
                "status_code": response.status_code,
                "response_type": response_type,
                "scope_count": len(page_scopes),
                "item_count": len(page_records),
                "warnings": page_warnings,
            }
        )
        report.warnings.extend(page_warnings)

    report.item_count = len(records)
    report.sample_records = records[: max(0, sample_size)]
    if scopes:
        diagnostic = diagnose_extraction(scopes, spider, source_url=report.final_url or target_url)
        report.field_diagnostics = diagnostic.field_diagnostics
        report.warnings = _unique_strings([*report.warnings, *diagnostic.warnings])
    report.field_quality = summarize_field_quality(records, spider.fields, sample_size=sample_size)
    report.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    if save_report:
        report.report_path = str(save_dry_run_report(store, report))
    return report


def diagnose_extraction(scopes: list[Any], spider: SpiderConfig, *, source_url: str | None = None) -> ExtractionDiagnostic:
    diagnostics = [_diagnose_field(scopes, rule, source_url=source_url) for rule in spider.fields]
    warnings = [item.warning for item in diagnostics if item.warning]
    return ExtractionDiagnostic(
        item_selector=spider.item_selector,
        items_json_path=spider.items_json_path,
        total_scopes=len(scopes),
        field_diagnostics=diagnostics,
        warnings=[warning for warning in warnings if warning],
    )


def summarize_field_quality(records: list[dict[str, Any]], fields: list[FieldRule], *, sample_size: int = 5) -> list[FieldQualitySummary]:
    total = len(records)
    summaries: list[FieldQualitySummary] = []
    for rule in fields:
        key = rule.namespace or rule.name
        values = [record.get(key) for record in records]
        non_empty_values = [value for value in values if not _empty_for_quality(value)]
        empty_count = total - len(non_empty_values)
        missing_rate = round(empty_count / total, 4) if total else 0.0
        status = "ok"
        hint = None
        if total == 0:
            status = "unknown"
            hint = "no records were extracted; field completeness was not evaluated"
        elif rule.required and missing_rate >= 1:
            status = "failed"
            hint = "required field is missing in every record; check selector, render readiness, or follow rules"
        elif (rule.required and missing_rate > 0) or (not rule.required and total and missing_rate >= 0.5):
            status = "warning"
            if rule.required:
                hint = f"required field is missing in {empty_count}/{total} records"
            else:
                hint = f"field is empty in {empty_count}/{total} records"
        elif empty_count:
            hint = f"field is empty in {empty_count}/{total} records"
        summaries.append(
            FieldQualitySummary(
                field=key,
                total_records=total,
                non_empty_count=len(non_empty_values),
                empty_count=empty_count,
                missing_rate=missing_rate,
                sample_values=non_empty_values[: max(0, sample_size)],
                required=rule.required,
                status=status,
                hint=hint,
            )
        )
    return summaries


def save_dry_run_report(store: FileStore, report: DryRunReport) -> Path:
    path = store.debug_reports_dir / f"{_safe_name(report.dry_run_id)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_debug_artifact(
    store: FileStore,
    run_id: str,
    *,
    request: HttpRequest | None = None,
    response: HttpResponse | None = None,
    error: Exception | None = None,
) -> str:
    artifact_root = store.debug_artifacts_dir / "tasks" / _safe_name(run_id)
    responses_dir = artifact_root / "responses"
    failed_pages_dir = artifact_root / "failed_pages"
    screenshots_dir = artifact_root / "screenshots"
    for directory in (responses_dir, failed_pages_dir, screenshots_dir):
        directory.mkdir(parents=True, exist_ok=True)

    artifact_id = uuid.uuid4().hex[:12]
    body_path = None
    failed_page_path = None
    body_prefix = None
    if response is not None:
        body_text = response.text
        body_prefix = body_text[:1000]
        body_path = responses_dir / f"{artifact_id}.txt"
        body_path.write_text(body_text, encoding=response.encoding or "utf-8", errors="replace")
        failed_page_path = failed_pages_dir / f"{artifact_id}.html"
        failed_page_path.write_text(body_text, encoding=response.encoding or "utf-8", errors="replace")

    metadata_path = artifact_root / "metadata.json"
    metadata = _read_artifact_metadata(metadata_path)
    metadata["dry_run_id"] = run_id
    metadata.setdefault("artifacts", []).append(
        {
            "artifact_id": artifact_id,
            "url": response.url if response is not None else (request.url if request is not None else None),
            "final_url": response.final_url if response is not None else None,
            "status_code": response.status_code if response is not None else None,
            "response_headers": redact_sensitive(response.headers) if response is not None else {},
            "request_headers": redact_sensitive(request.headers) if request is not None else {},
            "request_cookies": redact_sensitive(request.cookies) if request is not None else {},
            "error_type": _error_type(error),
            "message": str(error) if error is not None else None,
            "response_text_prefix": body_prefix,
            "response_path": str(body_path) if body_path is not None else None,
            "failed_page_path": str(failed_page_path) if failed_page_path is not None else None,
            "screenshot_path": None,
        }
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(artifact_root)


def _build_dry_run_request(spider: SpiderConfig, url: str, response_type: str, dry_run_id: str) -> HttpRequest:
    options = replace(spider.request, params=dict(spider.request.params))
    effective_playwright = None
    strategy_source = None
    if spider.type == "playwright" or spider.playwright.enabled:
        effective_playwright, strategy_source = resolve_playwright_options(
            spider.playwright,
            options.playwright,
            page_role="debug",
            override_source="request.playwright.debug" if options.playwright is not None else "spider.playwright",
        )
    return build_http_request(
        url,
        options,
        response_type=response_type,
        context=RequestContext(spider_id=spider.id, task_id=dry_run_id, start_url=url, response_type=response_type, page_role="debug"),
        playwright_options=effective_playwright,
        playwright_strategy_source=strategy_source,
    )


def _response_type(spider: SpiderConfig) -> str:
    return spider.request.response_type or ("json" if spider.type == "api" else "html")


def _first_target_url(spider: SpiderConfig) -> str:
    urls = collect_request_urls(spider)
    if urls:
        return urls[0]
    raise RuntimeError("spider has no start_urls, request.url, or seed")


def _extract_scopes(parsed: Any, content: str, spider: SpiderConfig) -> list[Any]:
    if isinstance(parsed, (dict, list)) and spider.items_json_path:
        items = json_path(parsed, spider.items_json_path)
        if isinstance(items, list):
            return items
        return [items] if items is not None else []
    data = _try_json(content)
    if data is not None and spider.items_json_path:
        items = json_path(data, spider.items_json_path)
        if isinstance(items, list):
            return items
        return [items] if items is not None else []
    document = HTMLDocument(content)
    return document.select(spider.item_selector) if spider.item_selector else [document.root]


def _extract_preview_records(scopes: list[Any], spider: SpiderConfig, source_url: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    records: list[dict[str, Any]] = []
    for scope in scopes[:limit]:
        records.append(extract_fields(scope, spider.fields, strict=False, context={"source_url": source_url}))
    return records


def _diagnose_field(scopes: list[Any], rule: FieldRule, *, source_url: str | None = None) -> FieldDiagnostic:
    match_count = 0
    missing_count = 0
    raw_sample = None
    transformed_sample = None
    errors: list[str] = []
    child_scope_pool: list[Any] = []
    for scope in scopes:
        try:
            raw_values = _raw_values(scope, rule)
            if raw_values:
                match_count += len(raw_values)
                if raw_sample is None:
                    raw_sample = raw_values[0]
            else:
                missing_count += 1
        except Exception as exc:
            missing_count += 1
            errors.append(str(exc))
        try:
            transformed = extract_value(scope, rule, strict=False, context={"source_url": source_url} if source_url else None)
            if transformed_sample is None and not _empty_for_quality(transformed):
                transformed_sample = transformed
        except ExtractorError as exc:
            errors.append(str(exc))
        if rule.children:
            try:
                child_scope_pool.extend(_select_child_scopes(scope, rule))
            except Exception as exc:
                errors.append(str(exc))
    warning = None
    if rule.required and missing_count:
        warning = f"required field {rule.name} missing in {missing_count} scope(s)"
    if errors and warning is None:
        warning = f"field {rule.name} has extraction errors"
    return FieldDiagnostic(
        field=rule.namespace or rule.name,
        selector_type=rule.type,
        selector=rule.json_path or rule.selector or rule.pattern,
        attr=rule.attribute,
        match_count=match_count,
        missing_count=missing_count,
        required=rule.required,
        raw_sample=raw_sample,
        transformed_sample=transformed_sample,
        errors=_unique_strings(errors),
        warning=warning,
        children=[_diagnose_field(child_scope_pool, child, source_url=source_url) for child in rule.children],
    )


def _raw_values(scope: Any, rule: FieldRule) -> list[Any]:
    if rule.children:
        return _select_child_scopes(scope, rule)
    if rule.type == "json_path":
        value = json_path(scope, rule.json_path or rule.selector or "")
        values = value if isinstance(value, list) else [value]
        return [item for item in values if not _is_missing(item)]
    if rule.type in {"css", "attribute", "attr"}:
        document = _as_document(scope)
        values = [_node_value(node, rule.attribute if rule.type in {"attribute", "attr"} or rule.attribute else None) for node in document.select(rule.selector)]
        return [value for value in values if not _is_missing(value)]
    if rule.type == "xpath":
        values = _as_document(scope).xpath(rule.selector or "")
        return [item.text if isinstance(item, Node) else str(item) for item in values if not _is_missing(item)]
    if rule.type == "regex":
        text = _scope_text(scope)
        if not rule.pattern:
            return []
        matches = list(re.finditer(rule.pattern, text, flags=re.S))
        return [match.group(1) if match.groups() else match.group(0) for match in matches]
    return []


def _selector_sample_value(item: Any) -> str:
    if isinstance(item, Node):
        return item.text or item.to_html()
    return str(item)


def _record_failure(report: DryRunReport, request: HttpRequest, response: HttpResponse | None, error: Exception, artifact_path: str) -> None:
    report.final_url = response.final_url if response is not None else None
    report.status_code = response.status_code if response is not None else None
    report.artifact_path = artifact_path
    report.errors.append(
        {
            "url": response.url if response is not None else request.url,
            "status_code": response.status_code if response is not None else None,
            "error_type": _error_type(error),
            "message": str(error),
            "artifact_path": artifact_path,
        }
    )


def _diagnostic_errors(diagnostics: list[FieldDiagnostic]) -> list[str]:
    errors: list[str] = []
    for diagnostic in diagnostics:
        errors.extend(diagnostic.errors)
        errors.extend(_diagnostic_errors(diagnostic.children))
    return _unique_strings(errors)


def _read_artifact_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"artifacts": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"artifacts": []}


def _error_type(error: Exception | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, FetchError):
        return error.error_type
    return error.__class__.__name__


def _empty_for_quality(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "debug"
