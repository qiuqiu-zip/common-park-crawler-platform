from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import DEFAULT_TRACKING_PARAMS, FieldRule, SpiderConfig
from .url_seed import collect_seed_urls

SUPPORTED_VERSIONS = ["0.1", "1.0"]
SUPPORTED_MODES = ["http", "api", "playwright"]
SUPPORTED_FIELD_TYPES = {"css", "xpath", "regex", "attribute", "attr", "json_path"}
SUPPORTED_PAGINATION_TYPES = ["none", "page", "offset", "url_list", "next_button", "cursor"]
SUPPORTED_EXPORT_FORMATS = ["json", "jsonl", "csv", "xlsx"]
SUPPORTED_EXPORT_NESTED_STRATEGIES = ["flatten_dot", "flatten_underscore", "json_string"]
SUPPORTED_EXPORT_LIST_STRATEGIES = ["json_string", "join"]
SUPPORTED_SCHEDULER_TYPES = ["manual", "cron", "interval"]
SUPPORTED_MISFIRE_POLICIES = ["skip", "run_once", "catch_up"]
SUPPORTED_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
SUPPORTED_RESPONSE_TYPES = ["html", "json", "text", "binary"]
SUPPORTED_DETAIL_MERGE_STRATEGIES = ["override", "namespace", "keep_list"]
SUPPORTED_DEDUP_HASH_METHODS = ["sha256", "md5"]
SUPPORTED_DEDUP_SCOPES = ["global", "spider", "task"]
SUPPORTED_MISSING_KEY_POLICIES = ["error", "warn", "skip", "allow_empty"]
SUPPORTED_WATERMARK_STRATEGIES = ["max", "min"]
SUPPORTED_WATERMARK_TYPES = ["string", "int", "float", "datetime"]
SUPPORTED_RETRY_BACKOFF = ["fixed", "exponential", "none"]
SUPPORTED_RETRY_ERRORS = ["network", "timeout", "http_status", "parse", "render", "proxy", "rate_limit"]
SUPPORTED_PROXY_MODES = ["round_robin", "random", "sticky"]
SUPPORTED_REFERER_POLICIES = ["none", "previous_url", "origin"]
SUPPORTED_AUTH_CHECK_TYPES = ["status_code", "body_contains", "body_not_contains", "json_path", "header_exists"]
SUPPORTED_SESSION_FLOW_STEPS = ["request", "extract", "set_cookie", "set_header", "save_session"]
SUPPORTED_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
SUPPORTED_ROBOTS_MODES = ["warn", "block"]
SUPPORTED_ROBOTS_UNAVAILABLE_POLICIES = ["warn", "block", "ignore"]
SUPPORTED_SCROLL_MODES = ["none", "viewport", "incremental", "bottom"]


@dataclass(slots=True)
class ValidationIssue:
    path: str
    message: str


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [{"path": issue.path, "message": issue.message} for issue in self.issues],
        }


class SpiderConfigValidationError(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in result.issues))


def validate_spider_config(config: dict[str, Any] | SpiderConfig) -> ValidationResult:
    data = config.to_dict() if isinstance(config, SpiderConfig) else _normalize_legacy(config)
    issues: list[ValidationIssue] = []

    _require_string(data, "id", issues)
    _require_string(data, "name", issues, required=False)
    version = str(data.get("version", "1.0"))
    if version not in SUPPORTED_VERSIONS:
        issues.append(ValidationIssue("version", f"unsupported version: {version}"))
    spider_type = data.get("type", "http")
    if spider_type not in SUPPORTED_MODES:
        issues.append(ValidationIssue("type", f"must be one of {SUPPORTED_MODES}"))
    start_urls = data.get("start_urls")
    request = data.get("request")
    request_url = request.get("url") if isinstance(request, dict) else None
    has_source = False

    if start_urls is not None:
        if not isinstance(start_urls, list) or not start_urls:
            issues.append(ValidationIssue("start_urls", "must be a non-empty list"))
        elif not all(isinstance(url, str) and url.strip() for url in start_urls):
            issues.append(ValidationIssue("start_urls", "all urls must be non-empty strings"))
        else:
            has_source = True

    if request_url is not None:
        if not isinstance(request_url, str) or not request_url.strip():
            issues.append(ValidationIssue("request.url", "must be a non-empty string"))
        else:
            has_source = True

    if data.get("seed") is not None:
        if _has_seed_url(data, issues):
            has_source = True
    if not has_source:
        issues.append(
            ValidationIssue(
                "start_urls | request.url | seed",
                "one of start_urls, request.url, or seed must be provided and non-empty",
            )
        )

    if spider_type == "api" and not data.get("items_json_path"):
        issues.append(ValidationIssue("items_json_path", "api type requires items_json_path"))
    if spider_type in {"http", "playwright"} and not (data.get("item_selector") or data.get("fields")):
        issues.append(ValidationIssue("fields", "http/playwright type requires fields or item_selector"))

    fields = data.get("fields", [])
    if not isinstance(fields, list) or not fields:
        issues.append(ValidationIssue("fields", "must be a non-empty list"))
    else:
        for index, field in enumerate(fields):
            _validate_field(field, f"fields[{index}]", issues)

    detail = data.get("detail", {})
    if detail is not None and not isinstance(detail, dict):
        issues.append(ValidationIssue("detail", "must be an object"))
        detail = {}
    if detail.get("enabled"):
        _validate_detail(detail, "detail", issues)

    request = data.get("request", {})
    _validate_request(request, "request", issues)

    pagination = data.get("pagination", {})
    pagination_type = pagination.get("type", "none")
    if pagination_type not in SUPPORTED_PAGINATION_TYPES:
        issues.append(ValidationIssue("pagination.type", f"must be one of {SUPPORTED_PAGINATION_TYPES}"))
    _positive_int(pagination.get("max_pages", 1), "pagination.max_pages", issues)
    page_size = pagination.get("page_size")
    if page_size is not None:
        _positive_int(page_size, "pagination.page_size", issues)
    max_records = pagination.get("max_records")
    if max_records is not None:
        _positive_int(max_records, "pagination.max_records", issues)

    playwright = data.get("playwright", {})
    if playwright is not None and not isinstance(playwright, dict):
        issues.append(ValidationIssue("playwright", "must be an object"))
        playwright = {}
    pool_size = playwright.get("browser_pool_size", playwright.get("pool_size", 1))
    _positive_int(pool_size, "playwright.browser_pool_size", issues)
    if "headless" in playwright and not isinstance(playwright["headless"], bool):
        issues.append(ValidationIssue("playwright.headless", "must be a boolean"))
    if playwright.get("wait_until", "networkidle") not in {"load", "domcontentloaded", "networkidle"}:
        issues.append(ValidationIssue("playwright.wait_until", "must be load, domcontentloaded, or networkidle"))
    wait_selector = playwright.get("wait_for_selector")
    if wait_selector is not None and (not isinstance(wait_selector, str) or not wait_selector.strip()):
        issues.append(ValidationIssue("playwright.wait_for_selector", "must be a non-empty string when provided"))
    wait_selector_timeout_ms = playwright.get("wait_for_selector_timeout_ms")
    if wait_selector_timeout_ms is not None:
        _positive_int(wait_selector_timeout_ms, "playwright.wait_for_selector_timeout_ms", issues)
    post_load_wait_ms = playwright.get("post_load_wait_ms")
    if post_load_wait_ms is not None:
        _non_negative_int(post_load_wait_ms, "playwright.post_load_wait_ms", issues)
    scroll_strategy = playwright.get("scroll_strategy", {})
    if scroll_strategy is not None and not isinstance(scroll_strategy, dict):
        issues.append(ValidationIssue("playwright.scroll_strategy", "must be an object"))
        scroll_strategy = {}
    if "enabled" in scroll_strategy and not isinstance(scroll_strategy["enabled"], bool):
        issues.append(ValidationIssue("playwright.scroll_strategy.enabled", "must be a boolean"))
    if scroll_strategy.get("mode", "none") not in SUPPORTED_SCROLL_MODES:
        issues.append(ValidationIssue("playwright.scroll_strategy.mode", f"must be one of {SUPPORTED_SCROLL_MODES}"))
    if scroll_strategy.get("max_scrolls") is not None:
        _positive_int(scroll_strategy.get("max_scrolls"), "playwright.scroll_strategy.max_scrolls", issues)
    if scroll_strategy.get("scroll_pause_ms") is not None:
        _non_negative_int(scroll_strategy.get("scroll_pause_ms"), "playwright.scroll_strategy.scroll_pause_ms", issues)
    stop_selector = scroll_strategy.get("stop_selector")
    if stop_selector is not None and (not isinstance(stop_selector, str) or not stop_selector.strip()):
        issues.append(ValidationIssue("playwright.scroll_strategy.stop_selector", "must be a non-empty string when provided"))

    _validate_scheduler(data.get("scheduler", {}), issues)

    _validate_dedup(data.get("dedup", {}), data.get("unique_fields", []), issues)
    _validate_watermark(data.get("watermark", {}), issues)
    _validate_retry(data.get("retry", {}), issues)
    _validate_proxy(data.get("proxy", {}), issues)
    _validate_anti_bot(data.get("anti_bot", {}), issues)
    _validate_crawl_policy(data.get("crawl_policy"), issues)
    _validate_session(data.get("session", {}), issues)
    _validate_observability(data.get("observability", {}), issues)
    _validate_rate_limit(data.get("rate_limit", {}), issues)
    _validate_concurrency(data.get("concurrency", {}), issues)

    _validate_export(data.get("export", {}), issues)

    return ValidationResult(valid=not issues, issues=issues)


