import random
import re
import threading
import time
from pathlib import Path

from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FetchError, HttpRequest, HttpResponse
from crawler_platform.models import (
    AntiBotConfig,
    ConcurrencyConfig,
    DetailOptions,
    FieldRule,
    PaginationOptions,
    PlaywrightOptions,
    ProxyConfig,
    RateLimitConfig,
    RequestOptions,
    RetryConfig,
    SpiderConfig,
    TaskRecord,
    TaskStatus,
)
from crawler_platform.playwright_runner import PlaywrightFetcher
from crawler_platform.request_governance import ProxyManager, RequestPipeline
from crawler_platform.storage import FileStore


class SequenceFetcher:
    def __init__(self, responses):
        self.responses = {url: list(values) for url, values in responses.items()}
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        values = self.responses.get(request.url)
        if not values:
            raise FetchError(f"No fake response for {request.url}", "network", url=request.url)
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, HttpResponse):
            return value
        return HttpResponse(url=request.url, final_url=request.url, status_code=200, body=value)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _html_spider(**overrides):
    spider = SpiderConfig(
        id=overrides.pop("id", "governance-html"),
        name="Governance HTML",
        type="http",
        start_urls=overrides.pop("start_urls", ["https://example.test/list"]),
        item_selector="article.item",
        fields=[FieldRule(name="title", type="css", selector=".title")],
        unique_fields=["title"],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _api_spider(**overrides):
    spider = SpiderConfig(
        id=overrides.pop("id", "governance-api"),
        name="Governance API",
        type="api",
        start_urls=overrides.pop("start_urls", ["https://example.test/api"]),
        items_json_path="items",
        fields=[FieldRule(name="id", type="json_path", json_path="id"), FieldRule(name="title", type="json_path", json_path="title")],
        unique_fields=["id"],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _html(title):
    return f'<article class="item"><span class="title">{title}</span><a class="detail" href="/detail/{title.lower()}">Detail</a></article>'


def test_retry_disabled_only_attempts_once(workspace_tmp_path):
    fetcher = SequenceFetcher({"https://example.test/list": [FetchError("down", "network", url="https://example.test/list"), _html("A")]})
    spider = _html_spider(retry=RetryConfig(enabled=False, max_attempts=3))

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="retry-disabled")

    assert task.status == TaskStatus.FAILED
    assert len(fetcher.requests) == 1
    assert task.retry_attempts == 0


def test_retry_network_error_succeeds_and_updates_counters(workspace_tmp_path):
    fetcher = SequenceFetcher({"https://example.test/list": [FetchError("down", "network", url="https://example.test/list"), _html("A")]})
    spider = _html_spider(retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"], backoff="none"))

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="retry-network")

    assert task.status == TaskStatus.SUCCESS
    assert len(fetcher.requests) == 2
    assert task.retry_attempts == 1
    assert task.retry_successes == 1


def test_retry_http_status_succeeds(workspace_tmp_path):
    fetcher = SequenceFetcher(
        {"https://example.test/list": [HttpResponse(url="https://example.test/list", status_code=503, body="busy"), _html("A")]}
    )
    spider = _html_spider(retry=RetryConfig(enabled=True, max_attempts=2, retry_on_status=[503], retry_on_errors=["http_status"]))

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="retry-status")

    assert task.status == TaskStatus.SUCCESS
    assert task.retry_attempts == 1


def test_retry_json_parse_error_succeeds(workspace_tmp_path):
    fetcher = SequenceFetcher({"https://example.test/api": ["not-json", '{"items":[{"id":1,"title":"A"}]}']})
    spider = _api_spider(retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["parse"]))

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="retry-parse")

    assert task.status == TaskStatus.SUCCESS
    assert task.retry_successes == 1


def test_retry_exhausted_marks_failure(workspace_tmp_path):
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [
                FetchError("down 1", "network", url="https://example.test/list"),
                FetchError("down 2", "network", url="https://example.test/list"),
            ]
        }
    )
    spider = _html_spider(retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"]))

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="retry-exhausted")

    assert task.status == TaskStatus.FAILED
    assert task.error_type == "retry_exhausted"
    assert task.retry_attempts == 1
    assert task.retry_failures == 1


