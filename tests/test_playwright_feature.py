import json

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FetchError, HttpRequest
from crawler_platform.models import DetailOptions, FieldRule, PaginationOptions, PlaywrightOptions, SpiderConfig
from crawler_platform.playwright_runner import BrowserPool, FakeRenderBackend, PlaywrightFetcher
from crawler_platform.storage import FileStore
from crawler_platform.validation import validate_spider_config


def _request(url):
    return HttpRequest(method="GET", url=url, response_type="html")


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
    payload["playwright"] = {"enabled": True, "pool_size": 3, "headless": False, "wait_until": "load"}

    result = validate_spider_config(payload)
    spider = SpiderConfig.from_dict(payload)

    assert result.valid
    assert spider.playwright.browser_pool_size == 3
    assert spider.playwright.headless is False


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
