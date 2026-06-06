from __future__ import annotations

import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import DEFAULT_TRACKING_PARAMS, CrawlPolicyConfig, SpiderConfig
from .url_seed import collect_request_urls

LOCAL_ROBOTS_HOSTS = {"localhost", "127.0.0.1", "::1", "example.test"}


@dataclass(slots=True)
class CrawlPolicyDecision:
    input_url: str
    normalized_url: str
    kind: str
    depth: int
    allowed: bool = True
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    robots: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CrawlPolicyEngine:
    def __init__(self, spider: SpiderConfig, *, clock: Any = None) -> None:
        self.spider = spider
        self.policy = spider.crawl_policy
        self.clock = clock or time.monotonic
        self.started_at = float(self.clock())
        self.seed_domains = seed_domains(spider)
        self.allowed_domains = {_clean_domain(item) for item in self.policy.allowed_domains if _clean_domain(item)}
        self.denied_domains = {_clean_domain(item) for item in self.policy.denied_domains if _clean_domain(item)}
        self.checked_urls = 0
        self.blocked_urls = 0
        self.warning_events = 0
        self.normalized_urls = 0
        self.decisions: list[CrawlPolicyDecision] = []

    @property
    def enabled(self) -> bool:
        return bool(self.policy.enabled)

    def check_url(self, url: str, *, depth: int = 0, kind: str = "request") -> CrawlPolicyDecision:
        if not self.enabled:
            decision = CrawlPolicyDecision(input_url=url, normalized_url=url, kind=kind, depth=depth)
            self.decisions.append(decision)
            return decision

        normalized = normalize_url(url, self.policy)
        decision = CrawlPolicyDecision(input_url=url, normalized_url=normalized, kind=kind, depth=depth)
        self.checked_urls += 1
        if normalized != url:
            self.normalized_urls += 1

        self._check_budget(decision)
        self._check_scope(decision)
        self._check_patterns(decision)
        self._check_robots(decision)

        if decision.violations:
            decision.allowed = False
            self.blocked_urls += 1
        if decision.warnings:
            self.warning_events += len(decision.warnings)
        self.decisions.append(decision)
        return decision

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "policy_checked_urls": self.checked_urls,
            "policy_blocked_urls": self.blocked_urls,
            "policy_warnings": self.warning_events,
            "normalized_urls": self.normalized_urls,
            "allowed_domains": sorted(self.allowed_domains or self.seed_domains),
            "denied_domains": sorted(self.denied_domains),
            "allow_cross_domain": bool(self.policy.allow_cross_domain),
            "robots": {
                "enabled": bool(self.policy.robots.enabled),
                "mode": self.policy.robots.mode,
                "unavailable_policy": self.policy.robots.unavailable_policy,
            },
        }

    def blocked_decisions(self) -> list[dict[str, Any]]:
        return [decision.to_dict() for decision in self.decisions if decision.blocked]

    def warning_decisions(self) -> list[dict[str, Any]]:
        return [decision.to_dict() for decision in self.decisions if decision.warnings]

    def _check_budget(self, decision: CrawlPolicyDecision) -> None:
        if self.checked_urls > self.policy.max_requests:
            decision.violations.append(f"max_requests exceeded: {self.policy.max_requests}")
        if decision.depth > self.policy.max_depth:
            decision.violations.append(f"max_depth exceeded: {self.policy.max_depth}")
        elapsed = max(0.0, float(self.clock()) - self.started_at)
        if elapsed > self.policy.max_duration_seconds:
            decision.violations.append(f"max_duration_seconds exceeded: {self.policy.max_duration_seconds}")

    def _check_scope(self, decision: CrawlPolicyDecision) -> None:
        host = domain_for_url(decision.normalized_url)
        if not host:
            return
        if any(_domain_matches(host, denied) for denied in self.denied_domains):
            decision.violations.append(f"denied domain: {host}")
            return
        if self.policy.allow_cross_domain:
            return
        allowed = self.allowed_domains or self.seed_domains
        if allowed and not any(_domain_matches(host, domain) for domain in allowed):
            decision.violations.append(f"outside allowed domain scope: {host}")

    def _check_patterns(self, decision: CrawlPolicyDecision) -> None:
        url = decision.normalized_url
        if self.policy.include_url_patterns and not any(re.search(pattern, url) for pattern in self.policy.include_url_patterns):
            decision.violations.append("url did not match include_url_patterns")
        if any(re.search(pattern, url) for pattern in self.policy.exclude_url_patterns):
            decision.violations.append("url matched exclude_url_patterns")

    def _check_robots(self, decision: CrawlPolicyDecision) -> None:
        robots = self.policy.robots
        if not robots.enabled:
            decision.robots = {"status": "disabled"}
            return
        parsed = urllib.parse.urlsplit(decision.normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            decision.robots = {"status": "skipped_local"}
            return
        host = parsed.hostname.lower()
        if _is_local_robots_host(host):
            decision.robots = {"status": "skipped_local", "domain": host}
            return
        robots_text = robots.rules.get(host) or _matching_rule_text(host, robots.rules)
        if robots_text is None:
            decision.robots = {"status": "unavailable", "domain": host}
            if robots.unavailable_policy == "block":
                decision.violations.append(f"robots unavailable for {host}")
            elif robots.unavailable_policy == "warn":
                decision.warnings.append(f"robots unavailable for {host}")
            return
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_text.splitlines())
        user_agent = self.policy.user_agent or self.spider.request.user_agent or "*"
        allowed = parser.can_fetch(user_agent, decision.normalized_url)
        decision.robots = {"status": "allowed" if allowed else "disallowed", "domain": host, "user_agent": user_agent}
        if allowed:
            return
        message = f"robots disallow for {host}: {decision.normalized_url}"
        if robots.mode == "block":
            decision.violations.append(message)
        else:
            decision.warnings.append(message)