def test_fixed_and_exponential_backoff_use_fake_sleep(workspace_tmp_path):
    fixed_clock = FakeClock()
    fixed_fetcher = SequenceFetcher({"https://example.test/list": [FetchError("one", "network"), FetchError("two", "network"), _html("A")]})
    fixed_spider = _html_spider(retry=RetryConfig(enabled=True, max_attempts=3, retry_on_errors=["network"], backoff="fixed", backoff_base_seconds=2))

    CrawlerEngine(store=FileStore(workspace_tmp_path / "fixed"), fetcher=fixed_fetcher, sleep=fixed_clock.sleep, clock=fixed_clock.clock).run(
        fixed_spider, task_id="fixed"
    )

    exponential_clock = FakeClock()
    exponential_fetcher = SequenceFetcher({"https://example.test/list": [FetchError("one", "network"), FetchError("two", "network"), _html("A")]})
    exponential_spider = _html_spider(
        retry=RetryConfig(enabled=True, max_attempts=3, retry_on_errors=["network"], backoff="exponential", backoff_base_seconds=2)
    )

    CrawlerEngine(
        store=FileStore(workspace_tmp_path / "exponential"),
        fetcher=exponential_fetcher,
        sleep=exponential_clock.sleep,
        clock=exponential_clock.clock,
    ).run(exponential_spider, task_id="exponential")

    assert fixed_clock.sleeps == [2, 2]
    assert exponential_clock.sleeps == [2, 4]


def test_jitter_is_deterministic_with_seed(workspace_tmp_path):
    def run_once(root):
        clock = FakeClock()
        fetcher = SequenceFetcher({"https://example.test/list": [FetchError("one", "network"), _html("A")]})
        spider = _html_spider(
            retry=RetryConfig(
                enabled=True,
                max_attempts=2,
                retry_on_errors=["network"],
                backoff="fixed",
                backoff_base_seconds=2,
                jitter=True,
                random_seed=7,
            )
        )
        CrawlerEngine(store=FileStore(root), fetcher=fetcher, sleep=clock.sleep, clock=clock.clock).run(spider, task_id="jitter")
        return clock.sleeps

    assert run_once(workspace_tmp_path / "a") == run_once(workspace_tmp_path / "b")


def test_round_robin_proxy_rotates_and_http_request_receives_proxy(workspace_tmp_path):
    spider = _html_spider(
        start_urls=["https://example.test/a", "https://example.test/b"],
        proxy=ProxyConfig(enabled=True, mode="round_robin", proxies=["http://p1", "http://p2"]),
    )
    fetcher = SequenceFetcher({"https://example.test/a": [_html("A")], "https://example.test/b": [_html("B")]})

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="proxy-rr")

    assert task.status == TaskStatus.SUCCESS
    assert [request.proxy for request in fetcher.requests] == ["http://p1", "http://p2"]


def test_random_proxy_is_deterministic():
    spider = _html_spider(proxy=ProxyConfig(enabled=True, mode="random", proxies=["http://p1", "http://p2", "http://p3"], random_seed=3))
    task = TaskRecord(id="t", spider_id=spider.id)
    manager = ProxyManager(spider.proxy)

    actual = [manager.select(HttpRequest("GET", f"https://example.test/{index}"), spider, task) for index in range(4)]
    expected_random = random.Random(3)
    expected = [expected_random.choice(["http://p1", "http://p2", "http://p3"]) for _ in range(4)]

    assert actual == expected


def test_sticky_proxy_uses_same_proxy_for_domain():
    spider = _html_spider(proxy=ProxyConfig(enabled=True, mode="sticky", proxies=["http://p1", "http://p2"], sticky_key="domain"))
    task = TaskRecord(id="t", spider_id=spider.id)
    manager = ProxyManager(spider.proxy)

    first = manager.select(HttpRequest("GET", "https://example.test/a"), spider, task)
    second = manager.select(HttpRequest("GET", "https://example.test/b"), spider, task)

    assert first == second


