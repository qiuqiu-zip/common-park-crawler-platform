from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from .extractor import extract_fields, extract_records, json_path
from .crawl_policy import CrawlPolicyDecision, CrawlPolicyEngine
from .html_query import HTMLDocument
from .http_client import (
    FetchError,
    HttpFetcher,
    HttpRequest,
    HttpResponse,
    HttpStatusError,
    RequestContext,
    build_http_request,
    join_url,
    parse_response,
)
from .lifecycle import CancellationRequested, LifecycleSignal
from .models import DetailOptions, LifecycleEvent, RequestOptions, SpiderConfig, TaskRecord, TaskStatus
from .observability import (
    ensure_task_trace,
    log_event,
    record_task_metrics,
    record_task_report,
    safe_observe,
    start_trace,
    trace_event,
    trace_id_from_task,
)
from .playwright_runner import PlaywrightFetcher, resolve_playwright_options
from .request_governance import RequestPipeline, error_summary, normalized_error_type
from .storage import FileStore
from .url_seed import collect_request_urls


class Fetcher(Protocol):
    def fetch(self, request: HttpRequest) -> HttpResponse:
        ...


class CrawlerEngineError(RuntimeError):
    pass


@dataclass(slots=True)
class FetchResult:
    request: HttpRequest
    response: HttpResponse
    parsed: object


@dataclass(slots=True)
class PageRequestSpec:
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    page_index: int = 1
    page_role: str = "start"


@dataclass(slots=True)
class EngineRunResult:
    task: TaskRecord
    records: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IncrementalState:
    dedup_enabled: bool = False
    dedup_dataset: str = ""
    dedup_keys: list[str] = field(default_factory=list)
    hash_method: str = "sha256"
    dedup_scope: str = "global"
    hash_scope: str = "global"
    skip_existing: bool = True
    missing_key_policy: str = "error"
    include_source_url: bool = False
    watermark_enabled: bool = False
    watermark_dataset: str = ""
    watermark_field: str | None = None
    watermark_strategy: str = "max"
    watermark_type: str = "string"
    watermark_format: str | None = None
    stop_when_older: bool = False
    current_watermark_raw: Any = None
    current_watermark_value: Any = None
    watermark_candidate_raw: Any = None
    watermark_candidate_value: Any = None


@dataclass(slots=True)
class SaveRecordsResult:
    records: list[dict] = field(default_factory=list)
    stop_pagination: bool = False


