import json

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FetchError, HttpRequest, RequestContext
from crawler_platform.models import DetailOptions, FieldRule, PaginationOptions, PlaywrightOptions, RequestOptions, SpiderConfig
from crawler_platform.playwright_runner import BrowserPool, FakeRenderBackend, PlaywrightFetcher, _apply_page_readiness_controls
from crawler_platform.storage import FileStore
from crawler_platform.validation import validate_spider_config


def _request(url):
    return HttpRequest(method="GET", url=url, response_type="html")


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return self.page.selector_counts(self.selector)


class _FakePage:
    def __init__(self, *, stop_selector_visible_after=0, fail_wait_selector=False):
        self.stop_selector_visible_after = stop_selector_visible_after
        self.fail_wait_selector = fail_wait_selector
        self.wait_selector_calls = []
        self.wait_timeout_calls = []
        self.scroll_modes = []
        self.scroll_states = []

    def wait_for_selector(self, selector, *, state, timeout):
        self.wait_selector_calls.append({"selector": selector, "state": state, "timeout": timeout})
        if self.fail_wait_selector:
            raise RuntimeError("selector not ready")

    def wait_for_timeout(self, timeout_ms):
        self.wait_timeout_calls.append(timeout_ms)

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def selector_counts(self, selector):
        if selector == ".video-card" and self.scroll_states:
            return 1
        if selector == ".stop-after-scroll":
            return 1 if len(self.scroll_states) >= self.stop_selector_visible_after else 0
        return 0

    def evaluate(self, script, mode):
        steps = len(self.scroll_states) + 1
        scroll_top = 800 * steps if mode != "incremental" else 400 * steps
        state = {"scroll_top": scroll_top, "scroll_height": 3200, "viewport_height": 800}
        self.scroll_modes.append(mode)
        self.scroll_states.append(state)
        return state