def test_proxy_cooldown_skips_failed_proxy_then_recovers():
    clock = FakeClock()
    spider = _html_spider(proxy=ProxyConfig(enabled=True, mode="round_robin", proxies=["http://p1", "http://p2"], fail_threshold=1, cooldown_seconds=5))
    task = TaskRecord(id="t", spider_id=spider.id)
    manager = ProxyManager(spider.proxy, clock=clock.clock)

    failed = manager.select(HttpRequest("GET", "https://example.test/a"), spider, task)
    manager.report_failure(failed)
    during_cooldown = manager.select(HttpRequest("GET", "https://example.test/b"), spider, task)
    clock.sleep(5)
    after_cooldown = {manager.select(HttpRequest("GET", f"https://example.test/{i}"), spider, task) for i in range(3)}
    manager.report_success(failed)

    assert failed == "http://p1"
    assert during_cooldown == "http://p2"
    assert "http://p1" in after_cooldown
    assert manager.snapshot()["states"][0]["failures"] == 0


def test_antibot_headers_cookies_delay_and_referer(workspace_tmp_path):
    cookie_file = workspace_tmp_path / "cookies.json"
    cookie_file.write_text('{"file_cookie":"yes"}', encoding="utf-8")
    clock = FakeClock()
    spider = _html_spider(
        start_urls=["https://example.test/a", "https://example.test/b"],
        anti_bot=AntiBotConfig(
            enabled=True,
            user_agents=["UA-1"],
            headers_pool=[{"X-Pool": "one"}],
            random_delay=True,
            min_delay_seconds=0.5,
            max_delay_seconds=0.5,
            cookie_file=str(cookie_file),
            cookies={"inline_cookie": "yes"},
            referer_policy="previous_url",
        ),
    )
    fetcher = SequenceFetcher({"https://example.test/a": [_html("A")], "https://example.test/b": [_html("B")]})

    task = CrawlerEngine(store=FileStore(workspace_tmp_path / "store"), fetcher=fetcher, sleep=clock.sleep, clock=clock.clock).run(
        spider, task_id="antibot"
    )

    assert task.status == TaskStatus.SUCCESS
    assert fetcher.requests[0].headers["User-Agent"] == "UA-1"
    assert fetcher.requests[0].headers["X-Pool"] == "one"
    assert fetcher.requests[0].cookies == {"inline_cookie": "yes", "file_cookie": "yes"}
    assert fetcher.requests[1].headers["Referer"] == "https://example.test/a"
    assert clock.sleeps == [0.5, 0.5]


def test_rate_limit_uses_fake_sleep_and_updates_counters(workspace_tmp_path):
    clock = FakeClock()
    spider = _html_spider(
        start_urls=["https://example.test/a", "https://example.test/b"],
        rate_limit=RateLimitConfig(enabled=True, requests_per_second=1, per_domain=True),
    )
    fetcher = SequenceFetcher({"https://example.test/a": [_html("A")], "https://example.test/b": [_html("B")]})

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher, sleep=clock.sleep, clock=clock.clock).run(spider, task_id="rate")

    assert task.status == TaskStatus.SUCCESS
    assert clock.sleeps == [1.0]
    assert task.rate_limit_waits == 1
    assert task.rate_limit_wait_seconds == 1.0


def test_concurrency_limiter_caps_fetch_many_peak():
    spider = _html_spider(concurrency=ConcurrencyConfig(enabled=True, max_concurrent_requests=2, per_domain=False))
    task = TaskRecord(id="t", spider_id=spider.id)
    pipeline = RequestPipeline(spider, task)
    active = 0
    peak = 0
    lock = threading.Lock()

    def fetch(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return HttpResponse(url=request.url, final_url=request.url, status_code=200, body=_html(request.url[-1]))

    requests = [HttpRequest("GET", f"https://example.test/{index}") for index in range(4)]
    pipeline.fetch_many(requests, fetch)

    assert peak == 2
    assert task.concurrent_requests_peak == 2


def test_pagination_retry_success(workspace_tmp_path):
    spider = _html_spider(
        start_urls=["https://example.test/page-1"],
        pagination=PaginationOptions(type="url_list", urls=["https://example.test/page-2"], max_pages=2),
        retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"]),
    )
    fetcher = SequenceFetcher(
        {
            "https://example.test/page-1": [_html("A")],
            "https://example.test/page-2": [FetchError("down", "network"), _html("B")],
        }
    )

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="page-retry")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 2
    assert task.retry_successes == 1