def ensure_valid_spider_config(config: dict[str, Any] | SpiderConfig) -> SpiderConfig:
    data = config.to_dict() if isinstance(config, SpiderConfig) else _normalize_legacy(config)
    result = validate_spider_config(data)
    if not result.valid:
        raise SpiderConfigValidationError(result)
    return config if isinstance(config, SpiderConfig) else SpiderConfig.from_dict(data)


def dump_spider_config(config: SpiderConfig, path: str | Path | None = None) -> str:
    text = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def spider_config_json_schema() -> dict[str, Any]:
    def field_rule_schema(depth: int = 3) -> dict[str, Any]:
        child_items = field_rule_schema(depth - 1) if depth > 0 else {"type": "object"}
        return {
            "type": "object",
            "required": ["name", "type"],
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": sorted(SUPPORTED_FIELD_TYPES)},
                "selector": {"type": ["string", "null"]},
                "pattern": {"type": ["string", "null"]},
                "attribute": {"type": ["string", "null"]},
                "json_path": {"type": ["string", "null"]},
                "default": {},
                "many": {"type": "boolean", "default": False},
                "required": {"type": "boolean", "default": False},
                "transforms": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "value": {},
                                    "format": {"type": "string"},
                                    "pattern": {"type": "string"},
                                    "replacement": {"type": "string"},
                                },
                                "additionalProperties": True,
                            },
                        ]
                    },
                },
                "join_with": {"type": ["string", "null"]},
                "children": {"type": "array", "items": child_items},
                "namespace": {"type": ["string", "null"]},
                "override": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    field_rule = field_rule_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://common-park.local/schemas/spider_config.schema.json",
        "title": "Crawler Platform Spider Config",
        "type": "object",
        "required": ["id", "fields"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "version": {"type": "string", "enum": SUPPORTED_VERSIONS, "default": "1.0"},
            "description": {"type": ["string", "null"]},
            "type": {"type": "string", "enum": SUPPORTED_MODES, "default": "http"},
            "enabled": {"type": "boolean", "default": True},
            "start_urls": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "seed": {
                "type": ["array", "object"],
                "description": "Alternative seed source when start_urls is absent.",
            },
            "item_selector": {"type": ["string", "null"]},
            "items_json_path": {"type": ["string", "null"]},
            "fields": {"type": "array", "minItems": 1, "items": field_rule},
            "unique_fields": {"type": "array", "items": {"type": "string"}},
            "request": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": SUPPORTED_HTTP_METHODS, "default": "GET"},
                    "url": {"type": ["string", "null"]},
                    "params": {"type": "object", "additionalProperties": True},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "cookies": {"type": "object", "additionalProperties": {"type": "string"}},
                    "user_agent": {"type": ["string", "null"]},
                    "body": {"type": ["string", "null"]},
                    "json": {},
                    "delay_seconds": {"type": "number", "minimum": 0, "default": 0},
                    "proxy": {"type": ["string", "null"]},
                    "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "default": 20},
                    "max_retries": {"type": "integer", "minimum": 0, "default": 2},
                    "encoding": {"type": ["string", "null"]},
                    "response_type": {"type": ["string", "null"], "enum": [*SUPPORTED_RESPONSE_TYPES, None]},
                    "follow_redirects": {"type": "boolean", "default": True},
                    "fail_fast": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            "pagination": {
                "type": "object",
                "properties": {
                    "next_selector": {"type": ["string", "null"]},
                    "type": {"type": "string", "enum": SUPPORTED_PAGINATION_TYPES, "default": "none"},
                    "next_attribute": {"type": "string", "default": "href"},
                    "next_json_path": {"type": ["string", "null"]},
                    "page_param": {"type": ["string", "null"]},
                    "offset_param": {"type": ["string", "null"]},
                    "page_size_param": {"type": ["string", "null"]},
                    "page_size": {"type": ["integer", "null"], "minimum": 1},
                    "cursor_json_path": {"type": ["string", "null"]},
                    "urls": {"type": "array", "items": {"type": "string"}},
                    "max_pages": {"type": "integer", "minimum": 1, "default": 1},
                    "max_records": {"type": ["integer", "null"], "minimum": 1},
                },
                "additionalProperties": False,
            },
            "detail": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "url_field": {"type": ["string", "null"]},
                    "url_selector": {"type": ["string", "null"]},
                    "url_attr": {"type": "string", "default": "href"},
                    "link_selector": {"type": ["string", "null"]},
                    "link_attribute": {"type": "string", "default": "href"},
                    "request": {
                        "type": "object",
                        "properties": {
                            "method": {"type": "string", "enum": SUPPORTED_HTTP_METHODS, "default": "GET"},
                            "url": {"type": ["string", "null"]},
                            "params": {"type": "object", "additionalProperties": True},
                            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                            "cookies": {"type": "object", "additionalProperties": {"type": "string"}},
                            "user_agent": {"type": ["string", "null"]},
                            "body": {"type": ["string", "null"]},
                            "json": {},
                            "delay_seconds": {"type": "number", "minimum": 0, "default": 0},
                            "proxy": {"type": ["string", "null"]},
                            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "default": 20},
                            "max_retries": {"type": "integer", "minimum": 0, "default": 2},
                            "encoding": {"type": ["string", "null"]},
                            "response_type": {"type": ["string", "null"], "enum": [*SUPPORTED_RESPONSE_TYPES, None]},
                            "follow_redirects": {"type": "boolean", "default": True},
                            "fail_fast": {"type": "boolean", "default": False},
                        },
                        "additionalProperties": False,
                    },
                    "fields": {"type": "array", "items": field_rule},
                    "merge_strategy": {"type": "string", "enum": SUPPORTED_DETAIL_MERGE_STRATEGIES, "default": "override"},
                    "namespace": {"type": ["string", "null"]},
                    "max_depth": {"type": "integer", "minimum": 1, "default": 1},
                    "details": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": False,
            },
            "playwright": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "browser_pool_size": {"type": "integer", "minimum": 1, "default": 1},
                    "pool_size": {"type": "integer", "minimum": 1},
                    "headless": {"type": "boolean", "default": True},
                    "wait_until": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "default": "networkidle",
                    },
                    "wait_for_selector": {"type": ["string", "null"]},
                    "wait_for_selector_timeout_ms": {"type": "integer", "minimum": 1, "default": 10000},
                    "post_load_wait_ms": {"type": "integer", "minimum": 0, "default": 0},
                    "scroll_strategy": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": False},
                            "mode": {"type": "string", "enum": SUPPORTED_SCROLL_MODES, "default": "none"},
                            "max_scrolls": {"type": "integer", "minimum": 1, "default": 3},
                            "scroll_pause_ms": {"type": "integer", "minimum": 0, "default": 800},
                            "stop_selector": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "dedup": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "dataset": {"type": ["string", "null"]},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "hash_method": {"type": "string", "enum": SUPPORTED_DEDUP_HASH_METHODS, "default": "sha256"},
                    "scope": {"type": "string", "enum": SUPPORTED_DEDUP_SCOPES, "default": "global"},
                    "skip_existing": {"type": "boolean", "default": True},
                    "missing_key_policy": {
                        "type": "string",
                        "enum": SUPPORTED_MISSING_KEY_POLICIES,
                        "default": "error",
                    },
                    "include_source_url": {"type": "boolean", "default": False},
                    "strategy": {"type": ["string", "null"]},
                },
                "additionalProperties": True,
            },
            "watermark": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "dataset": {"type": ["string", "null"]},
                    "field": {"type": ["string", "null"]},
                    "strategy": {"type": "string", "enum": SUPPORTED_WATERMARK_STRATEGIES, "default": "max"},
                    "type": {"type": "string", "enum": SUPPORTED_WATERMARK_TYPES, "default": "string"},
                    "format": {"type": ["string", "null"]},
                    "stop_when_older": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            "retry": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "max_attempts": {"type": "integer", "minimum": 1, "default": 1},
                    "backoff": {"type": "string", "enum": SUPPORTED_RETRY_BACKOFF, "default": "none"},
                    "backoff_base_seconds": {"type": "number", "minimum": 0, "default": 0},
                    "backoff_max_seconds": {"type": "number", "minimum": 0, "default": 30},
                    "retry_on_status": {"type": "array", "items": {"type": "integer"}},
                    "retry_on_errors": {"type": "array", "items": {"type": "string", "enum": SUPPORTED_RETRY_ERRORS}},
                    "jitter": {"type": "boolean", "default": False},
                    "fail_after_attempts": {"type": "boolean", "default": True},
                    "random_seed": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
            "anti_bot": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "user_agents": {"type": "array", "items": {"type": "string"}},
                    "headers_pool": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "random_delay": {"type": "boolean", "default": False},
                    "min_delay_seconds": {"type": "number", "minimum": 0, "default": 0},
                    "max_delay_seconds": {"type": "number", "minimum": 0, "default": 0},
                    "respect_robots_txt": {"type": "boolean", "default": False},
                    "cookie_file": {"type": ["string", "null"]},
                    "cookies": {"type": "object", "additionalProperties": {"type": "string"}},
                    "referer_policy": {"type": "string", "enum": SUPPORTED_REFERER_POLICIES, "default": "none"},
                    "random_seed": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
            "crawl_policy": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "user_agent": {"type": ["string", "null"]},
                    "robots": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": True},
                            "mode": {"type": "string", "enum": SUPPORTED_ROBOTS_MODES, "default": "warn"},
                            "unavailable_policy": {
                                "type": "string",
                                "enum": SUPPORTED_ROBOTS_UNAVAILABLE_POLICIES,
                                "default": "warn",
                            },
                            "rules": {"type": "object", "additionalProperties": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                    "allowed_domains": {"type": "array", "items": {"type": "string"}},
                    "denied_domains": {"type": "array", "items": {"type": "string"}},
                    "allow_cross_domain": {"type": "boolean", "default": False},
                    "include_url_patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude_url_patterns": {"type": "array", "items": {"type": "string"}},
                    "normalize_url": {"type": "boolean", "default": True},
                    "remove_fragment": {"type": "boolean", "default": True},
                    "remove_tracking_params": {"type": "boolean", "default": True},
                    "tracking_params": {"type": "array", "items": {"type": "string"}, "default": DEFAULT_TRACKING_PARAMS},
                    "max_requests": {"type": "integer", "minimum": 1, "default": 100},
                    "max_depth": {"type": "integer", "minimum": 0, "default": 3},
                    "max_duration_seconds": {"type": "integer", "minimum": 1, "default": 300},
                },
                "additionalProperties": False,
            },
            "session": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "profile": {"type": ["string", "null"]},
                    "cookie_file": {"type": ["string", "null"]},
                    "storage_state": {"type": ["string", "null"]},
                    "persist": {"type": "boolean", "default": True},
                    "load_before_request": {"type": "boolean", "default": True},
                    "save_after_request": {"type": "boolean", "default": True},
                    "account_ref": {"type": ["string", "null"]},
                    "auth_check": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": False},
                            "type": {"type": "string", "enum": SUPPORTED_AUTH_CHECK_TYPES, "default": "status_code"},
                            "expected_status": {"type": "integer", "default": 200},
                            "body_contains": {"type": ["string", "null"]},
                            "body_not_contains": {"type": ["string", "null"]},
                            "json_path": {"type": ["string", "null"]},
                            "header": {"type": ["string", "null"]},
                            "expected_value": {},
                        },
                        "additionalProperties": False,
                    },
                    "login_flow": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": False},
                            "steps": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        },
                        "additionalProperties": False,
                    },
                    "refresh_flow": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean", "default": False},
                            "steps": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "proxy": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "mode": {"type": "string", "enum": SUPPORTED_PROXY_MODES, "default": "round_robin"},
                    "proxies": {"type": "array", "items": {"type": "string"}},
                    "proxy_file": {"type": ["string", "null"]},
                    "fail_threshold": {"type": "integer", "minimum": 1, "default": 2},
                    "cooldown_seconds": {"type": "number", "minimum": 0, "default": 60},
                    "sticky_key": {"type": ["string", "null"]},
                    "health_check_url": {"type": ["string", "null"]},
                    "random_seed": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
            "observability": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "log_level": {"type": "string", "enum": SUPPORTED_LOG_LEVELS, "default": "INFO"},
                    "structured_logs": {"type": "boolean", "default": True},
                    "capture_request_timeline": {"type": "boolean", "default": True},
                    "capture_response_metadata": {"type": "boolean", "default": True},
                    "capture_record_samples": {"type": "boolean", "default": True},
                    "record_sample_limit": {"type": "integer", "minimum": 0, "default": 10},
                    "redact_sensitive": {"type": "boolean", "default": True},
                    "metrics_enabled": {"type": "boolean", "default": True},
                    "run_report_enabled": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            "rate_limit": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "requests_per_second": {"type": "number", "minimum": 0, "default": 0},
                    "per_domain": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            "concurrency": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "max_concurrent_requests": {"type": "integer", "minimum": 1, "default": 1},
                    "per_domain": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            "export": {
                "type": "object",
                "properties": {
                    "formats": {"type": "array", "items": {"type": "string", "enum": SUPPORTED_EXPORT_FORMATS}},
                    "default_format": {"type": "string", "enum": SUPPORTED_EXPORT_FORMATS, "default": "jsonl"},
                    "format": {"type": "string", "enum": SUPPORTED_EXPORT_FORMATS},
                    "output_dir": {"type": ["string", "null"]},
                    "enabled": {"type": "boolean", "default": True},
                    "include_fields": {"type": "array", "items": {"type": "string"}},
                    "exclude_fields": {"type": "array", "items": {"type": "string"}},
                    "field_aliases": {"type": "object", "additionalProperties": {"type": "string"}},
                    "include_metadata": {"type": "boolean", "default": False},
                    "include_dedup": {"type": "boolean", "default": True},
                    "include_dedup_metadata": {"type": "boolean"},
                    "redact_sensitive": {"type": "boolean", "default": True},
                    "nested_strategy": {"type": "string", "enum": SUPPORTED_EXPORT_NESTED_STRATEGIES, "default": "flatten_dot"},
                    "flatten": {"type": "string", "enum": SUPPORTED_EXPORT_NESTED_STRATEGIES},
                    "list_strategy": {"type": "string", "enum": SUPPORTED_EXPORT_LIST_STRATEGIES, "default": "json_string"},
                    "join_separator": {"type": "string", "default": ","},
                    "include_observability": {"type": "boolean", "default": False},
                    "include_lifecycle": {"type": "boolean", "default": False},
                    "manifest_enabled": {"type": "boolean", "default": True},
                },
                "additionalProperties": True,
            },
            "metadata": {"type": "object"},
            "scheduler": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": False},
                    "type": {"type": "string", "enum": SUPPORTED_SCHEDULER_TYPES, "default": "manual"},
                    "interval_seconds": {"type": ["integer", "null"], "minimum": 1},
                    "cron": {"type": ["string", "null"]},
                    "timezone": {"type": "string", "default": "UTC"},
                    "start_at": {"type": ["string", "null"]},
                    "end_at": {"type": ["string", "null"]},
                    "misfire_policy": {"type": "string", "enum": SUPPORTED_MISFIRE_POLICIES, "default": "skip"},
                    "max_instances": {"type": "integer", "minimum": 1, "default": 1},
                    "max_concurrency": {"type": "integer", "minimum": 1},
                    "jitter_seconds": {"type": "integer", "minimum": 0, "default": 0},
                },
                "additionalProperties": False,
            },
        },
        "oneOf": [
            {"required": ["start_urls"]},
            {
                "properties": {
                    "request": {
                        "type": "object",
                        "required": ["url"],
                        "properties": {"url": {"type": "string", "minLength": 1}},
                    },
                },
                "required": ["request"],
            },
            {"required": ["seed"]},
        ],
        "additionalProperties": False,
    }