def _playwright_spider(**overrides):
    spider = SpiderConfig(
        id=overrides.pop("id", "playwright-demo"),
        name="Playwright Demo",
        type="playwright",
        start_urls=overrides.pop("start_urls", ["https://example.test/list"]),
        item_selector="article.result",
        unique_fields=["title"],
        fields=[
            FieldRule(name="title", type="css", selector="a.title"),
            FieldRule(name="detail_path", type="attr", selector="a.title", attribute="href"),
        ],
        playwright=PlaywrightOptions(enabled=True, browser_pool_size=2, headless=True, wait_until="domcontentloaded"),
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def test_browser_pool_reuses_idle_browser_and_respects_headful_mode():
    backend = FakeRenderBackend({"https://example.test/a": "<html>A</html>", "https://example.test/b": "<html>B</html>"})
    pool = BrowserPool(PlaywrightOptions(enabled=True, browser_pool_size=1, headless=False), backend=backend)

    first = pool.fetch(_request("https://example.test/a"))
    second = pool.fetch(_request("https://example.test/b"))
    stats = pool.stats()
    pool.close()

    assert first.text == "<html>A</html>"
    assert second.text == "<html>B</html>"
    assert backend.opened == 1
    assert backend.closed == 1
    assert backend.launch_options[0].headless is False
    assert stats.reused == 1
    assert stats.requests == 2


def test_browser_pool_fetch_many_uses_concurrency():
    backend = FakeRenderBackend(
        {
            "https://example.test/a": "<html>A</html>",
            "https://example.test/b": "<html>B</html>",
        },
        delay_seconds=0.05,
    )
    pool = BrowserPool(PlaywrightOptions(enabled=True, browser_pool_size=2), backend=backend)

    responses = pool.fetch_many([_request("https://example.test/a"), _request("https://example.test/b")])
    stats = pool.stats()
    pool.close()

    assert [response.text for response in responses] == ["<html>A</html>", "<html>B</html>"]
    assert backend.max_active == 2
    assert stats.opened == 2
    assert stats.requests == 2


def test_browser_pool_releases_slot_after_failure():
    backend = FakeRenderBackend(
        {
            "https://example.test/fail": FetchError("render failed", "playwright_render", url="https://example.test/fail"),
            "https://example.test/ok": "<html>OK</html>",
        }
    )
    pool = BrowserPool(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend)

    try:
        try:
            pool.fetch(_request("https://example.test/fail"))
        except FetchError:
            pass
        response = pool.fetch(_request("https://example.test/ok"))
        stats = pool.stats()
    finally:
        pool.close()

    assert response.text == "<html>OK</html>"
    assert stats.active == 0
    assert stats.failures == 1
    assert stats.reused == 1


def test_playwright_config_accepts_pool_size_alias_and_headless():
    payload = _playwright_spider().to_dict()
    payload["playwright"] = {
        "enabled": True,
        "pool_size": 3,
        "headless": False,
        "wait_until": "load",
        "wait_for_selector": ".video-card",
        "wait_for_selector_timeout_ms": 4321,
        "post_load_wait_ms": 250,
        "scroll_strategy": {
            "enabled": True,
            "mode": "viewport",
            "max_scrolls": 4,
            "scroll_pause_ms": 125,
            "stop_selector": ".video-card",
        },
    }

    result = validate_spider_config(payload)
    spider = SpiderConfig.from_dict(payload)

    assert result.valid
    assert spider.playwright.browser_pool_size == 3
    assert spider.playwright.headless is False
    assert spider.playwright.wait_for_selector == ".video-card"
    assert spider.playwright.wait_for_selector_timeout_ms == 4321
    assert spider.playwright.post_load_wait_ms == 250
    assert spider.playwright.scroll_strategy.mode == "viewport"
    assert spider.playwright.scroll_strategy.max_scrolls == 4
    assert spider.playwright.scroll_strategy.stop_selector == ".video-card"


def test_playwright_config_accepts_request_level_render_overrides():
    payload = _playwright_spider().to_dict()
    payload["request"]["playwright"] = {
        "wait_for_selector": ".feed-card",
        "post_load_wait_ms": 250,
    }
    payload["pagination"] = {
        "type": "url_list",
        "urls": ["https://example.test/list?page=2"],
        "max_pages": 2,
        "request": {
            "playwright": {
                "wait_for_selector": ".page-ready",
                "scroll_strategy": {
                    "enabled": True,
                    "mode": "bottom",
                    "max_scrolls": 2,
                    "stop_selector": ".page-ready",
                },
            }
        },
    }
    payload["detail"] = {
        "enabled": True,
        "url_field": "detail_path",
        "request": {
            "playwright": {
                "wait_for_selector": None,
                "post_load_wait_ms": 100,
            }
        },
        "fields": [{"name": "summary", "type": "css", "selector": ".summary"}],
    }

    result = validate_spider_config(payload)
    spider = SpiderConfig.from_dict(payload)

    assert result.valid
    assert spider.request.playwright is not None
    assert spider.request.playwright.wait_for_selector == ".feed-card"
    assert spider.pagination.request.playwright is not None
    assert spider.pagination.request.playwright.scroll_strategy is not None
    assert spider.pagination.request.playwright.scroll_strategy.mode == "bottom"
    assert spider.detail.request.playwright is not None
    assert spider.detail.request.playwright.has("wait_for_selector")
    assert spider.detail.request.playwright.wait_for_selector is None


def test_playwright_readiness_controls_wait_and_scroll_until_stop_selector():
    page = _FakePage(stop_selector_visible_after=2)
    options = PlaywrightOptions.from_dict(
        {
            "enabled": True,
            "wait_for_selector": ".video-card",
            "wait_for_selector_timeout_ms": 1500,
            "post_load_wait_ms": 300,
            "scroll_strategy": {
                "enabled": True,
                "mode": "viewport",
                "max_scrolls": 5,
                "scroll_pause_ms": 200,
                "stop_selector": ".stop-after-scroll",
            },
        }
    )

    readiness = _apply_page_readiness_controls(
        page,
        "https://example.test/list",
        options,
        page_role="pagination",
        strategy_source="pagination.request.playwright",
    )

    assert page.wait_selector_calls == [{"selector": ".video-card", "state": "attached", "timeout": 1500}]
    assert page.scroll_modes == ["viewport", "viewport"]
    assert page.wait_timeout_calls == [300, 200, 200]
    assert readiness["page_role"] == "pagination"
    assert readiness["strategy_source"] == "pagination.request.playwright"
    assert readiness["wait_for_selector_used"] == ".video-card"
    assert readiness["wait_for_selector_matched"] is True
    assert readiness["wait_for_selector_elapsed_ms"] >= 0
    assert readiness["scroll_count"] == 2
    assert readiness["scrolls_performed"] == 2


def test_playwright_readiness_controls_wrap_wait_failures():
    page = _FakePage(fail_wait_selector=True)
    options = PlaywrightOptions.from_dict({"enabled": True, "wait_for_selector": ".video-card"})

    try:
        _apply_page_readiness_controls(page, "https://example.test/list", options, page_role="detail", strategy_source="detail.request.playwright")
    except FetchError as exc:
        assert exc.error_type == "playwright_render"
        assert exc.url == "https://example.test/list"
        assert "detail page" in str(exc)
        assert ".video-card" in str(exc)
        assert "timeout_ms=10000" in str(exc)
    else:
        raise AssertionError("expected FetchError")


def test_engine_uses_playwright_fetcher_for_playwright_spider(workspace_tmp_path):
    backend = FakeRenderBackend({"https://example.test/list": '<article class="result"><a class="title">Rendered A</a></article>'})
    fetcher = PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend)
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, playwright_fetcher=fetcher).run(_playwright_spider(), task_id="pw-task")

    assert task.status.value == "success"
    assert task.saved_records == 1
    assert store.read_records("pw-task")[0]["title"] == "Rendered A"
    assert backend.rendered_urls == ["https://example.test/list"]