def test_detail_retry_success(workspace_tmp_path):
    spider = _html_spider(
        detail=DetailOptions(enabled=True, url_field="detail_path", fields=[FieldRule(name="body", type="css", selector=".body")]),
        fields=[FieldRule(name="title", type="css", selector=".title"), FieldRule(name="detail_path", type="attr", selector="a.detail", attribute="href")],
        retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"]),
    )
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [_html("A")],
            "https://example.test/detail/a": [FetchError("down", "network"), '<div class="body">Detail A</div>'],
        }
    )

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="detail-retry")

    assert task.status == TaskStatus.SUCCESS
    assert task.retry_successes == 1


class SequenceRenderBackend:
    def __init__(self, responses):
        self.responses = {url: list(values) for url, values in responses.items()}
        self.requests = []

    def open_browser(self, options):
        return object()

    def render(self, browser, request, options):
        self.requests.append(request)
        value = self.responses[request.url].pop(0)
        if isinstance(value, Exception):
            raise value
        return HttpResponse(url=request.url, final_url=request.url, status_code=200, body=value, content_type="text/html")

    def close_browser(self, browser):
        pass

    def close(self):
        pass


def test_playwright_render_error_retry_and_proxy_passthrough(workspace_tmp_path):
    spider = _html_spider(
        type="playwright",
        playwright=PlaywrightOptions(enabled=True, browser_pool_size=1),
        retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["render"]),
        proxy=ProxyConfig(enabled=True, proxies=["http://p1"]),
    )
    backend = SequenceRenderBackend({"https://example.test/list": [FetchError("render failed", "playwright_render"), _html("A")]})
    fetcher = PlaywrightFetcher(spider.playwright, backend=backend)

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), playwright_fetcher=fetcher).run(spider, task_id="pw-retry")

    assert task.status == TaskStatus.SUCCESS
    assert task.retry_successes == 1
    assert [request.proxy for request in backend.requests] == ["http://p1", "http://p1"]


def test_fail_fast_true_fails_on_unrecoverable_error(workspace_tmp_path):
    spider = _html_spider(request=RequestOptions(fail_fast=True))
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [
                HttpResponse(url="https://example.test/list", status_code=404, body="missing"),
            ]
        }
    )

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="fail-fast")

    assert task.status == TaskStatus.FAILED
    assert task.failed_requests == 1


def test_fail_fast_false_keeps_success_with_structured_warning(workspace_tmp_path):
    spider = _html_spider(start_urls=["https://example.test/a", "https://example.test/b"], request=RequestOptions(fail_fast=False))
    fetcher = SequenceFetcher({"https://example.test/a": [_html("A")], "https://example.test/b": [FetchError("down", "network")]})

    task = CrawlerEngine(store=FileStore(workspace_tmp_path), fetcher=fetcher).run(spider, task_id="soft-failure")

    assert task.status == TaskStatus.SUCCESS
    assert task.failed_requests == 1
    assert task.warnings[0]["error_type"] == "network"


def test_fastapi_task_query_includes_request_governance_summary(workspace_tmp_path):
    import pytest

    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    fetcher = SequenceFetcher({"https://example.test/list": [FetchError("down", "network"), _html("A")]})
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    task_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}" and "GET" in route.methods)
    spider = _html_spider(retry=RetryConfig(enabled=True, max_attempts=2, retry_on_errors=["network"]))

    run_route.endpoint({"spider": spider.to_dict(), "task_id": "api-governance"})
    payload = task_route.endpoint("api-governance")

    assert payload["request_governance"]["retry"]["attempts"] == 1


def test_runtime_has_no_database_dependency_references():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    paths = list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]

    matches = [str(path) for path in paths if pattern.search(path.read_text(encoding="utf-8"))]

    assert matches == []