class CrawlerEngine:
    def __init__(
        self,
        store: FileStore | None = None,
        fetcher: Fetcher | None = None,
        playwright_fetcher: Fetcher | None = None,
        sleep=None,
        clock=None,
    ) -> None:
        self.store = store or FileStore()
        self.fetcher = fetcher or HttpFetcher()
        self.playwright_fetcher = playwright_fetcher
        self._owned_playwright_fetcher: PlaywrightFetcher | None = None
        self.sleep = sleep
        self.clock = clock
        self._request_pipeline: RequestPipeline | None = None
        self._lifecycle_signal: LifecycleSignal | None = None
        self._crawl_policy: CrawlPolicyEngine | None = None

    def run(
        self,
        spider: SpiderConfig,
        task_id: str | None = None,
        *,
        lifecycle_signal: LifecycleSignal | None = None,
        trace_id: str | None = None,
        persist_spider: bool = True,
    ) -> TaskRecord:
        return self.run_with_result(
            spider,
            task_id=task_id,
            lifecycle_signal=lifecycle_signal,
            trace_id=trace_id,
            persist_spider=persist_spider,
        ).task

    def run_with_result(
        self,
        spider: SpiderConfig,
        task_id: str | None = None,
        *,
        resume: bool = False,
        lifecycle_signal: LifecycleSignal | None = None,
        trace_id: str | None = None,
        persist_spider: bool = True,
    ) -> EngineRunResult:
        checkpoint: dict[str, Any] | None = None
        resume_start_index = 0
        resume_next_spec: PageRequestSpec | None = None
        if resume:
            if not task_id:
                raise CrawlerEngineError("resume requires a task_id")
            checkpoint = self.store.load_checkpoint(task_id)
            if checkpoint.get("completed"):
                raise CrawlerEngineError(f"checkpoint for task {task_id} is already completed")
            task = self.store.load_task(task_id)
            if task.spider_id != spider.id:
                raise CrawlerEngineError(f"checkpoint spider_id {task.spider_id} does not match {spider.id}")
            task.resume_count += 1
            task.failed_requests = 0
            task.failed_count = 0
            task.error_type = None
            task.error_message = None
            resume_start_index = int(checkpoint.get("current_start_url_index", 0) or 0)
            next_url = checkpoint.get("next_url")
            if next_url:
                resume_next_spec = PageRequestSpec(
                    str(next_url),
                    params=dict(checkpoint.get("next_params", {})),
                    page_index=int(checkpoint.get("next_page_index", checkpoint.get("current_page", 0) + 1) or 1),
                    page_role=str(checkpoint.get("next_page_role") or "start"),
                )
        else:
            task = TaskRecord(id=task_id or uuid.uuid4().hex, spider_id=spider.id)
        selected_trace_id = ensure_task_trace(task, trace_id=trace_id)
        safe_observe(start_trace, self.store, selected_trace_id, metadata={"task_id": task.id, "spider_id": spider.id})
        if persist_spider:
            self.store.save_spider(spider)
        self._observe_log(
            spider,
            task,
            "INFO",
            "engine",
            "task_started",
            f"Task {task.id} started",
            metadata={"resume": resume},
        )
        self._transition(task, TaskStatus.RUNNING, started_at=_now(), finished_at=None)
        self._lifecycle_signal = lifecycle_signal
        if self._lifecycle_signal is not None:
            self._lifecycle_signal.bind_task(task.id)
        result = EngineRunResult(task=task)
        last_error: Exception | None = None
        hard_error = False
        incremental = self._prepare_incremental(spider, task)
        request_urls = self._request_urls(spider)
        self._request_pipeline = RequestPipeline(spider, task, sleep=self.sleep, clock=self.clock, store=self.store)
        self._crawl_policy = CrawlPolicyEngine(spider, clock=self.clock)
        cancellation: CancellationRequested | None = None

        try:
            self._check_lifecycle(task, "run_start")
            for start_index, start_url in enumerate(request_urls[resume_start_index:], start=resume_start_index):
                self._check_lifecycle(task, "start_url")
                next_spec: PageRequestSpec | None = resume_next_spec if start_index == resume_start_index and resume_next_spec else PageRequestSpec(start_url, page_role="start")
                resume_next_spec = None
                while next_spec is not None and not self._record_limit_reached(spider, task):
                    self._check_lifecycle(task, "page")
                    current_spec = self._apply_policy_to_spec(spider, task, next_spec, depth=0, kind="page")
                    if current_spec is None:
                        next_spec = None
                        self.store.save_task(task)
                        continue
                    task.total_requests += 1
                    try:
                        fetch_result = self._fetch_and_parse(spider, task, current_spec)
                        self._check_lifecycle(task, "after_fetch")
                        task.success_requests += 1
                        self._observe_trace(spider, task, "extract_started", url=fetch_result.response.url)
                        extracted_records = self._extract_response_records(spider, fetch_result)
                        self._observe_trace(spider, task, "extract_finished", url=fetch_result.response.url, metadata={"records": len(extracted_records)})
                        candidate_records = self._records_within_limit(spider, task, extracted_records)
                        detailed_records = self._follow_page_details(spider, task, fetch_result, candidate_records)
                        self._check_lifecycle(task, "record_batch")
                        records = detailed_records
                        self._count_records(task, records)
                        save_result = self._save_records(spider, task, fetch_result.response, records, incremental)
                        self._observe_trace(
                            spider,
                            task,
                            "result_saved",
                            url=fetch_result.response.url,
                            metadata={"saved_records": len(save_result.records), "skipped_records": task.skipped_records},
                        )
                        result.records.extend(save_result.records)
                        if save_result.stop_pagination:
                            next_spec = None
                        else:
                            next_spec = self._next_page_spec(spider, fetch_result, current_spec.page_index, len(extracted_records))
                        self._observe_trace(
                            spider,
                            task,
                            "pagination_next",
                            url=current_spec.url,
                            metadata={"next_url": next_spec.url if next_spec else None, "page_index": current_spec.page_index},
                        )
                        self._save_checkpoint(spider, task, start_index, current_spec, next_spec, incremental=incremental)
                    except CancellationRequested:
                        raise
                    except Exception as exc:
                        task.failed_requests += 1
                        task.failed_count += 1
                        last_error = exc
                        hard_error = hard_error or isinstance(exc, CrawlerEngineError)
                        warning = _task_warning(exc, current_spec.url)
                        task.warnings.append(warning)
                        result.warnings.append(warning)
                        self._observe_log(
                            spider,
                            task,
                            "WARNING",
                            "engine",
                            "request_failed",
                            f"Request failed for {current_spec.url}",
                            url=current_spec.url,
                            error_type=classify_error(exc),
                            metadata=warning,
                        )
                        next_spec = None
                        if spider.request.fail_fast:
                            break
                    self.store.save_task(task)
                if spider.request.fail_fast and task.failed_requests:
                    break
                if self._record_limit_reached(spider, task):
                    break
        except CancellationRequested as exc:
            cancellation = exc

        if self._request_pipeline is not None:
            task.request_governance = self._request_pipeline.snapshot()
        if self._crawl_policy is not None:
            task.request_governance["crawl_policy"] = self._crawl_policy.summary()
        if cancellation is not None:
            task.error_type = "cancelled"
            task.error_message = str(cancellation)
            task.warnings.append(
                {
                    "type": "cancelled",
                    "target_type": cancellation.target_type,
                    "target_id": cancellation.target_id,
                    "boundary": cancellation.boundary,
                    "reason": cancellation.reason,
                }
            )
            self._save_cancel_checkpoint(spider, task, incremental, cancellation)
            self._transition(task, TaskStatus.CANCELLED, finished_at=_now())
            self.store.clear_lifecycle_signal("task", task.id)
        elif task.success_requests == 0 or hard_error or (spider.request.fail_fast and task.failed_requests):
            task.error_type = classify_error(last_error) if last_error else "unknown"
            task.error_message = str(last_error) if last_error else "no requests were executed"
            self._transition(task, TaskStatus.FAILED, finished_at=_now())
        else:
            self._commit_watermark(spider, task, incremental)
            self._save_checkpoint(spider, task, len(request_urls) - 1, None, None, incremental=incremental, completed=True)
            self._transition(task, TaskStatus.SUCCESS, finished_at=_now())
        self._close_owned_playwright_fetcher()
        self._finalize_observability(spider, task)
        self._request_pipeline = None
        self._lifecycle_signal = None
        self._crawl_policy = None
        return result

    def resume_task(self, task_id: str) -> TaskRecord:
        task = self.store.load_task(task_id)
        spider = self.store.load_spider(task.spider_id)
        return self.run_with_result(spider, task_id=task_id, resume=True).task

    def cancel(self, task_id: str) -> TaskRecord:
        task = self.store.load_task(task_id)
        task.status = TaskStatus.CANCELLED
        task.finished_at = _now()
        self.store.save_task(task)
        return task

    def _request_urls(self, spider: SpiderConfig) -> list[str]:
        urls = collect_request_urls(spider)
        if not urls:
            raise CrawlerEngineError("spider has no start_urls, request.url, or seed")
        return urls

    def _apply_policy_to_spec(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        spec: PageRequestSpec,
        *,
        depth: int,
        kind: str,
    ) -> PageRequestSpec | None:
        decision = self._check_policy_url(spider, task, spec.url, depth=depth, kind=kind)
        if decision.blocked:
            return None
        if decision.normalized_url == spec.url:
            return spec
        return PageRequestSpec(decision.normalized_url, params=dict(spec.params), page_index=spec.page_index, page_role=spec.page_role)

    def _check_policy_url(self, spider: SpiderConfig, task: TaskRecord, url: str, *, depth: int, kind: str) -> CrawlPolicyDecision:
        policy = self._crawl_policy or CrawlPolicyEngine(spider, clock=self.clock)
        self._crawl_policy = policy
        decision = policy.check_url(url, depth=depth, kind=kind)
        if not policy.enabled:
            return decision
        if decision.warnings or decision.violations:
            warning = {
                "type": "crawl_policy",
                "kind": kind,
                "url": decision.input_url,
                "normalized_url": decision.normalized_url,
                "blocked": decision.blocked,
                "warnings": list(decision.warnings),
                "violations": list(decision.violations),
                "robots": decision.robots,
            }
            task.warnings.append(warning)
            self._observe_log(
                spider,
                task,
                "WARNING",
                "crawl_policy",
                "policy_decision",
                f"Crawl policy {'blocked' if decision.blocked else 'warned'} {decision.normalized_url}",
                url=decision.normalized_url,
                metadata=warning,
            )
        return decision

    def _fetch_and_parse(self, spider: SpiderConfig, task: TaskRecord, request_spec: PageRequestSpec) -> FetchResult:
        response_type = self._response_type(spider)
        request_id = uuid.uuid4().hex
        options = self._request_options_for_page(spider, request_spec)
        request = self._build_request(
            spider,
            task,
            request_spec.url,
            options,
            response_type=response_type,
            page_role=request_spec.page_role,
            override_source=self._playwright_override_source(request_spec.page_role, options),
        )
        self._observe_trace(spider, task, "request_built", request_id=request_id, url=request.url, metadata={"response_type": response_type})
        execution = self._execute_request(spider, task, request, response_type, request_id=request_id)
        return FetchResult(request=execution.request, response=execution.response, parsed=execution.parsed)

    def _extract_response_records(self, spider: SpiderConfig, fetch_result: FetchResult) -> list[dict]:
        if fetch_result.request.response_type == "binary":
            return []
        content = fetch_result.parsed if isinstance(fetch_result.parsed, str) else json.dumps(fetch_result.parsed, ensure_ascii=False)
        return extract_records(content, spider, context={"source_url": fetch_result.response.final_url or fetch_result.response.url})

    def _follow_page_details(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        fetch_result: FetchResult,
        records: list[dict],
    ) -> list[dict]:
        detail = spider.detail
        if not detail.enabled or not records:
            return records
        selector_urls = _selector_detail_urls(fetch_result, detail, len(records), item_selector=spider.item_selector)
        followed: list[dict] = []
        for index, record in enumerate(records):
            self._check_lifecycle(task, "detail")
            followed.append(
                self._follow_detail_config(
                    spider,
                    task,
                    base_record=record,
                    source_response=fetch_result.response,
                    detail=detail,
                    selector_urls=selector_urls[index] if index < len(selector_urls) else [],
                    depth=1,
                    max_depth=detail.max_depth,
                    visited=set(),
                )
            )
        return followed

    def _follow_detail_config(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        *,
        base_record: dict,
        source_response: HttpResponse,
        detail: DetailOptions,
        selector_urls: list[str],
        depth: int,
        max_depth: int,
        visited: set[str],
    ) -> dict:
        if not detail.enabled or depth > max_depth:
            return base_record
        urls = _record_detail_urls(base_record, source_response, detail, selector_urls)
        if not urls:
            return base_record

        detail_records: list[dict] = []
        for url in urls:
            self._check_lifecycle(task, "detail_url")
            decision = self._check_policy_url(spider, task, url, depth=depth, kind="detail_url")
            if decision.blocked:
                continue
            url = decision.normalized_url
            if url in visited:
                continue
            visited.add(url)
            self._observe_trace(spider, task, "detail_started", url=url)
            detail_fetch_result = self._fetch_detail(spider, task, detail, url)
            if detail_fetch_result is None:
                self._observe_trace(spider, task, "detail_finished", url=url, error_type="detail_fetch_failed")
                continue
            extracted = _extract_detail_records(detail, detail_fetch_result)
            if depth < max_depth and detail.details:
                extracted = [
                    self._follow_nested_details(spider, task, detail_fetch_result, detail_record, detail, depth, max_depth, visited)
                    for detail_record in extracted
                ]
            detail_records.extend(extracted)
            self._observe_trace(spider, task, "detail_finished", url=url, metadata={"records": len(extracted)})
        return _merge_detail_records(base_record, detail_records, detail)

    def _follow_nested_details(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        detail_fetch_result: FetchResult,
        detail_record: dict,
        parent_detail: DetailOptions,
        depth: int,
        max_depth: int,
        visited: set[str],
    ) -> dict:
        merged = detail_record
        for child_detail in parent_detail.details:
            selector_urls = _selector_detail_urls(detail_fetch_result, child_detail, 1, item_selector=None)
            merged = self._follow_detail_config(
                spider,
                task,
                base_record=merged,
                source_response=detail_fetch_result.response,
                detail=child_detail,
                selector_urls=selector_urls[0] if selector_urls else [],
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
            )
        return merged

    def _fetch_detail(self, spider: SpiderConfig, task: TaskRecord, detail: DetailOptions, url: str) -> FetchResult | None:
        task.total_requests += 1
        response_type = detail.request.response_type or ("json" if spider.type == "api" else "html")
        request_id = uuid.uuid4().hex
        request = self._build_request(
            spider,
            task,
            url,
            detail.request,
            response_type=response_type,
            page_role="detail",
            override_source=self._playwright_override_source("detail", detail.request),
        )
        try:
            self._check_lifecycle(task, "detail_fetch")
            self._observe_trace(spider, task, "request_built", request_id=request_id, url=request.url, metadata={"response_type": response_type, "detail": True})
            execution = self._execute_request(spider, task, request, response_type, request_id=request_id)
            task.success_requests += 1
            return FetchResult(request=execution.request, response=execution.response, parsed=execution.parsed)
        except CancellationRequested:
            raise
        except Exception as exc:
            if spider.request.fail_fast:
                raise
            task.failed_requests += 1
            task.failed_count += 1
            warning = _task_warning(exc, url)
            task.warnings.append(warning)
            return None

    def _request_options_for_page(self, spider: SpiderConfig, request_spec: PageRequestSpec) -> RequestOptions:
        options = replace(spider.request, params={**spider.request.params, **request_spec.params})
        if request_spec.page_role != "pagination":
            return options
        return replace(
            options,
            playwright=spider.pagination.request.playwright
            if spider.pagination.request.playwright is not None
            else options.playwright,
        )

    def _build_request(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        url: str,
        options: RequestOptions,
        *,
        response_type: str,
        page_role: str,
        override_source: str,
    ) -> HttpRequest:
        effective_playwright = None
        strategy_source = None
        if _uses_playwright(spider):
            effective_playwright, strategy_source = resolve_playwright_options(
                spider.playwright,
                options.playwright,
                page_role=page_role,
                override_source=override_source,
            )
        return build_http_request(
            url,
            options,
            response_type=response_type,
            context=RequestContext(
                spider_id=spider.id,
                task_id=task.id,
                start_url=url,
                response_type=response_type,
                page_role=page_role,
            ),
            playwright_options=effective_playwright,
            playwright_strategy_source=strategy_source,
        )

    def _playwright_override_source(self, page_role: str, options: RequestOptions) -> str:
        if options.playwright is None:
            return "spider.playwright"
        if page_role == "pagination":
            return "pagination.request.playwright"
        if page_role == "detail":
            return "detail.request.playwright"
        if page_role == "debug":
            return "request.playwright.debug"
        return "request.playwright"

    def _records_within_limit(self, spider: SpiderConfig, task: TaskRecord, records: list[dict]) -> list[dict]:
        if spider.pagination.max_records is None:
            return records
        remaining = spider.pagination.max_records - task.total_records
        if remaining <= 0:
            return []
        return records[:remaining]

    def _count_records(self, task: TaskRecord, records: list[dict]) -> None:
        task.total_seen += len(records)
        task.total_records += len(records)

    def _record_limit_reached(self, spider: SpiderConfig, task: TaskRecord) -> bool:
        return spider.pagination.max_records is not None and task.total_records >= spider.pagination.max_records

    def _next_page_spec(
        self,
        spider: SpiderConfig,
        fetch_result: FetchResult,
        page_index: int,
        extracted_count: int,
    ) -> PageRequestSpec | None:
        pagination = spider.pagination
        if pagination.type == "none" or page_index >= pagination.max_pages:
            return None

        explicit_index = page_index - 1
        if explicit_index < len(pagination.urls):
            return PageRequestSpec(pagination.urls[explicit_index], page_index=page_index + 1, page_role="pagination")

        if extracted_count == 0:
            return None
        if pagination.type == "url_list":
            return None
        if pagination.type == "page":
            param = pagination.page_param or "page"
            current_page = _request_int(fetch_result.request, param) or page_index
            return PageRequestSpec(
                _without_query_params(fetch_result.request.url, {param}),
                params={param: current_page + 1},
                page_index=page_index + 1,
                page_role="pagination",
            )
        if pagination.type == "offset":
            param = pagination.offset_param or "offset"
            current_offset = _request_int(fetch_result.request, param) or 0
            page_size = pagination.page_size
            if page_size is None and pagination.page_size_param:
                page_size = _request_int(fetch_result.request, pagination.page_size_param)
            if page_size is None:
                page_size = extracted_count
            if not page_size:
                return None
            params: dict[str, Any] = {param: current_offset + page_size}
            remove_params = {param}
            if pagination.page_size_param and pagination.page_size is not None:
                params[pagination.page_size_param] = pagination.page_size
                remove_params.add(pagination.page_size_param)
            return PageRequestSpec(
                _without_query_params(fetch_result.request.url, remove_params),
                params=params,
                page_index=page_index + 1,
                page_role="pagination",
            )
        if pagination.type == "next_button":
            next_url = _next_html_url(fetch_result, pagination.next_selector, pagination.next_attribute)
            return PageRequestSpec(next_url, page_index=page_index + 1, page_role="pagination") if next_url else None
        if pagination.type == "cursor":
            next_url = _next_json_url(fetch_result, pagination.next_json_path)
            if next_url:
                return PageRequestSpec(next_url, page_index=page_index + 1, page_role="pagination")
            cursor = _cursor_value(fetch_result, pagination.cursor_json_path)
            if cursor is None or cursor == "":
                return None
            param = pagination.page_param or "cursor"
            return PageRequestSpec(
                _without_query_params(fetch_result.request.url, {param}),
                params={param: cursor},
                page_index=page_index + 1,
                page_role="pagination",
            )
        return None

    def _save_records(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        response: HttpResponse,
        records: list[dict],
        incremental: IncrementalState,
    ) -> SaveRecordsResult:
        fetched_at = _now()
        saved: list[dict] = []
        stop_pagination = False
        for record in records:
            self._check_lifecycle(task, "record")
            data = dict(record)
            source_url = response.final_url or response.url
            dedup_meta: dict[str, Any] | None = None
            unique_hash: str | None = None
            if incremental.dedup_enabled:
                dedup_decision = self._dedup_decision(spider, task, data, source_url, incremental)
                if dedup_decision == "skip":
                    continue
                unique_hash, is_duplicate = dedup_decision
                dedup_meta = {
                    "hash": unique_hash,
                    "dataset": incremental.dedup_dataset,
                    "scope": incremental.dedup_scope,
                    "is_duplicate": is_duplicate,
                    "keys": list(incremental.dedup_keys),
                }

            if incremental.watermark_enabled:
                watermark_decision = self._watermark_decision(task, data, incremental)
                if watermark_decision == "skip":
                    task.skipped_by_watermark += 1
                    task.skipped_records += 1
                    if incremental.stop_when_older:
                        stop_pagination = True
                    continue
                if watermark_decision == "stop":
                    task.skipped_by_watermark += 1
                    task.skipped_records += 1
                    stop_pagination = True
                    continue

            enriched = {
                **data,
                "source_url": source_url,
                "fetched_at": fetched_at,
                "response_status": response.status_code,
                "spider_id": spider.id,
                "task_id": task.id,
            }
            if unique_hash is not None:
                enriched["unique_hash"] = unique_hash
            if dedup_meta is not None:
                enriched["_dedup"] = dedup_meta
            self.store.append_record(task.id, enriched)
            if unique_hash is not None:
                self.store.add_hash(incremental.dedup_dataset, unique_hash, scope=incremental.hash_scope)
            task.saved_count += 1
            task.saved_records += 1
            saved.append(enriched)
        return SaveRecordsResult(records=saved, stop_pagination=stop_pagination)

    def _prepare_incremental(self, spider: SpiderConfig, task: TaskRecord) -> IncrementalState:
        dedup = spider.dedup or {}
        dedup_enabled = bool(dedup.get("enabled")) if "enabled" in dedup else bool(spider.unique_fields or dedup.get("keys") or dedup.get("fields"))
        dedup_dataset = str(dedup.get("dataset") or spider.id)
        dedup_scope = str(dedup.get("scope", "global"))
        state = IncrementalState(
            dedup_enabled=dedup_enabled,
            dedup_dataset=dedup_dataset,
            dedup_keys=list(dedup.get("keys") or dedup.get("fields") or spider.unique_fields),
            hash_method=str(dedup.get("hash_method", "sha256")),
            dedup_scope=dedup_scope,
            hash_scope=_hash_scope_key(dedup_scope, spider, task),
            skip_existing=bool(dedup.get("skip_existing", True)),
            missing_key_policy=str(dedup.get("missing_key_policy", "error")),
            include_source_url=bool(dedup.get("include_source_url", False)),
        )
        watermark = spider.watermark or {}
        state.watermark_enabled = bool(watermark.get("enabled", False))
        state.watermark_dataset = str(watermark.get("dataset") or dedup.get("dataset") or spider.id)
        state.watermark_field = watermark.get("field")
        state.watermark_strategy = str(watermark.get("strategy", "max"))
        state.watermark_type = str(watermark.get("type", "string"))
        state.watermark_format = watermark.get("format")
        state.stop_when_older = bool(watermark.get("stop_when_older", False))
        if state.watermark_enabled:
            payload = self.store.get_watermark(spider.id, state.watermark_dataset)
            if payload is not None:
                state.current_watermark_raw = payload.get("value")
                state.current_watermark_value = _parse_watermark_value(state.current_watermark_raw, state)
        return state

    def _dedup_decision(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        record: dict[str, Any],
        source_url: str,
        incremental: IncrementalState,
    ) -> tuple[str, bool] | str:
        values: dict[str, Any] = {}
        missing: list[str] = []
        for key in incremental.dedup_keys:
            value = _value_at_path(record, key, missing)
            values[key] = value
        if incremental.include_source_url:
            values["source_url"] = source_url
        if missing:
            message = f"missing dedup key(s) {', '.join(missing)} for spider {spider.id}"
            if incremental.missing_key_policy == "error":
                raise CrawlerEngineError(message)
            if incremental.missing_key_policy == "skip":
                task.skipped_records += 1
                return "skip"
            if incremental.missing_key_policy == "warn":
                task.warnings.append(
                    {
                        "type": "dedup_missing_key",
                        "error_type": "field_quality",
                        "message": message,
                        "spider_id": spider.id,
                        "source_url": source_url,
                        "missing_keys": list(missing),
                        "dedup_keys": list(incremental.dedup_keys),
                    }
                )
        unique_hash = build_unique_hash(values, hash_method=incremental.hash_method)
        is_duplicate = self.store.has_hash(incremental.dedup_dataset, unique_hash, scope=incremental.hash_scope)
        if is_duplicate:
            task.duplicate_records += 1
            if incremental.skip_existing:
                task.skipped_duplicates += 1
                task.skipped_records += 1
                return "skip"
        return unique_hash, is_duplicate

    def _watermark_decision(self, task: TaskRecord, record: dict[str, Any], incremental: IncrementalState) -> str:
        if not incremental.watermark_field:
            return "keep"
        missing: list[str] = []
        raw_value = _value_at_path(record, incremental.watermark_field, missing)
        if missing:
            warning = f"missing watermark field {incremental.watermark_field}"
            task.warnings.append(warning)
            return "keep"
        try:
            value = _parse_watermark_value(raw_value, incremental)
        except ValueError as exc:
            task.warnings.append(str(exc))
            return "keep"
        current = incremental.current_watermark_value
        if current is not None and not _watermark_is_new(value, current, incremental.watermark_strategy):
            return "stop" if incremental.stop_when_older else "skip"
        candidate = incremental.watermark_candidate_value
        if candidate is None or _watermark_is_new(value, candidate, incremental.watermark_strategy):
            incremental.watermark_candidate_value = value
            incremental.watermark_candidate_raw = raw_value
        return "keep"

    def _commit_watermark(self, spider: SpiderConfig, task: TaskRecord, incremental: IncrementalState) -> None:
        if not incremental.watermark_enabled or incremental.watermark_candidate_value is None:
            return
        current = incremental.current_watermark_value
        if current is not None and not _watermark_is_new(incremental.watermark_candidate_value, current, incremental.watermark_strategy):
            return
        self.store.update_watermark(
            spider.id,
            incremental.watermark_dataset,
            incremental.watermark_candidate_raw,
            metadata={
                "task_id": task.id,
                "field": incremental.watermark_field,
                "strategy": incremental.watermark_strategy,
                "type": incremental.watermark_type,
            },
        )
        task.watermark_updates += 1
        self._observe_trace(
            spider,
            task,
            "watermark_updated",
            metadata={"dataset": incremental.watermark_dataset, "field": incremental.watermark_field, "value": incremental.watermark_candidate_raw},
        )

    def _save_checkpoint(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        start_url_index: int,
        current_spec: PageRequestSpec | None,
        next_spec: PageRequestSpec | None,
        *,
        incremental: IncrementalState,
        completed: bool = False,
    ) -> None:
        state = {
            "task_id": task.id,
            "spider_id": spider.id,
            "current_start_url_index": start_url_index,
            "current_page": current_spec.page_index if current_spec else None,
            "next_url": next_spec.url if next_spec else None,
            "next_params": next_spec.params if next_spec else {},
            "next_page_index": next_spec.page_index if next_spec else None,
            "next_page_role": next_spec.page_role if next_spec else None,
            "offset": _checkpoint_offset(spider, next_spec),
            "cursor": _checkpoint_cursor(spider, next_spec),
            "visited_urls": [],
            "saved_hashes": [],
            "watermark_candidate": incremental.watermark_candidate_raw,
            "completed": completed,
        }
        self.store.save_checkpoint(task.id, state)
        task.checkpoint_saves += 1
        self._observe_trace(
            spider,
            task,
            "checkpoint_saved",
            metadata={"completed": completed, "next_url": state["next_url"], "current_page": state["current_page"]},
        )

    def _save_cancel_checkpoint(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        incremental: IncrementalState,
        cancellation: CancellationRequested,
    ) -> None:
        try:
            self.store.load_checkpoint(task.id)
            return
        except FileNotFoundError:
            pass
        state = {
            "task_id": task.id,
            "spider_id": spider.id,
            "current_start_url_index": 0,
            "current_page": None,
            "next_url": None,
            "next_params": {},
            "next_page_index": None,
            "offset": None,
            "cursor": None,
            "visited_urls": [],
            "saved_hashes": [],
            "watermark_candidate": incremental.watermark_candidate_raw,
            "completed": False,
            "cancelled": True,
            "cancel_boundary": cancellation.boundary,
        }
        self.store.save_checkpoint(task.id, state)
        task.checkpoint_saves += 1

    def _response_type(self, spider: SpiderConfig) -> str:
        if spider.request.response_type:
            return spider.request.response_type
        return "json" if spider.type == "api" else "html"

    def _fetch_request(self, spider: SpiderConfig, request: HttpRequest) -> HttpResponse:
        if _uses_playwright(spider):
            return self._playwright_fetcher(spider).fetch(request)
        return self.fetcher.fetch(request)

    def _execute_request(self, spider: SpiderConfig, task: TaskRecord, request: HttpRequest, response_type: str, *, request_id: str | None = None):
        pipeline = self._request_pipeline
        if pipeline is None:
            pipeline = RequestPipeline(spider, task, sleep=self.sleep, clock=self.clock, store=self.store)
            self._request_pipeline = pipeline

        def observed_fetch(governed: HttpRequest) -> HttpResponse:
            started = time.perf_counter()
            self._observe_trace(spider, task, "fetch_started", request_id=request_id, url=governed.url, metadata={"attempt": governed.context.attempt if governed.context else None})
            try:
                response = self._fetch_request(spider, governed)
                duration = (time.perf_counter() - started) * 1000
                self._observe_log(
                    spider,
                    task,
                    "INFO",
                    "request",
                    "fetch_finished",
                    f"Fetched {governed.url}",
                    request_id=request_id,
                    url=governed.url,
                    status_code=response.status_code,
                    duration_ms=duration,
                    metadata={"response_metadata": response.metadata if spider.observability.capture_response_metadata else {}},
                )
                return response
            except Exception as exc:
                duration = (time.perf_counter() - started) * 1000
                self._observe_log(
                    spider,
                    task,
                    "ERROR",
                    "request",
                    "fetch_failed",
                    f"Fetch failed for {governed.url}",
                    request_id=request_id,
                    url=governed.url,
                    duration_ms=duration,
                    error_type=classify_error(exc),
                    metadata={"error": str(exc)},
                )
                self._observe_trace(spider, task, "fetch_finished", request_id=request_id, url=governed.url, duration_ms=duration, error_type=classify_error(exc))
                raise

        def observed_parse(response: HttpResponse):
            started = time.perf_counter()
            self._observe_trace(spider, task, "parse_started", request_id=request_id, url=response.url, status_code=response.status_code)
            try:
                parsed = parse_response(response, response_type)
                duration = (time.perf_counter() - started) * 1000
                self._observe_trace(spider, task, "parse_finished", request_id=request_id, url=response.url, status_code=response.status_code, duration_ms=duration)
                return parsed
            except Exception as exc:
                duration = (time.perf_counter() - started) * 1000
                self._observe_log(
                    spider,
                    task,
                    "ERROR",
                    "request",
                    "parse_failed",
                    f"Parse failed for {response.url}",
                    request_id=request_id,
                    url=response.url,
                    status_code=response.status_code,
                    duration_ms=duration,
                    error_type=classify_error(exc),
                    metadata={"error": str(exc), "response_type": response_type},
                )
                self._observe_trace(spider, task, "parse_finished", request_id=request_id, url=response.url, status_code=response.status_code, duration_ms=duration, error_type=classify_error(exc))
                raise

        return pipeline.execute(request, observed_fetch, observed_parse)

    def _playwright_fetcher(self, spider: SpiderConfig) -> Fetcher:
        if self.playwright_fetcher is not None:
            return self.playwright_fetcher
        if self._owned_playwright_fetcher is None:
            self._owned_playwright_fetcher = PlaywrightFetcher(spider.playwright)
        return self._owned_playwright_fetcher

    def _close_owned_playwright_fetcher(self) -> None:
        if self._owned_playwright_fetcher is not None:
            self._owned_playwright_fetcher.close()
            self._owned_playwright_fetcher = None

    def _observe_log(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        level: str,
        component: str,
        event_type: str,
        message: str,
        *,
        request_id: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        duration_ms: float | None = None,
        error_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_observe(
            log_event,
            self.store,
            spider,
            level=level,
            component=component,
            event_type=event_type,
            message=message,
            trace_id=trace_id_from_task(task),
            task_id=task.id,
            spider_id=spider.id,
            request_id=request_id,
            url=url,
            status_code=status_code,
            duration_ms=duration_ms,
            error_type=error_type,
            metadata=metadata or {},
        )

    def _observe_trace(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        event_type: str,
        *,
        request_id: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        duration_ms: float | None = None,
        error_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        trace_event(
            self.store,
            spider,
            trace_id_from_task(task),
            event_type,
            task_id=task.id,
            spider_id=spider.id,
            request_id=request_id,
            url=url,
            status_code=status_code,
            duration_ms=duration_ms,
            error_type=error_type,
            metadata=metadata or {},
        )

    def _finalize_observability(self, spider: SpiderConfig, task: TaskRecord) -> None:
        self._observe_log(
            spider,
            task,
            "INFO" if task.status == TaskStatus.SUCCESS else "ERROR" if task.status == TaskStatus.FAILED else "WARNING",
            "engine",
            "task_finished",
            f"Task {task.id} finished with {task.status.value}",
            error_type=task.error_type,
            metadata={"warnings": task.warnings, "request_governance": task.request_governance},
        )
        safe_observe(record_task_metrics, self.store, spider, task)
        safe_observe(record_task_report, self.store, spider, task)

    def _transition(self, task: TaskRecord, status: TaskStatus, **updates) -> None:
        from_status = task.status
        task.status = status
        for key, value in updates.items():
            setattr(task, key, value)
        self.store.save_task(task)
        self.store.record_lifecycle_event(
            LifecycleEvent(
                event_id=uuid.uuid4().hex,
                target_type="task",
                target_id=task.id,
                event_type=_task_event_type(from_status, status),
                from_status=from_status.value,
                to_status=status.value,
                created_at=_now(),
                metadata={},
            )
        )

    def _check_lifecycle(self, task: TaskRecord, boundary: str) -> None:
        if self._lifecycle_signal is not None:
            self._lifecycle_signal.check(boundary=boundary)


def build_unique_hash(record: dict, fields: list[str] | None = None, *, hash_method: str = "sha256") -> str:
    if fields:
        missing: list[str] = []
        payload = {field: _value_at_path(record, field, missing) for field in fields}
    else:
        payload = record
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if hash_method == "md5":
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_error(exc: Exception | None) -> str:
    error_type = normalized_error_type(exc)
    if error_type != "unknown":
        return error_type
    if isinstance(exc, CrawlerEngineError):
        return "engine"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "parse"
    return "unknown"


def _task_warning(exc: Exception, url: str) -> dict[str, Any]:
    request = HttpRequest(method="GET", url=url)
    return error_summary(exc, request=request, attempt=getattr(exc, "attempt", None) or 1, proxy=getattr(exc, "proxy", None), retryable=getattr(exc, "retryable", None))


def _value_at_path(record: dict[str, Any], path: str, missing: list[str]) -> Any:
    current: Any = record
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                pass
        missing.append(path)
        return None
    return current


def _hash_scope_key(scope: str, spider: SpiderConfig, task: TaskRecord) -> str:
    if scope == "spider":
        return f"spider-{spider.id}"
    if scope == "task":
        return f"task-{task.id}"
    return "global"


def _parse_watermark_value(value: Any, incremental: IncrementalState) -> Any:
    value_type = incremental.watermark_type
    if value is None:
        raise ValueError(f"watermark field {incremental.watermark_field} is empty")
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "datetime":
        if isinstance(value, datetime):
            return value
        text = str(value)
        if incremental.watermark_format:
            return datetime.strptime(text, incremental.watermark_format)
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    return str(value)


def _watermark_is_new(value: Any, current: Any, strategy: str) -> bool:
    if strategy == "min":
        return value < current
    return value > current


def _uses_playwright(spider: SpiderConfig) -> bool:
    return spider.type == "playwright" or spider.playwright.enabled


def _task_event_type(from_status: TaskStatus, to_status: TaskStatus) -> str:
    if to_status == TaskStatus.RUNNING and from_status != TaskStatus.RUNNING:
        return "started"
    if to_status == TaskStatus.SUCCESS:
        return "completed"
    if to_status == TaskStatus.FAILED:
        return "failed"
    if to_status == TaskStatus.CANCELLED:
        return "cancelled"
    if to_status == TaskStatus.CANCELLING:
        return "cancel_requested"
    if to_status == TaskStatus.PAUSED:
        return "paused"
    if to_status == TaskStatus.PENDING and from_status == TaskStatus.PAUSED:
        return "resumed"
    return "status_changed"


def _checkpoint_offset(spider: SpiderConfig, next_spec: PageRequestSpec | None) -> Any:
    if next_spec is None:
        return None
    param = spider.pagination.offset_param or "offset"
    return next_spec.params.get(param)


def _checkpoint_cursor(spider: SpiderConfig, next_spec: PageRequestSpec | None) -> Any:
    if next_spec is None:
        return None
    param = spider.pagination.page_param or "cursor"
    return next_spec.params.get(param)


def _request_int(request: HttpRequest, name: str | None) -> int | None:
    value = _request_value(request, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _request_value(request: HttpRequest, name: str | None) -> Any:
    if not name:
        return None
    if name in request.params:
        return request.params[name]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.url).query, keep_blank_values=True))
    return query.get(name)