def normalize_url(url: str, policy: CrawlPolicyConfig | None = None) -> str:
    policy = policy or CrawlPolicyConfig(enabled=True)
    if not policy.normalize_url:
        return url
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    fragment = "" if policy.remove_fragment else parsed.fragment
    query = parsed.query
    if policy.remove_tracking_params and query:
        tracking = {item.lower() for item in (policy.tracking_params or DEFAULT_TRACKING_PARAMS)}
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        query = urllib.parse.urlencode([(key, value) for key, value in pairs if key.lower() not in tracking], doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path, query, fragment))


def seed_domains(spider: SpiderConfig) -> set[str]:
    domains: set[str] = set()
    for url in collect_request_urls(spider):
        host = domain_for_url(url)
        if host:
            domains.add(host)
    return domains


def domain_for_url(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    return parsed.hostname.lower() if parsed.hostname else None


def build_crawl_plan(spider: SpiderConfig) -> dict[str, Any]:
    engine = CrawlPolicyEngine(spider)
    request_urls = collect_request_urls(spider)
    start_decisions = [engine.check_url(url, depth=0, kind="start_url") for url in request_urls]
    pagination_decisions = [engine.check_url(url, depth=0, kind="pagination_url") for url in spider.pagination.urls]
    decisions = [*start_decisions, *pagination_decisions]
    max_pages = spider.pagination.max_pages
    estimated_requests = _estimated_max_requests(spider, max_pages=max_pages)
    fields = _field_summary(spider)
    blocked = [decision.to_dict() for decision in decisions if decision.blocked]
    warnings = [warning for decision in decisions for warning in decision.warnings]
    return {
        "spider_id": spider.id,
        "start_urls": request_urls,
        "normalized_start_urls": [decision.normalized_url for decision in start_decisions],
        "allowed_domains": sorted(engine.allowed_domains or engine.seed_domains),
        "estimated_max_requests": estimated_requests,
        "max_pages": spider.pagination.max_pages,
        "max_records": spider.pagination.max_records,
        "max_depth": spider.crawl_policy.max_depth if spider.crawl_policy.enabled else spider.detail.max_depth,
        "pagination": {
            "type": spider.pagination.type,
            "max_pages": spider.pagination.max_pages,
            "static_urls": list(spider.pagination.urls),
            "next_selector": spider.pagination.next_selector,
            "next_json_path": spider.pagination.next_json_path,
        },
        "detail": {
            "enabled": spider.detail.enabled,
            "url_field": spider.detail.url_field,
            "url_selector": spider.detail.url_selector,
            "max_depth": spider.detail.max_depth,
            "nested_levels": len(spider.detail.details),
        },
        "crawl_policy": engine.summary(),
        "robots_checks": [decision.robots for decision in decisions if decision.robots],
        "blocked_urls": blocked,
        "warnings": warnings,
        "fields": fields,
        "safe_to_run": not blocked,
    }


def _request_urls(spider: SpiderConfig) -> list[str]:
    return collect_request_urls(spider)


def _estimated_max_requests(spider: SpiderConfig, *, max_pages: int) -> int:
    page_requests = max(1, len(_request_urls(spider))) * max(1, max_pages)
    detail_requests = 0
    if spider.detail.enabled:
        detail_requests = spider.pagination.max_records or page_requests
    estimated = page_requests + detail_requests
    if spider.crawl_policy.enabled:
        return min(estimated, spider.crawl_policy.max_requests)
    return estimated


def _field_summary(spider: SpiderConfig) -> list[dict[str, Any]]:
    return [
        {
            "name": field.namespace or field.name,
            "type": field.type,
            "required": field.required,
            "selector": field.json_path or field.selector or field.pattern,
        }
        for field in spider.fields
    ]


def _clean_domain(value: str) -> str:
    value = str(value).strip().lower()
    if not value:
        return ""
    if "://" in value:
        return urllib.parse.urlsplit(value).hostname or ""
    return value.split(":", 1)[0].strip()


def _domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _matching_rule_text(host: str, rules: dict[str, str]) -> str | None:
    for domain, text in rules.items():
        cleaned = _clean_domain(domain)
        if cleaned and _domain_matches(host, cleaned):
            return text
    return None


def _is_local_robots_host(host: str) -> bool:
    return host in LOCAL_ROBOTS_HOSTS or host.endswith(".test")
