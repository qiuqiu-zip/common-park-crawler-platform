from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    RETRYING = "retrying"
    RERUNNING = "rerunning"
    CANCELLING = "cancelling"


class WorkerJobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"
    PAUSED = "paused"
    RETRYING = "retrying"
    CANCELLING = "cancelling"


@dataclass(slots=True)
class LifecycleEvent:
    event_id: str
    target_type: str
    target_id: str
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    reason: str | None = None
    operator: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LifecycleEvent":
        raw = data.copy()
        raw.setdefault("metadata", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FieldRule:
    name: str
    type: str
    selector: str | None = None
    pattern: str | None = None
    attribute: str | None = None
    json_path: str | None = None
    default: Any = None
    many: bool = False
    required: bool = False
    transforms: list[Any] = field(default_factory=list)
    join_with: str | None = None
    children: list["FieldRule"] = field(default_factory=list)
    namespace: str | None = None
    override: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldRule":
        raw = data.copy()
        raw["children"] = [cls.from_dict(item) for item in raw.get("children", [])]
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class ScrollStrategyOverrideOptions:
    enabled: bool | None = None
    mode: str | None = None
    max_scrolls: int | None = None
    scroll_pause_ms: int | None = None
    stop_selector: str | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScrollStrategyOverrideOptions":
        raw = dict(data or {})
        values = {key: raw[key] for key in ("enabled", "mode", "max_scrolls", "scroll_pause_ms", "stop_selector") if key in raw}
        return cls(**values, _raw=values)

    def has(self, name: str) -> bool:
        return name in self._raw


@dataclass(slots=True)
class PlaywrightOverrideOptions:
    wait_until: str | None = None
    wait_for_selector: str | None = None
    wait_for_selector_timeout_ms: int | None = None
    post_load_wait_ms: int | None = None
    scroll_strategy: ScrollStrategyOverrideOptions | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlaywrightOverrideOptions":
        raw = dict(data or {})
        values = {key: raw[key] for key in ("wait_until", "wait_for_selector", "wait_for_selector_timeout_ms", "post_load_wait_ms") if key in raw}
        scroll_raw = raw.get("scroll_strategy") if "scroll_strategy" in raw else None
        return cls(
            **values,
            scroll_strategy=ScrollStrategyOverrideOptions.from_dict(scroll_raw) if "scroll_strategy" in raw else None,
            _raw={key: raw[key] for key in raw if key in {"wait_until", "wait_for_selector", "wait_for_selector_timeout_ms", "post_load_wait_ms", "scroll_strategy"}},
        )

    def has(self, name: str) -> bool:
        return name in self._raw


@dataclass(slots=True)
class RequestOptions:
    method: str = "GET"
    url: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str | None = None
    body: str | bytes | None = None
    json: Any = None
    delay_seconds: float = 0
    proxy: str | None = None
    timeout_seconds: float = 20
    max_retries: int = 2
    encoding: str | None = None
    response_type: str | None = None
    follow_redirects: bool = True
    fail_fast: bool = False
    playwright: PlaywrightOverrideOptions | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RequestOptions":
        raw = data or {}
        return cls(
            method=raw.get("method", "GET"),
            url=raw.get("url"),
            params=dict(raw.get("params", {})),
            headers={str(key): str(value) for key, value in dict(raw.get("headers", {})).items()},
            cookies={str(key): str(value) for key, value in dict(raw.get("cookies", {})).items()},
            user_agent=raw.get("user_agent"),
            body=raw.get("body"),
            json=raw.get("json"),
            delay_seconds=float(raw.get("delay_seconds", 0)),
            proxy=raw.get("proxy"),
            timeout_seconds=float(raw.get("timeout_seconds", 20)),
            max_retries=int(raw.get("max_retries", 2)),
            encoding=raw.get("encoding"),
            response_type=raw.get("response_type"),
            follow_redirects=bool(raw.get("follow_redirects", True)),
            fail_fast=bool(raw.get("fail_fast", False)),
            playwright=PlaywrightOverrideOptions.from_dict(raw.get("playwright")) if "playwright" in raw else None,
        )


@dataclass(slots=True)
class DetailOptions:
    enabled: bool = False
    url_field: str | None = None
    url_selector: str | None = None
    url_attr: str = "href"
    link_selector: str | None = None
    link_attribute: str = "href"
    request: RequestOptions = field(default_factory=RequestOptions)
    fields: list[FieldRule] = field(default_factory=list)
    merge_strategy: str = "override"
    namespace: str | None = None
    max_depth: int = 1
    details: list["DetailOptions"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DetailOptions":
        raw = data or {}
        fields = [FieldRule.from_dict(item) for item in raw.get("fields", [])]
        url_selector = raw.get("url_selector", raw.get("link_selector"))
        url_attr = raw.get("url_attr", raw.get("link_attribute", "href"))
        return cls(
            enabled=raw.get("enabled", False),
            url_field=raw.get("url_field"),
            url_selector=url_selector,
            url_attr=url_attr,
            link_selector=url_selector,
            link_attribute=url_attr,
            request=RequestOptions.from_dict(raw.get("request")),
            fields=fields,
            merge_strategy=raw.get("merge_strategy", "override"),
            namespace=raw.get("namespace"),
            max_depth=raw.get("max_depth", 1),
            details=[cls.from_dict(item) for item in raw.get("details", [])],
        )


@dataclass(slots=True)
class PaginationOptions:
    type: str = "none"
    next_selector: str | None = None
    next_attribute: str = "href"
    next_json_path: str | None = None
    page_param: str | None = None
    offset_param: str | None = None
    page_size_param: str | None = None
    page_size: int | None = None
    cursor_json_path: str | None = None
    urls: list[str] = field(default_factory=list)
    max_pages: int = 1
    max_records: int | None = None
    request: RequestOptions = field(default_factory=RequestOptions)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PaginationOptions":
        raw = data or {}
        return cls(
            type=raw.get("type", "none"),
            next_selector=raw.get("next_selector"),
            next_attribute=raw.get("next_attribute", "href"),
            next_json_path=raw.get("next_json_path"),
            page_param=raw.get("page_param"),
            offset_param=raw.get("offset_param"),
            page_size_param=raw.get("page_size_param"),
            page_size=raw.get("page_size"),
            cursor_json_path=raw.get("cursor_json_path"),
            urls=list(raw.get("urls", [])),
            max_pages=int(raw.get("max_pages", 1)),
            max_records=raw.get("max_records"),
            request=RequestOptions.from_dict(raw.get("request")),
        )


DEFAULT_TRACKING_PARAMS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
]


@dataclass(slots=True)
class RobotsPolicyConfig:
    enabled: bool = True
    mode: str = "warn"
    unavailable_policy: str = "warn"
    rules: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RobotsPolicyConfig":
        raw = dict(data or {})
        return cls(
            enabled=raw.get("enabled", True),
            mode=str(raw.get("mode", "warn")),
            unavailable_policy=str(raw.get("unavailable_policy", "warn")),
            rules={str(key).lower(): str(value) for key, value in dict(raw.get("rules", {})).items()},
        )


@dataclass(slots=True)
class CrawlPolicyConfig:
    enabled: bool = False
    user_agent: str | None = None
    robots: RobotsPolicyConfig = field(default_factory=RobotsPolicyConfig)
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    allow_cross_domain: bool = False
    include_url_patterns: list[str] = field(default_factory=list)
    exclude_url_patterns: list[str] = field(default_factory=list)
    normalize_url: bool = True
    remove_fragment: bool = True
    remove_tracking_params: bool = True
    tracking_params: list[str] = field(default_factory=lambda: list(DEFAULT_TRACKING_PARAMS))
    max_requests: int = 100
    max_depth: int = 3
    max_duration_seconds: int = 300

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CrawlPolicyConfig":
        if data is None:
            return cls()
        raw = dict(data)
        return cls(
            enabled=raw.get("enabled", True),
            user_agent=raw.get("user_agent"),
            robots=RobotsPolicyConfig.from_dict(raw.get("robots")),
            allowed_domains=[str(item).lower() for item in raw.get("allowed_domains", [])],
            denied_domains=[str(item).lower() for item in raw.get("denied_domains", [])],
            allow_cross_domain=raw.get("allow_cross_domain", False),
            include_url_patterns=[str(item) for item in raw.get("include_url_patterns", [])],
            exclude_url_patterns=[str(item) for item in raw.get("exclude_url_patterns", [])],
            normalize_url=raw.get("normalize_url", True),
            remove_fragment=raw.get("remove_fragment", True),
            remove_tracking_params=raw.get("remove_tracking_params", True),
            tracking_params=[str(item) for item in raw.get("tracking_params", DEFAULT_TRACKING_PARAMS)],
            max_requests=int(raw.get("max_requests", 100)),
            max_depth=int(raw.get("max_depth", 3)),
            max_duration_seconds=int(raw.get("max_duration_seconds", 300)),
        )


@dataclass(slots=True)
class ScrollStrategyOptions:
    enabled: bool = False
    mode: str = "none"
    max_scrolls: int = 3
    scroll_pause_ms: int = 800
    stop_selector: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScrollStrategyOptions":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class PlaywrightOptions:
    enabled: bool = False
    browser_pool_size: int = 1
    headless: bool = True
    wait_until: str = "networkidle"
    wait_for_selector: str | None = None
    wait_for_selector_timeout_ms: int = 10000
    post_load_wait_ms: int = 0
    scroll_strategy: ScrollStrategyOptions = field(default_factory=ScrollStrategyOptions)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlaywrightOptions":
        raw = dict(data or {})
        if "browser_pool_size" not in raw and "pool_size" in raw:
            raw["browser_pool_size"] = raw["pool_size"]
        return cls(
            enabled=raw.get("enabled", False),
            browser_pool_size=int(raw.get("browser_pool_size", 1)),
            headless=raw.get("headless", True),
            wait_until=raw.get("wait_until", "networkidle"),
            wait_for_selector=raw.get("wait_for_selector"),
            wait_for_selector_timeout_ms=int(raw.get("wait_for_selector_timeout_ms", 10000)),
            post_load_wait_ms=int(raw.get("post_load_wait_ms", 0)),
            scroll_strategy=ScrollStrategyOptions.from_dict(raw.get("scroll_strategy")),
        )


def _scroll_strategy_options_to_dict(options: ScrollStrategyOptions) -> dict[str, Any]:
    return {
        "enabled": options.enabled,
        "mode": options.mode,
        "max_scrolls": options.max_scrolls,
        "scroll_pause_ms": options.scroll_pause_ms,
        "stop_selector": options.stop_selector,
    }


def _scroll_strategy_override_to_dict(options: ScrollStrategyOverrideOptions | None) -> dict[str, Any] | None:
    if options is None:
        return None
    data: dict[str, Any] = {}
    if options.has("enabled"):
        data["enabled"] = options.enabled
    if options.has("mode"):
        data["mode"] = options.mode
    if options.has("max_scrolls"):
        data["max_scrolls"] = options.max_scrolls
    if options.has("scroll_pause_ms"):
        data["scroll_pause_ms"] = options.scroll_pause_ms
    if options.has("stop_selector"):
        data["stop_selector"] = options.stop_selector
    return data


def _playwright_options_to_dict(options: PlaywrightOptions) -> dict[str, Any]:
    return {
        "enabled": options.enabled,
        "browser_pool_size": options.browser_pool_size,
        "headless": options.headless,
        "wait_until": options.wait_until,
        "wait_for_selector": options.wait_for_selector,
        "wait_for_selector_timeout_ms": options.wait_for_selector_timeout_ms,
        "post_load_wait_ms": options.post_load_wait_ms,
        "scroll_strategy": _scroll_strategy_options_to_dict(options.scroll_strategy),
    }


def _playwright_override_to_dict(options: PlaywrightOverrideOptions | None) -> dict[str, Any] | None:
    if options is None:
        return None
    data: dict[str, Any] = {}
    if options.has("wait_until"):
        data["wait_until"] = options.wait_until
    if options.has("wait_for_selector"):
        data["wait_for_selector"] = options.wait_for_selector
    if options.has("wait_for_selector_timeout_ms"):
        data["wait_for_selector_timeout_ms"] = options.wait_for_selector_timeout_ms
    if options.has("post_load_wait_ms"):
        data["post_load_wait_ms"] = options.post_load_wait_ms
    if options.has("scroll_strategy"):
        data["scroll_strategy"] = _scroll_strategy_override_to_dict(options.scroll_strategy)
    return data


def _request_options_to_dict(options: RequestOptions) -> dict[str, Any]:
    return {
        "method": options.method,
        "url": options.url,
        "params": dict(options.params),
        "headers": dict(options.headers),
        "cookies": dict(options.cookies),
        "user_agent": options.user_agent,
        "body": options.body,
        "json": options.json,
        "delay_seconds": options.delay_seconds,
        "proxy": options.proxy,
        "timeout_seconds": options.timeout_seconds,
        "max_retries": options.max_retries,
        "encoding": options.encoding,
        "response_type": options.response_type,
        "follow_redirects": options.follow_redirects,
        "fail_fast": options.fail_fast,
        "playwright": _playwright_override_to_dict(options.playwright),
    }


def _detail_options_to_dict(detail: DetailOptions) -> dict[str, Any]:
    return {
        "enabled": detail.enabled,
        "url_field": detail.url_field,
        "url_selector": detail.url_selector,
        "url_attr": detail.url_attr,
        "link_selector": detail.link_selector,
        "link_attribute": detail.link_attribute,
        "request": _request_options_to_dict(detail.request),
        "fields": [asdict(item) for item in detail.fields],
        "merge_strategy": detail.merge_strategy,
        "namespace": detail.namespace,
        "max_depth": detail.max_depth,
        "details": [_detail_options_to_dict(item) for item in detail.details],
    }


def _pagination_options_to_dict(options: PaginationOptions) -> dict[str, Any]:
    return {
        "type": options.type,
        "next_selector": options.next_selector,
        "next_attribute": options.next_attribute,
        "next_json_path": options.next_json_path,
        "page_param": options.page_param,
        "offset_param": options.offset_param,
        "page_size_param": options.page_size_param,
        "page_size": options.page_size,
        "cursor_json_path": options.cursor_json_path,
        "urls": list(options.urls),
        "max_pages": options.max_pages,
        "max_records": options.max_records,
        "request": _request_options_to_dict(options.request),
    }


@dataclass(slots=True)
class RetryConfig:
    enabled: bool = False
    max_attempts: int = 1
    backoff: str = "none"
    backoff_base_seconds: float = 0
    backoff_max_seconds: float = 30
    retry_on_status: list[int] = field(default_factory=lambda: [500, 502, 503, 504])
    retry_on_errors: list[str] = field(default_factory=lambda: ["network", "timeout", "http_status", "parse", "render"])
    jitter: bool = False
    fail_after_attempts: bool = True
    random_seed: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetryConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class ProxyConfig:
    enabled: bool = False
    mode: str = "round_robin"
    proxies: list[str] = field(default_factory=list)
    proxy_file: str | None = None
    fail_threshold: int = 2
    cooldown_seconds: float = 60
    sticky_key: str | None = None
    health_check_url: str | None = None
    random_seed: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProxyConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class AntiBotConfig:
    enabled: bool = False
    user_agents: list[str] = field(default_factory=list)
    headers_pool: list[dict[str, str]] = field(default_factory=list)
    random_delay: bool = False
    min_delay_seconds: float = 0
    max_delay_seconds: float = 0
    respect_robots_txt: bool = False
    cookie_file: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    referer_policy: str = "none"
    random_seed: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AntiBotConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class AuthCheckConfig:
    enabled: bool = False
    type: str = "status_code"
    expected_status: int = 200
    body_contains: str | None = None
    body_not_contains: str | None = None
    json_path: str | None = None
    header: str | None = None
    expected_value: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AuthCheckConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class SessionFlowConfig:
    enabled: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SessionFlowConfig":
        raw = dict(data or {})
        steps = raw.get("steps", [])
        return cls(enabled=bool(raw.get("enabled", False)), steps=list(steps if isinstance(steps, list) else []))


@dataclass(slots=True)
class SessionConfig:
    enabled: bool = False
    profile: str | None = None
    cookie_file: str | None = None
    storage_state: str | None = None
    persist: bool = True
    load_before_request: bool = True
    save_after_request: bool = True
    account_ref: str | None = None
    auth_check: AuthCheckConfig = field(default_factory=AuthCheckConfig)
    login_flow: SessionFlowConfig = field(default_factory=SessionFlowConfig)
    refresh_flow: SessionFlowConfig = field(default_factory=SessionFlowConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SessionConfig":
        raw = dict(data or {})
        return cls(
            enabled=bool(raw.get("enabled", False)),
            profile=raw.get("profile"),
            cookie_file=raw.get("cookie_file"),
            storage_state=raw.get("storage_state"),
            persist=raw.get("persist", True),
            load_before_request=raw.get("load_before_request", True),
            save_after_request=raw.get("save_after_request", True),
            account_ref=raw.get("account_ref"),
            auth_check=AuthCheckConfig.from_dict(raw.get("auth_check")),
            login_flow=SessionFlowConfig.from_dict(raw.get("login_flow")),
            refresh_flow=SessionFlowConfig.from_dict(raw.get("refresh_flow")),
        )


@dataclass(slots=True)
class ObservabilityConfig:
    enabled: bool = True
    log_level: str = "INFO"
    structured_logs: bool = True
    capture_request_timeline: bool = True
    capture_response_metadata: bool = True
    capture_record_samples: bool = True
    record_sample_limit: int = 10
    redact_sensitive: bool = True
    metrics_enabled: bool = True
    run_report_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ObservabilityConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        if "log_level" in raw:
            raw["log_level"] = str(raw["log_level"]).upper()
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class ExportConfig:
    enabled: bool = True
    formats: list[str] = field(default_factory=lambda: ["json", "jsonl", "csv", "xlsx"])
    default_format: str = "jsonl"
    output_dir: str | None = None
    include_fields: list[str] = field(default_factory=list)
    exclude_fields: list[str] = field(default_factory=list)
    field_aliases: dict[str, str] = field(default_factory=dict)
    include_metadata: bool = False
    include_dedup: bool = True
    redact_sensitive: bool = True
    nested_strategy: str = "flatten_dot"
    list_strategy: str = "json_string"
    join_separator: str = ","
    include_observability: bool = False
    include_lifecycle: bool = False
    manifest_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExportConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        if "format" in raw and "default_format" not in raw:
            raw["default_format"] = raw["format"]
        if "flatten" in raw and "nested_strategy" not in raw:
            raw["nested_strategy"] = raw["flatten"]
        if "include_dedup_metadata" in raw and "include_dedup" not in raw:
            raw["include_dedup"] = raw["include_dedup_metadata"]
        if "formats" in raw and raw["formats"] is not None:
            raw["formats"] = [str(item).lower() for item in raw["formats"]]
        if "default_format" in raw and raw["default_format"] is not None:
            raw["default_format"] = str(raw["default_format"]).lower()
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class RateLimitConfig:
    enabled: bool = False
    requests_per_second: float = 0
    per_domain: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RateLimitConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class ConcurrencyConfig:
    enabled: bool = False
    max_concurrent_requests: int = 1
    per_domain: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConcurrencyConfig":
        raw = dict(data or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class SchedulerOptions:
    enabled: bool = False
    type: str = "manual"
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str = "UTC"
    start_at: str | None = None
    end_at: str | None = None
    misfire_policy: str = "skip"
    max_instances: int = 1
    jitter_seconds: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SchedulerOptions":
        raw = dict(data or {})
        if "max_instances" not in raw and "max_concurrency" in raw:
            raw["max_instances"] = raw["max_concurrency"]
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    @property
    def max_concurrency(self) -> int:
        return self.max_instances

    @max_concurrency.setter
    def max_concurrency(self, value: int) -> None:
        self.max_instances = value


@dataclass(slots=True)
class SpiderConfig:
    id: str
    name: str
    start_urls: list[str] = field(default_factory=list)
    seed: Any | None = None
    version: str = "1.0"
    type: str = "http"
    description: str | None = None
    enabled: bool = True
    item_selector: str | None = None
    items_json_path: str | None = None
    fields: list[FieldRule] = field(default_factory=list)
    unique_fields: list[str] = field(default_factory=list)
    request: RequestOptions = field(default_factory=RequestOptions)
    pagination: PaginationOptions = field(default_factory=PaginationOptions)
    detail: DetailOptions = field(default_factory=DetailOptions)
    playwright: PlaywrightOptions = field(default_factory=PlaywrightOptions)
    scheduler: SchedulerOptions = field(default_factory=SchedulerOptions)
    dedup: dict[str, Any] = field(default_factory=dict)
    watermark: dict[str, Any] = field(default_factory=dict)
    retry: RetryConfig = field(default_factory=RetryConfig)
    anti_bot: AntiBotConfig = field(default_factory=AntiBotConfig)
    crawl_policy: CrawlPolicyConfig = field(default_factory=CrawlPolicyConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpiderConfig":
        raw = data.copy()
        if "version" not in raw and "schema_version" in raw:
            raw["version"] = str(raw["schema_version"])
        if "type" not in raw and "mode" in raw:
            raw["type"] = raw["mode"]
        if "scheduler" not in raw and "schedule" in raw:
            raw["scheduler"] = raw["schedule"]
        return cls(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            start_urls=list(raw.get("start_urls", [])),
            seed=raw.get("seed"),
            version=str(raw.get("version", "1.0")),
            type=raw.get("type", "http"),
            description=raw.get("description"),
            enabled=raw.get("enabled", True),
            item_selector=raw.get("item_selector"),
            items_json_path=raw.get("items_json_path"),
            fields=[FieldRule.from_dict(item) for item in raw.get("fields", [])],
            unique_fields=list(raw.get("unique_fields", [])),
            request=RequestOptions.from_dict(raw.get("request")),
            pagination=PaginationOptions.from_dict(raw.get("pagination")),
            detail=DetailOptions.from_dict(raw.get("detail")),
            playwright=PlaywrightOptions.from_dict(raw.get("playwright")),
            scheduler=SchedulerOptions.from_dict(raw.get("scheduler")),
            dedup=dict(raw.get("dedup", {})),
            watermark=dict(raw.get("watermark", {})),
            retry=RetryConfig.from_dict(raw.get("retry")),
            anti_bot=AntiBotConfig.from_dict(raw.get("anti_bot")),
            crawl_policy=CrawlPolicyConfig.from_dict(raw.get("crawl_policy")),
            session=SessionConfig.from_dict(raw.get("session")),
            observability=ObservabilityConfig.from_dict(raw.get("observability")),
            proxy=ProxyConfig.from_dict(raw.get("proxy")),
            rate_limit=RateLimitConfig.from_dict(raw.get("rate_limit")),
            concurrency=ConcurrencyConfig.from_dict(raw.get("concurrency")),
            export=ExportConfig.from_dict(raw.get("export")),
            metadata=dict(raw.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "start_urls": self.start_urls,
            "seed": self.seed,
            "version": self.version,
            "description": self.description,
            "enabled": self.enabled,
            "type": self.type,
            "item_selector": self.item_selector,
            "items_json_path": self.items_json_path,
            "fields": [asdict(item) for item in self.fields],
            "unique_fields": self.unique_fields,
            "request": _request_options_to_dict(self.request),
            "pagination": _pagination_options_to_dict(self.pagination),
            "detail": _detail_options_to_dict(self.detail),
            "playwright": _playwright_options_to_dict(self.playwright),
            "scheduler": asdict(self.scheduler),
            "dedup": self.dedup,
            "watermark": self.watermark,
            "retry": asdict(self.retry),
            "anti_bot": asdict(self.anti_bot),
            "crawl_policy": asdict(self.crawl_policy),
            "session": asdict(self.session),
            "observability": asdict(self.observability),
            "proxy": asdict(self.proxy),
            "rate_limit": asdict(self.rate_limit),
            "concurrency": asdict(self.concurrency),
            "export": asdict(self.export),
            "metadata": self.metadata,
        }

    @property
    def mode(self) -> str:
        return self.type

    @mode.setter
    def mode(self, value: str) -> None:
        self.type = value

    @property
    def schedule(self) -> SchedulerOptions:
        return self.scheduler

    @schedule.setter
    def schedule(self, value: SchedulerOptions) -> None:
        self.scheduler = value


@dataclass(slots=True)
class SchedulerJob:
    id: str
    spider_id: str
    spider: dict[str, Any]
    scheduler: dict[str, Any]
    status: str = "enabled"
    next_run_at: str | None = None
    last_run_at: str | None = None
    running_instances: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchedulerJob":
        raw = data.copy()
        raw.setdefault("warnings", [])
        raw.setdefault("metadata", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SchedulerRun:
    id: str
    schedule_id: str
    spider_id: str
    task_id: str | None = None
    status: str = "running"
    trigger: str = "scheduled"
    scheduled_for: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    records_count: int = 0
    warnings: list[Any] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchedulerRun":
        raw = data.copy()
        raw.setdefault("warnings", [])
        raw.setdefault("summary", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkerJob:
    job_id: str
    job_type: str
    spider_id: str
    spider_config: dict[str, Any]
    task_id: str | None = None
    schedule_id: str | None = None
    source: str = "manual"
    status: str = WorkerJobStatus.QUEUED.value
    priority: int = 0
    run_after: str | None = None
    attempt: int = 0
    max_attempts: int = 1
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    warnings: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerJob":
        raw = data.copy()
        raw.setdefault("warnings", [])
        raw.setdefault("metadata", {})
        raw.setdefault("attempt", 0)
        raw.setdefault("max_attempts", 1)
        raw.setdefault("priority", 0)
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkerStats:
    claimed_jobs: int = 0
    succeeded_jobs: int = 0
    failed_jobs: int = 0
    retried_jobs: int = 0
    dead_letter_jobs: int = 0
    heartbeat_count: int = 0
    concurrency_peak: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkerStats":
        raw = data or {}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: raw.get(key, 0) for key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkerState:
    worker_id: str
    status: str = "idle"
    current_job_id: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerState":
        raw = data.copy()
        raw.setdefault("stats", {})
        raw.setdefault("metadata", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkerRunResult:
    run_id: str
    worker_id: str
    job_id: str | None = None
    task_id: str | None = None
    status: str = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    warnings: list[Any] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerRunResult":
        raw = data.copy()
        raw.setdefault("warnings", [])
        raw.setdefault("summary", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskRecord:
    id: str
    spider_id: str
    status: TaskStatus = TaskStatus.PENDING
    source_task_id: str | None = None
    total_seen: int = 0
    saved_count: int = 0
    skipped_duplicates: int = 0
    duplicate_records: int = 0
    failed_count: int = 0
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    total_records: int = 0
    saved_records: int = 0
    skipped_records: int = 0
    skipped_by_watermark: int = 0
    watermark_updates: int = 0
    checkpoint_saves: int = 0
    resume_count: int = 0
    retry_attempts: int = 0
    retry_successes: int = 0
    retry_failures: int = 0
    rate_limit_waits: int = 0
    rate_limit_wait_seconds: float = 0
    concurrent_requests_peak: int = 0
    session_loads: int = 0
    session_saves: int = 0
    auth_check_failures: int = 0
    login_flow_runs: int = 0
    refresh_flow_runs: int = 0
    error_type: str | None = None
    error_message: str | None = None
    warnings: list[Any] = field(default_factory=list)
    request_governance: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        raw = data.copy()
        raw["status"] = TaskStatus(raw.get("status", TaskStatus.PENDING))
        raw.setdefault("source_task_id", None)
        raw.setdefault("lifecycle", {})
        raw.setdefault("metadata", {})
        return cls(**raw)