def _without_query_params(url: str, names: set[str]) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if key not in names]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query, doseq=True), parts.fragment))


def _next_html_url(fetch_result: FetchResult, selector: str | None, attribute: str) -> str | None:
    if not selector:
        return None
    nodes = HTMLDocument(fetch_result.response.text).select(selector)
    if not nodes:
        return None
    value = nodes[0].attrs.get(attribute) if attribute else nodes[0].text
    base = fetch_result.response.final_url or fetch_result.response.url
    return join_url(base, value)


def _next_json_url(fetch_result: FetchResult, path: str | None) -> str | None:
    if not path:
        return None
    value = json_path(fetch_result.parsed, path)
    if value is None or value == "":
        return None
    base = fetch_result.response.final_url or fetch_result.response.url
    return join_url(base, str(value))


def _cursor_value(fetch_result: FetchResult, path: str | None) -> Any:
    if not path:
        return None
    return json_path(fetch_result.parsed, path)


def _selector_detail_urls(
    fetch_result: FetchResult,
    detail: DetailOptions,
    count: int,
    *,
    item_selector: str | None,
) -> list[list[str]]:
    selector = detail.url_selector or detail.link_selector
    if not selector or count <= 0 or fetch_result.request.response_type not in {"html", "text"}:
        return [[] for _ in range(count)]
    document = HTMLDocument(fetch_result.response.text)
    scopes = document.select(item_selector) if item_selector else [document.root]
    per_record: list[list[str]] = []
    for scope in scopes[:count]:
        nodes = HTMLDocument(scope).select(selector)
        urls = [node.attrs.get(detail.url_attr or detail.link_attribute) if detail.url_attr or detail.link_attribute else node.text for node in nodes]
        per_record.append(_join_unique_urls(fetch_result.response, urls))
    while len(per_record) < count:
        per_record.append([])
    return per_record