def test_engine_playwright_pagination_and_detail(workspace_tmp_path):
    spider = _playwright_spider(start_urls=["https://example.test/list?page=1"])
    spider.pagination = PaginationOptions(type="url_list", urls=["https://example.test/list?page=2"], max_pages=2)
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=[FieldRule(name="summary", type="css", selector=".summary")])
    backend = FakeRenderBackend(
        {
            "https://example.test/list?page=1": '<article class="result"><a class="title" href="/detail/a">A</a></article>',
            "https://example.test/list?page=2": '<article class="result"><a class="title" href="/detail/b">B</a></article>',
            "https://example.test/detail/a": '<main><div class="summary">Summary A</div></main>',
            "https://example.test/detail/b": '<main><div class="summary">Summary B</div></main>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(
        store=store,
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=2), backend=backend),
    ).run(spider, task_id="pw-detail-task")

    assert task.total_requests == 4
    assert [record["summary"] for record in store.read_records("pw-detail-task")] == ["Summary A", "Summary B"]


def test_engine_playwright_detail_override_clears_global_wait_selector(workspace_tmp_path):
    spider = _playwright_spider()
    spider.playwright = PlaywrightOptions.from_dict({"enabled": True, "wait_for_selector": ".video-card", "wait_until": "domcontentloaded"})
    spider.detail = DetailOptions.from_dict(
        {
            "enabled": True,
            "url_field": "detail_path",
            "request": {
                "playwright": {
                    "wait_for_selector": None,
                    "post_load_wait_ms": 50,
                }
            },
            "fields": [{"name": "summary", "type": "css", "selector": ".summary"}],
        }
    )
    backend = FakeRenderBackend(
        {
            "https://example.test/list": {
                "body": '<article class="result"><a class="title" href="/detail/a">A</a></article>',
                "selector_counts": {".video-card": 1},
            },
            "https://example.test/detail/a": {
                "body": '<main><h1 class="headline">Detail A</h1><div class="summary">Summary A</div></main>',
                "wait_timeout_selectors": [".video-card"],
                "selector_counts": {"h1.headline": 1},
            },
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(
        store=store,
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    ).run(spider, task_id="pw-detail-override")

    assert task.status.value == "success"
    assert store.read_records("pw-detail-override")[0]["summary"] == "Summary A"
    assert [call["page_role"] for call in backend.render_calls] == ["start", "detail"]
    assert backend.render_calls[1]["strategy_source"] == "detail.request.playwright"
    assert backend.render_calls[1]["playwright_options"].wait_for_selector is None
    assert backend.render_calls[1]["playwright_options"].post_load_wait_ms == 50


def test_engine_playwright_pagination_uses_page_level_wait_override(workspace_tmp_path):
    spider = _playwright_spider(start_urls=["https://example.test/list?page=1"])
    spider.playwright = PlaywrightOptions.from_dict({"enabled": True, "wait_for_selector": ".video-card"})
    spider.pagination = PaginationOptions.from_dict(
        {
            "type": "url_list",
            "urls": ["https://example.test/list?page=2"],
            "max_pages": 2,
            "request": {
                "playwright": {
                    "wait_for_selector": ".page-ready",
                    "scroll_strategy": {
                        "enabled": True,
                        "mode": "bottom",
                        "max_scrolls": 2,
                        "stop_selector": ".page-ready",
                    },
                }
            },
        }
    )
    backend = FakeRenderBackend(
        {
            "https://example.test/list?page=1": {
                "body": '<article class="result"><a class="title">A</a></article>',
                "selector_counts": {".video-card": 1},
            },
            "https://example.test/list?page=2": {
                "body": '<article class="result"><a class="title">B</a></article>',
                "selector_counts": {".page-ready": 1},
            },
        }
    )

    task = CrawlerEngine(
        store=FileStore(workspace_tmp_path),
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    ).run(spider, task_id="pw-pagination-override")

    assert task.status.value == "success"
    assert [call["page_role"] for call in backend.render_calls] == ["start", "pagination"]
    assert backend.render_calls[0]["strategy_source"] == "spider.playwright"
    assert backend.render_calls[1]["strategy_source"] == "pagination.request.playwright"
    assert backend.render_calls[1]["playwright_options"].wait_for_selector == ".page-ready"
    assert backend.render_calls[1]["playwright_options"].scroll_strategy.mode == "bottom"


def test_fake_render_backend_supports_scroll_revealed_content():
    backend = FakeRenderBackend(
        {
            "https://example.test/space": {
                "body": '<div class="loading">拼命加载中...</div>',
                "body_after_scroll": '<section class="grid"><article class="video-card"><a class="title">Loaded</a></article></section>',
                "required_scrolls": 2,
                "selector_counts": {},
                "selector_counts_after_scroll": {".video-card": 1},
            }
        }
    )
    fetcher = PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend)
    request = HttpRequest(
        method="GET",
        url="https://example.test/space",
        response_type="html",
        context=RequestContext(spider_id="demo", task_id="task", start_url="https://example.test/space", response_type="html", page_role="start"),
        playwright_options=PlaywrightOptions.from_dict(
            {
                "enabled": True,
                "scroll_strategy": {
                    "enabled": True,
                    "mode": "viewport",
                    "max_scrolls": 3,
                    "scroll_pause_ms": 10,
                    "stop_selector": ".video-card",
                },
            }
        ),
        playwright_strategy_source="request.playwright",
    )

    response = fetcher.fetch(request)

    assert "Loaded" in response.text
    assert response.metadata["playwright_readiness"]["scroll_count"] == 2
    assert response.metadata["playwright_readiness"]["strategy_source"] == "request.playwright"


def test_engine_playwright_attribute_selector_regression_keeps_video_urls(workspace_tmp_path):
    spider = _playwright_spider(
        unique_fields=["title", "video_url"],
        fields=[
            FieldRule(name="title", type="css", selector="a.title"),
            FieldRule(name="video_url", type="attr", selector='a[href*="/video/"]', attribute="href"),
        ],
    )
    backend = FakeRenderBackend(
        {
            "https://example.test/list": (
                '<article class="result">'
                '<a class="title" href="/detail/a">A</a>'
                '<a class="video-link" href="https://www.bilibili.com/video/BV001">Watch</a>'
                "</article>"
                '<article class="result">'
                '<a class="title" href="/detail/b">B</a>'
                '<a class="video-link" href="https://www.bilibili.com/video/BV002">Watch</a>'
                "</article>"
            )
        }
    )

    task = CrawlerEngine(
        store=FileStore(workspace_tmp_path),
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    ).run(spider, task_id="pw-attr-regression")

    records = FileStore(workspace_tmp_path).read_records("pw-attr-regression")
    assert task.status.value == "success"
    assert [record["video_url"] for record in records] == [
        "https://www.bilibili.com/video/BV001",
        "https://www.bilibili.com/video/BV002",
    ]
    assert not task.warnings


def test_fastapi_runs_playwright_spider_with_fake_renderer(workspace_tmp_path):
    backend = FakeRenderBackend({"https://example.test/list": '<article class="result"><a class="title">API Rendered</a></article>'})
    app = create_app(
        workspace_tmp_path,
        playwright_fetcher=PlaywrightFetcher(PlaywrightOptions(enabled=True, browser_pool_size=1), backend=backend),
    )
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    results_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/results")

    response = run_route.endpoint({"spider": _playwright_spider().to_dict(), "task_id": "api-pw-task"})

    assert response["status"] == "success"
    assert results_route.endpoint("api-pw-task")[0]["title"] == "API Rendered"


def test_cli_run_local_playwright_fixture(workspace_tmp_path, capsys):
    exit_code = main(["run", "examples/playwright_local_fixture.json", "--data-dir", str(workspace_tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["records_count"] == 1