def write_spider_config_schema(path: str | Path = "docs/spider_config.schema.json") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(spider_config_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _normalize_legacy(data: dict[str, Any]) -> dict[str, Any]:
    normalized = data.copy()
    if "version" not in normalized and "schema_version" in normalized:
        normalized["version"] = str(normalized["schema_version"])
    if "type" not in normalized and "mode" in normalized:
        normalized["type"] = normalized["mode"]
    if "scheduler" not in normalized and "schedule" in normalized:
        normalized["scheduler"] = normalized["schedule"]
    normalized.setdefault("version", "1.0")
    normalized.setdefault("type", "http")
    return normalized


def _has_seed_url(data: dict[str, Any], issues: list[ValidationIssue]) -> bool:
    seed = data.get("seed")
    try:
        return bool(collect_seed_urls(seed))
    except ValueError as exc:
        issues.append(ValidationIssue("seed", str(exc)))
        return False


def _validate_field(field: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(field, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return
    _require_string(field, "name", issues, path)
    field_type = field.get("type")
    if field_type not in SUPPORTED_FIELD_TYPES:
        issues.append(ValidationIssue(f"{path}.type", f"must be one of {sorted(SUPPORTED_FIELD_TYPES)}"))
        return
    if field_type in {"css", "attribute", "attr", "xpath"} and not field.get("selector"):
        issues.append(ValidationIssue(f"{path}.selector", f"{field_type} field requires selector"))
    if field_type in {"attribute", "attr"} and not field.get("attribute"):
        issues.append(ValidationIssue(f"{path}.attribute", f"{field_type} field requires attribute"))
    if field_type == "regex":
        pattern = field.get("pattern")
        if not pattern:
            issues.append(ValidationIssue(f"{path}.pattern", "regex field requires pattern"))
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                issues.append(ValidationIssue(f"{path}.pattern", f"invalid regex: {exc}"))
    if field_type == "json_path" and not (field.get("json_path") or field.get("selector")):
        issues.append(ValidationIssue(f"{path}.json_path", "json_path field requires json_path or selector"))
    transforms = field.get("transforms", [])
    if transforms is not None and not isinstance(transforms, list):
        issues.append(ValidationIssue(f"{path}.transforms", "must be a list"))
    children = field.get("children", [])
    if children is not None:
        if not isinstance(children, list):
            issues.append(ValidationIssue(f"{path}.children", "must be a list"))
        else:
            for index, child in enumerate(children):
                _validate_field(child, f"{path}.children[{index}]", issues)


def _validate_export(export: Any, issues: list[ValidationIssue]) -> None:
    if export is None:
        return
    if not isinstance(export, dict):
        issues.append(ValidationIssue("export", "must be an object"))
        return
    formats = export.get("formats", [])
    if formats:
        if not isinstance(formats, list) or not all(item in SUPPORTED_EXPORT_FORMATS for item in formats):
            issues.append(ValidationIssue("export.formats", f"must contain only {SUPPORTED_EXPORT_FORMATS}"))
    normalized_formats = list(formats) if isinstance(formats, list) and formats else SUPPORTED_EXPORT_FORMATS
    default_format = export.get("default_format", export.get("format", "jsonl"))
    if default_format not in SUPPORTED_EXPORT_FORMATS:
        issues.append(ValidationIssue("export.default_format", f"must be one of {SUPPORTED_EXPORT_FORMATS}"))
    elif default_format not in normalized_formats:
        issues.append(ValidationIssue("export.default_format", "must be included in export.formats"))
    nested_strategy = export.get("nested_strategy", export.get("flatten", "flatten_dot"))
    if nested_strategy not in SUPPORTED_EXPORT_NESTED_STRATEGIES:
        issues.append(ValidationIssue("export.nested_strategy", f"must be one of {SUPPORTED_EXPORT_NESTED_STRATEGIES}"))
    list_strategy = export.get("list_strategy", "json_string")
    if list_strategy not in SUPPORTED_EXPORT_LIST_STRATEGIES:
        issues.append(ValidationIssue("export.list_strategy", f"must be one of {SUPPORTED_EXPORT_LIST_STRATEGIES}"))
    for key in ("enabled", "include_metadata", "include_dedup", "include_dedup_metadata", "redact_sensitive", "include_observability", "include_lifecycle", "manifest_enabled"):
        _optional_bool(export, key, f"export.{key}", issues)
    for key in ("include_fields", "exclude_fields"):
        value = export.get(key, [])
        if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value)):
            issues.append(ValidationIssue(f"export.{key}", "must be a list of non-empty strings"))
    aliases = export.get("field_aliases", {})
    if aliases is not None and (
        not isinstance(aliases, dict)
        or not all(isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip() for key, value in aliases.items())
    ):
        issues.append(ValidationIssue("export.field_aliases", "must be an object with non-empty string keys and values"))
    output_dir = export.get("output_dir")
    if output_dir is not None and (not isinstance(output_dir, str) or not output_dir.strip()):
        issues.append(ValidationIssue("export.output_dir", "must be a non-empty string"))
    join_separator = export.get("join_separator", ",")
    if not isinstance(join_separator, str):
        issues.append(ValidationIssue("export.join_separator", "must be a string"))


def _validate_dedup(dedup: Any, unique_fields: Any, issues: list[ValidationIssue]) -> None:
    if dedup is None:
        return
    if not isinstance(dedup, dict):
        issues.append(ValidationIssue("dedup", "must be an object"))
        return
    enabled = dedup.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        issues.append(ValidationIssue("dedup.enabled", "must be a boolean"))
    keys = dedup.get("keys", dedup.get("fields", unique_fields or []))
    if keys is not None and (not isinstance(keys, list) or not all(isinstance(item, str) and item.strip() for item in keys)):
        issues.append(ValidationIssue("dedup.keys", "must be a list of non-empty strings"))
    if dedup.get("enabled") is True and not keys:
        issues.append(ValidationIssue("dedup.keys", "enabled dedup requires keys or unique_fields"))
    dataset = dedup.get("dataset")
    if dataset is not None and (not isinstance(dataset, str) or not dataset.strip()):
        issues.append(ValidationIssue("dedup.dataset", "must be a non-empty string"))
    hash_method = dedup.get("hash_method", "sha256")
    if hash_method not in SUPPORTED_DEDUP_HASH_METHODS:
        issues.append(ValidationIssue("dedup.hash_method", f"must be one of {SUPPORTED_DEDUP_HASH_METHODS}"))
    scope = dedup.get("scope", "global")
    if scope not in SUPPORTED_DEDUP_SCOPES:
        issues.append(ValidationIssue("dedup.scope", f"must be one of {SUPPORTED_DEDUP_SCOPES}"))
    missing_key_policy = dedup.get("missing_key_policy", "error")
    if missing_key_policy not in SUPPORTED_MISSING_KEY_POLICIES:
        issues.append(ValidationIssue("dedup.missing_key_policy", f"must be one of {SUPPORTED_MISSING_KEY_POLICIES}"))
    for key in ("skip_existing", "include_source_url"):
        if key in dedup and not isinstance(dedup[key], bool):
            issues.append(ValidationIssue(f"dedup.{key}", "must be a boolean"))


def _validate_watermark(watermark: Any, issues: list[ValidationIssue]) -> None:
    if watermark is None:
        return
    if not isinstance(watermark, dict):
        issues.append(ValidationIssue("watermark", "must be an object"))
        return
    enabled = watermark.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        issues.append(ValidationIssue("watermark.enabled", "must be a boolean"))
    if watermark.get("enabled") is True:
        _require_string(watermark, "field", issues, "watermark")
    dataset = watermark.get("dataset")
    if dataset is not None and (not isinstance(dataset, str) or not dataset.strip()):
        issues.append(ValidationIssue("watermark.dataset", "must be a non-empty string"))
    strategy = watermark.get("strategy", "max")
    if strategy not in SUPPORTED_WATERMARK_STRATEGIES:
        issues.append(ValidationIssue("watermark.strategy", f"must be one of {SUPPORTED_WATERMARK_STRATEGIES}"))
    value_type = watermark.get("type", "string")
    if value_type not in SUPPORTED_WATERMARK_TYPES:
        issues.append(ValidationIssue("watermark.type", f"must be one of {SUPPORTED_WATERMARK_TYPES}"))
    if "stop_when_older" in watermark and not isinstance(watermark["stop_when_older"], bool):
            issues.append(ValidationIssue("watermark.stop_when_older", "must be a boolean"))


def _validate_scheduler(scheduler: Any, issues: list[ValidationIssue]) -> None:
    if scheduler is None:
        return
    if not isinstance(scheduler, dict):
        issues.append(ValidationIssue("scheduler", "must be an object"))
        return
    _optional_bool(scheduler, "enabled", "scheduler.enabled", issues)
    scheduler_type = scheduler.get("type", "manual")
    if scheduler_type not in SUPPORTED_SCHEDULER_TYPES:
        issues.append(ValidationIssue("scheduler.type", f"must be one of {SUPPORTED_SCHEDULER_TYPES}"))
    interval = scheduler.get("interval_seconds")
    if scheduler_type == "interval" and interval is None:
        issues.append(ValidationIssue("scheduler.interval_seconds", "interval scheduler requires interval_seconds"))
    if interval is not None:
        _positive_int(interval, "scheduler.interval_seconds", issues)
    cron = scheduler.get("cron")
    if scheduler_type == "cron" and (not isinstance(cron, str) or len(cron.split()) != 5):
        issues.append(ValidationIssue("scheduler.cron", "cron scheduler requires a 5-field expression"))
    if cron is not None and (not isinstance(cron, str) or not cron.strip()):
        issues.append(ValidationIssue("scheduler.cron", "must be a non-empty string"))
    timezone = scheduler.get("timezone", "UTC")
    if not isinstance(timezone, str) or not timezone.strip():
        issues.append(ValidationIssue("scheduler.timezone", "must be a non-empty string"))
    misfire_policy = scheduler.get("misfire_policy", "skip")
    if misfire_policy not in SUPPORTED_MISFIRE_POLICIES:
        issues.append(ValidationIssue("scheduler.misfire_policy", f"must be one of {SUPPORTED_MISFIRE_POLICIES}"))
    max_instances = scheduler.get("max_instances", scheduler.get("max_concurrency", 1))
    _positive_int(max_instances, "scheduler.max_instances", issues)
    if "max_concurrency" in scheduler:
        _positive_int(scheduler.get("max_concurrency"), "scheduler.max_concurrency", issues)
    jitter = scheduler.get("jitter_seconds", 0)
    _non_negative_int(jitter, "scheduler.jitter_seconds", issues)
    for key in ("start_at", "end_at"):
        value = scheduler.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            issues.append(ValidationIssue(f"scheduler.{key}", "must be an ISO datetime string"))


def _validate_retry(retry: Any, issues: list[ValidationIssue]) -> None:
    if retry is None:
        return
    if not isinstance(retry, dict):
        issues.append(ValidationIssue("retry", "must be an object"))
        return
    _optional_bool(retry, "enabled", "retry.enabled", issues)
    _positive_int(retry.get("max_attempts", 1), "retry.max_attempts", issues)
    backoff = retry.get("backoff", "none")
    if backoff not in SUPPORTED_RETRY_BACKOFF:
        issues.append(ValidationIssue("retry.backoff", f"must be one of {SUPPORTED_RETRY_BACKOFF}"))
    _non_negative_number(retry.get("backoff_base_seconds", 0), "retry.backoff_base_seconds", issues)
    _non_negative_number(retry.get("backoff_max_seconds", 30), "retry.backoff_max_seconds", issues)
    statuses = retry.get("retry_on_status", [])
    if statuses is not None and (not isinstance(statuses, list) or not all(isinstance(item, int) for item in statuses)):
        issues.append(ValidationIssue("retry.retry_on_status", "must be a list of integers"))
    errors = retry.get("retry_on_errors", [])
    if errors is not None and (not isinstance(errors, list) or not all(item in SUPPORTED_RETRY_ERRORS for item in errors)):
        issues.append(ValidationIssue("retry.retry_on_errors", f"must contain only {SUPPORTED_RETRY_ERRORS}"))
    _optional_bool(retry, "jitter", "retry.jitter", issues)
    _optional_bool(retry, "fail_after_attempts", "retry.fail_after_attempts", issues)
    seed = retry.get("random_seed")
    if seed is not None and not isinstance(seed, int):
        issues.append(ValidationIssue("retry.random_seed", "must be an integer"))


def _validate_proxy(proxy: Any, issues: list[ValidationIssue]) -> None:
    if proxy is None:
        return
    if not isinstance(proxy, dict):
        issues.append(ValidationIssue("proxy", "must be an object"))
        return
    _optional_bool(proxy, "enabled", "proxy.enabled", issues)
    mode = proxy.get("mode", "round_robin")
    if mode not in SUPPORTED_PROXY_MODES:
        issues.append(ValidationIssue("proxy.mode", f"must be one of {SUPPORTED_PROXY_MODES}"))
    proxies = proxy.get("proxies", [])
    if proxies is not None and (not isinstance(proxies, list) or not all(isinstance(item, str) and item.strip() for item in proxies)):
        issues.append(ValidationIssue("proxy.proxies", "must be a list of non-empty strings"))
    proxy_file = proxy.get("proxy_file")
    if proxy_file is not None and (not isinstance(proxy_file, str) or not proxy_file.strip()):
        issues.append(ValidationIssue("proxy.proxy_file", "must be a non-empty string"))
    _positive_int(proxy.get("fail_threshold", 2), "proxy.fail_threshold", issues)
    _non_negative_number(proxy.get("cooldown_seconds", 60), "proxy.cooldown_seconds", issues)
    seed = proxy.get("random_seed")
    if seed is not None and not isinstance(seed, int):
        issues.append(ValidationIssue("proxy.random_seed", "must be an integer"))


def _validate_anti_bot(anti_bot: Any, issues: list[ValidationIssue]) -> None:
    if anti_bot is None:
        return
    if not isinstance(anti_bot, dict):
        issues.append(ValidationIssue("anti_bot", "must be an object"))
        return
    _optional_bool(anti_bot, "enabled", "anti_bot.enabled", issues)
    user_agents = anti_bot.get("user_agents", [])
    if user_agents is not None and (not isinstance(user_agents, list) or not all(isinstance(item, str) and item.strip() for item in user_agents)):
        issues.append(ValidationIssue("anti_bot.user_agents", "must be a list of non-empty strings"))
    headers_pool = anti_bot.get("headers_pool", [])
    if headers_pool is not None and (
        not isinstance(headers_pool, list)
        or not all(isinstance(item, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in item.items()) for item in headers_pool)
    ):
        issues.append(ValidationIssue("anti_bot.headers_pool", "must be a list of string header objects"))
    _optional_bool(anti_bot, "random_delay", "anti_bot.random_delay", issues)
    _non_negative_number(anti_bot.get("min_delay_seconds", 0), "anti_bot.min_delay_seconds", issues)
    _non_negative_number(anti_bot.get("max_delay_seconds", 0), "anti_bot.max_delay_seconds", issues)
    min_delay = anti_bot.get("min_delay_seconds", 0)
    max_delay = anti_bot.get("max_delay_seconds", 0)
    if isinstance(min_delay, (int, float)) and isinstance(max_delay, (int, float)) and max_delay < min_delay:
        issues.append(ValidationIssue("anti_bot.max_delay_seconds", "must be >= min_delay_seconds"))
    _optional_bool(anti_bot, "respect_robots_txt", "anti_bot.respect_robots_txt", issues)
    cookies = anti_bot.get("cookies", {})
    if cookies is not None and (not isinstance(cookies, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in cookies.items())):
        issues.append(ValidationIssue("anti_bot.cookies", "must be an object with string values"))
    cookie_file = anti_bot.get("cookie_file")
    if cookie_file is not None and (not isinstance(cookie_file, str) or not cookie_file.strip()):
        issues.append(ValidationIssue("anti_bot.cookie_file", "must be a non-empty string"))
    referer_policy = anti_bot.get("referer_policy", "none")
    if referer_policy not in SUPPORTED_REFERER_POLICIES:
        issues.append(ValidationIssue("anti_bot.referer_policy", f"must be one of {SUPPORTED_REFERER_POLICIES}"))
    seed = anti_bot.get("random_seed")
    if seed is not None and not isinstance(seed, int):
        issues.append(ValidationIssue("anti_bot.random_seed", "must be an integer"))


def _validate_session(session: Any, issues: list[ValidationIssue]) -> None:
    if session is None:
        return
    if not isinstance(session, dict):
        issues.append(ValidationIssue("session", "must be an object"))
        return
    _optional_bool(session, "enabled", "session.enabled", issues)
    _optional_bool(session, "persist", "session.persist", issues)
    _optional_bool(session, "load_before_request", "session.load_before_request", issues)
    _optional_bool(session, "save_after_request", "session.save_after_request", issues)
    for key in ("profile", "cookie_file", "storage_state", "account_ref"):
        value = session.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            issues.append(ValidationIssue(f"session.{key}", "must be a non-empty string"))
    auth_check = session.get("auth_check", {})
    if auth_check is not None:
        if not isinstance(auth_check, dict):
            issues.append(ValidationIssue("session.auth_check", "must be an object"))
        else:
            _optional_bool(auth_check, "enabled", "session.auth_check.enabled", issues)
            check_type = auth_check.get("type", "status_code")
            if check_type not in SUPPORTED_AUTH_CHECK_TYPES:
                issues.append(ValidationIssue("session.auth_check.type", f"must be one of {SUPPORTED_AUTH_CHECK_TYPES}"))
            if "expected_status" in auth_check and not isinstance(auth_check["expected_status"], int):
                issues.append(ValidationIssue("session.auth_check.expected_status", "must be an integer"))
            for key in ("body_contains", "body_not_contains", "json_path", "header"):
                value = auth_check.get(key)
                if value is not None and not isinstance(value, str):
                    issues.append(ValidationIssue(f"session.auth_check.{key}", "must be a string"))
    _validate_session_flow(session.get("login_flow", {}), "session.login_flow", issues)
    _validate_session_flow(session.get("refresh_flow", {}), "session.refresh_flow", issues)


def _validate_session_flow(flow: Any, path: str, issues: list[ValidationIssue]) -> None:
    if flow is None:
        return
    if not isinstance(flow, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return
    _optional_bool(flow, "enabled", f"{path}.enabled", issues)
    steps = flow.get("steps", [])
    if steps is None:
        return
    if not isinstance(steps, list):
        issues.append(ValidationIssue(f"{path}.steps", "must be a list"))
        return
    for index, step in enumerate(steps):
        step_path = f"{path}.steps[{index}]"
        if not isinstance(step, dict):
            issues.append(ValidationIssue(step_path, "must be an object"))
            continue
        step_type = step.get("type")
        if step_type not in SUPPORTED_SESSION_FLOW_STEPS:
            issues.append(ValidationIssue(f"{step_path}.type", f"must be one of {SUPPORTED_SESSION_FLOW_STEPS}"))
        if step_type == "request":
            method = step.get("method", "GET")
            if method not in SUPPORTED_HTTP_METHODS:
                issues.append(ValidationIssue(f"{step_path}.method", f"must be one of {SUPPORTED_HTTP_METHODS}"))
            url = step.get("url")
            if url is not None and (not isinstance(url, str) or not url.strip()):
                issues.append(ValidationIssue(f"{step_path}.url", "must be a non-empty string"))
        if step_type in {"set_cookie", "set_header"}:
            name = step.get("name")
            if not isinstance(name, str) or not name.strip():
                issues.append(ValidationIssue(f"{step_path}.name", "must be a non-empty string"))


def _validate_observability(observability: Any, issues: list[ValidationIssue]) -> None:
    if observability is None:
        return
    if not isinstance(observability, dict):
        issues.append(ValidationIssue("observability", "must be an object"))
        return
    for key in (
        "enabled",
        "structured_logs",
        "capture_request_timeline",
        "capture_response_metadata",
        "capture_record_samples",
        "redact_sensitive",
        "metrics_enabled",
        "run_report_enabled",
    ):
        _optional_bool(observability, key, f"observability.{key}", issues)
    log_level = str(observability.get("log_level", "INFO")).upper()
    if log_level not in SUPPORTED_LOG_LEVELS:
        issues.append(ValidationIssue("observability.log_level", f"must be one of {SUPPORTED_LOG_LEVELS}"))
    _non_negative_int(observability.get("record_sample_limit", 10), "observability.record_sample_limit", issues)


def _validate_rate_limit(rate_limit: Any, issues: list[ValidationIssue]) -> None:
    if rate_limit is None:
        return
    if not isinstance(rate_limit, dict):
        issues.append(ValidationIssue("rate_limit", "must be an object"))
        return
    _optional_bool(rate_limit, "enabled", "rate_limit.enabled", issues)
    _non_negative_number(rate_limit.get("requests_per_second", 0), "rate_limit.requests_per_second", issues)
    _optional_bool(rate_limit, "per_domain", "rate_limit.per_domain", issues)


def _validate_concurrency(concurrency: Any, issues: list[ValidationIssue]) -> None:
    if concurrency is None:
        return
    if not isinstance(concurrency, dict):
        issues.append(ValidationIssue("concurrency", "must be an object"))
        return
    _optional_bool(concurrency, "enabled", "concurrency.enabled", issues)
    _positive_int(concurrency.get("max_concurrent_requests", 1), "concurrency.max_concurrent_requests", issues)
    _optional_bool(concurrency, "per_domain", "concurrency.per_domain", issues)


def _validate_crawl_policy(policy: Any, issues: list[ValidationIssue]) -> None:
    if policy is None:
        return
    if not isinstance(policy, dict):
        issues.append(ValidationIssue("crawl_policy", "must be an object"))
        return
    for key in ("enabled", "allow_cross_domain", "normalize_url", "remove_fragment", "remove_tracking_params"):
        _optional_bool(policy, key, f"crawl_policy.{key}", issues)
    if policy.get("user_agent") is not None and (not isinstance(policy.get("user_agent"), str) or not policy.get("user_agent").strip()):
        issues.append(ValidationIssue("crawl_policy.user_agent", "must be a non-empty string"))
    for key in ("allowed_domains", "denied_domains", "tracking_params"):
        _string_list(policy.get(key, []), f"crawl_policy.{key}", issues)
    for key in ("include_url_patterns", "exclude_url_patterns"):
        value = policy.get(key, [])
        if _string_list(value, f"crawl_policy.{key}", issues):
            for index, pattern in enumerate(value):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    issues.append(ValidationIssue(f"crawl_policy.{key}[{index}]", f"invalid regex: {exc}"))
    _positive_int(policy.get("max_requests", 100), "crawl_policy.max_requests", issues)
    _non_negative_int(policy.get("max_depth", 3), "crawl_policy.max_depth", issues)
    _positive_int(policy.get("max_duration_seconds", 300), "crawl_policy.max_duration_seconds", issues)

    robots = policy.get("robots", {})
    if robots is None:
        return
    if not isinstance(robots, dict):
        issues.append(ValidationIssue("crawl_policy.robots", "must be an object"))
        return
    _optional_bool(robots, "enabled", "crawl_policy.robots.enabled", issues)
    mode = robots.get("mode", "warn")
    if mode not in SUPPORTED_ROBOTS_MODES:
        issues.append(ValidationIssue("crawl_policy.robots.mode", f"must be one of {SUPPORTED_ROBOTS_MODES}"))
    unavailable_policy = robots.get("unavailable_policy", "warn")
    if unavailable_policy not in SUPPORTED_ROBOTS_UNAVAILABLE_POLICIES:
        issues.append(ValidationIssue("crawl_policy.robots.unavailable_policy", f"must be one of {SUPPORTED_ROBOTS_UNAVAILABLE_POLICIES}"))
    rules = robots.get("rules", {})
    if rules is not None and (not isinstance(rules, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in rules.items())):
        issues.append(ValidationIssue("crawl_policy.robots.rules", "must be an object with string values"))


def _validate_detail(detail: dict[str, Any], path: str, issues: list[ValidationIssue]) -> None:
    url_selector = detail.get("url_selector") or detail.get("link_selector")
    if not detail.get("url_field") and not url_selector:
        issues.append(ValidationIssue(path, "enabled detail requires url_field or url_selector"))
    merge_strategy = detail.get("merge_strategy", "override")
    if merge_strategy not in SUPPORTED_DETAIL_MERGE_STRATEGIES:
        issues.append(ValidationIssue(f"{path}.merge_strategy", f"must be one of {SUPPORTED_DETAIL_MERGE_STRATEGIES}"))
    _positive_int(detail.get("max_depth", 1), f"{path}.max_depth", issues)
    _validate_request(detail.get("request", {}), f"{path}.request", issues)
    for index, field in enumerate(detail.get("fields", [])):
        _validate_field(field, f"{path}.fields[{index}]", issues)
    for index, child in enumerate(detail.get("details", [])):
        if not isinstance(child, dict):
            issues.append(ValidationIssue(f"{path}.details[{index}]", "must be an object"))
        else:
            _validate_detail(child, f"{path}.details[{index}]", issues)


def _validate_request(request: Any, path: str, issues: list[ValidationIssue]) -> None:
    if request is None:
        request = {}
    if not isinstance(request, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return
    method = str(request.get("method", "GET")).upper()
    if method not in SUPPORTED_HTTP_METHODS:
        issues.append(ValidationIssue(f"{path}.method", f"must be one of {SUPPORTED_HTTP_METHODS}"))
    response_type = request.get("response_type")
    if response_type is not None and response_type not in SUPPORTED_RESPONSE_TYPES:
        issues.append(ValidationIssue(f"{path}.response_type", f"must be one of {SUPPORTED_RESPONSE_TYPES}"))
    _non_negative_number(request.get("delay_seconds", 0), f"{path}.delay_seconds", issues)
    _positive_number(request.get("timeout_seconds", 20), f"{path}.timeout_seconds", issues)
    _non_negative_int(request.get("max_retries", 2), f"{path}.max_retries", issues)


def _require_string(data: dict[str, Any], key: str, issues: list[ValidationIssue], prefix: str = "", required: bool = True) -> None:
    path = f"{prefix}.{key}" if prefix else key
    value = data.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue(path, "must be a non-empty string"))


def _positive_number(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, (int, float)) or value <= 0:
        issues.append(ValidationIssue(path, "must be a positive number"))


def _non_negative_number(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, (int, float)) or value < 0:
        issues.append(ValidationIssue(path, "must be a non-negative number"))


def _positive_int(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, int) or value < 1:
        issues.append(ValidationIssue(path, "must be a positive integer"))


def _non_negative_int(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, int) or value < 0:
        issues.append(ValidationIssue(path, "must be a non-negative integer"))


def _optional_bool(data: dict[str, Any], key: str, path: str, issues: list[ValidationIssue]) -> None:
    if key in data and not isinstance(data[key], bool):
        issues.append(ValidationIssue(path, "must be a boolean"))


def _string_list(value: Any, path: str, issues: list[ValidationIssue]) -> bool:
    if value is None:
        return True
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        issues.append(ValidationIssue(path, "must be a list of non-empty strings"))
        return False
    return True