def _record_detail_urls(record: dict, source_response: HttpResponse, detail: DetailOptions, selector_urls: list[str]) -> list[str]:
    values: list[Any] = list(selector_urls)
    if detail.url_field:
        value = record.get(detail.url_field)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return _join_unique_urls(source_response, values)


def _join_unique_urls(source_response: HttpResponse, values: list[Any]) -> list[str]:
    base = source_response.final_url or source_response.url
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        url = join_url(base, str(value))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _extract_detail_records(detail: DetailOptions, fetch_result: FetchResult) -> list[dict]:
    if fetch_result.request.response_type == "binary":
        return []
    if not detail.fields:
        return [{}]
    if fetch_result.request.response_type == "json":
        scope = fetch_result.parsed
    else:
        scope = HTMLDocument(fetch_result.response.text).root
    return [extract_fields(scope, detail.fields, context={"source_url": fetch_result.response.final_url or fetch_result.response.url})]


def _merge_detail_records(base_record: dict, detail_records: list[dict], detail: DetailOptions) -> dict:
    if not detail_records:
        return base_record
    strategy = detail.merge_strategy
    if strategy == "keep_list":
        namespace = detail.namespace or "details"
        merged = dict(base_record)
        existing = merged.get(namespace)
        if existing is None:
            values: list[dict] = []
        elif isinstance(existing, list):
            values = list(existing)
        else:
            values = [existing]
        merged[namespace] = values + detail_records
        return merged
    if strategy == "namespace":
        namespace = detail.namespace or "detail"
        merged = dict(base_record)
        merged[namespace] = detail_records[0] if len(detail_records) == 1 else detail_records
        return merged

    merged = dict(base_record)
    for detail_record in detail_records:
        merged.update(detail_record)
    return merged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
