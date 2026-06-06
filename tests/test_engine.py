import gzip
import json
import urllib.request

import pytest

from crawler_platform.cli import main
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import (
    HttpFetcher,
    FakeFetcher,
    FetchError,
    HttpResponse,
    ResponseParseError,
    build_http_request,
    parse_response,
)
from crawler_platform.models import DetailOptions, FieldRule, PaginationOptions, RequestOptions, SpiderConfig, TaskStatus
from crawler_platform.storage import FileStore


def _html_spider(**request_overrides):
    return SpiderConfig(
        id="html-demo",
        name="HTML Demo",
        type="http",
        start_urls=["https://example.test/list"],
        item_selector="article.item",
        unique_fields=["title"],
        request=RequestOptions(**request_overrides),
        fields=[
            FieldRule(name="title", type="css", selector="a.title"),
            FieldRule(name="detail_path", type="attr", selector="a.title", attribute="href"),
        ],
    )


def _api_spider(**request_overrides):
    return SpiderConfig(
        id="api-demo",
        name="API Demo",
        type="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        unique_fields=["id"],
        request=RequestOptions(**request_overrides),
        fields=[
            FieldRule(name="id", type="json_path", json_path="id"),
            FieldRule(name="title", type="json_path", json_path="title"),
        ],
    )


def _detail_fields():
    return [FieldRule(name="body", type="css", selector=".body")]


class _FakeHeaders(dict):
    def get_content_charset(self):
        content_type = self.get("Content-Type", "")
        if "charset=" not in content_type:
            return None
        return content_type.split("charset=", 1)[1].split(";", 1)[0].strip()


class _FakeRemoteResponse:
    def __init__(self, body: bytes, *, headers: dict[str, str], url: str = "https://example.test/list", status: int = 200):
        self._body = body
        self.headers = _FakeHeaders(headers)
        self.status = status
        self._url = url

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeOpener:
    def __init__(self, response):
        self._response = response

    def open(self, request, timeout=0):
        return self._response


def test_build_get_request_merges_headers_cookies_params_timeout_and_response_type():
    options = RequestOptions(
        params={"q": "widgets"},
        headers={"X-Test": "yes"},
        cookies={"session": "abc"},
        user_agent="Agent",
        timeout_seconds=3,
        encoding="utf-8",
        response_type="json",
    )

    request = build_http_request("https://example.test/api", options, response_type="json")

    assert request.method == "GET"
    assert request.params == {"q": "widgets"}
    assert request.headers["X-Test"] == "yes"
    assert request.headers["User-Agent"] == "Agent"
    assert request.cookies == {"session": "abc"}
    assert request.timeout == 3
    assert request.encoding == "utf-8"
    assert request.response_type == "json"


def test_build_post_json_request():
    options = RequestOptions(method="POST", json={"name": "Widget"}, headers={"X-Test": "yes"})

    request = build_http_request("https://example.test/api", options, response_type="json")

    assert request.method == "POST"
    assert request.json == {"name": "Widget"}
    assert request.headers["X-Test"] == "yes"


def test_parse_response_json_and_invalid_json():
    assert parse_response(HttpResponse(url="u", status_code=200, body='{"ok": true}'), "json") == {"ok": True}

    with pytest.raises(ResponseParseError) as exc:
        parse_response(HttpResponse(url="https://example.test/api", status_code=200, body="not-json"), "json")

    assert exc.value.url == "https://example.test/api"


def test_http_fetcher_decompresses_gzip_html(monkeypatch):
    html = "<article class='item'><a class='title'>A</a></article>"
    gzipped = gzip.compress(html.encode("utf-8"))
    response = _FakeRemoteResponse(
        gzipped,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Encoding": "gzip",
        },
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: _FakeOpener(response))

    fetched = HttpFetcher().fetch(build_http_request("https://example.test/list", RequestOptions(), response_type="html"))

    assert fetched.text == html


def test_engine_runs_http_html_response_and_writes_metadata(workspace_tmp_path):
    fetcher = FakeFetcher(
        {
            "https://example.test/list": """
            <article class="item"><a class="title" href="/detail/1">A</a></article>
            <article class="item"><a class="title" href="/detail/2">B</a></article>
            """
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_html_spider(), task_id="task-1")
    records = store.read_records("task-1")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 1
    assert task.success_requests == 1
    assert task.total_records == 2
    assert task.saved_records == 2
    assert records[0]["source_url"] == "https://example.test/list"
    assert records[0]["spider_id"] == "html-demo"
    assert records[0]["task_id"] == "task-1"
    assert records[0]["response_status"] == 200


def test_engine_runs_api_json_response(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"},{"id":2,"title":"B"}]}'})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_api_spider(), task_id="task-2")

    assert task.status == TaskStatus.SUCCESS
    assert [record["title"] for record in store.read_records("task-2")] == ["A", "B"]
    assert fetcher.requests[0].response_type == "json"


def test_engine_runs_multiple_start_urls(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/a", "https://example.test/b"]
    fetcher = FakeFetcher(
        {
            "https://example.test/a": '<article class="item"><a class="title">A</a></article>',
            "https://example.test/b": '<article class="item"><a class="title">B</a></article>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="task-3")

    assert task.total_requests == 2
    assert task.success_requests == 2
    assert [record["title"] for record in store.read_records("task-3")] == ["A", "B"]


def test_non_2xx_response_marks_task_failed(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/list": HttpResponse(url="https://example.test/list", status_code=500, body="nope")})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_html_spider(), task_id="task-4")

    assert task.status == TaskStatus.FAILED
    assert task.failed_requests == 1
    assert task.error_type == "http_status"


def test_json_parse_failure_marks_task_failed(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/api": "not-json"})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_api_spider(), task_id="task-5")

    assert task.status == TaskStatus.FAILED
    assert task.error_type == "parse"


def test_network_error_marks_task_failed(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/api": FetchError("network down", "network", url="https://example.test/api")})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_api_spider(), task_id="task-6")

    assert task.status == TaskStatus.FAILED
    assert task.error_type == "network"


def test_partial_request_failure_keeps_success_with_counters(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/a", "https://example.test/b"]
    fetcher = FakeFetcher(
        {
            "https://example.test/a": '<article class="item"><a class="title">A</a></article>',
            "https://example.test/b": FetchError("network down", "network", url="https://example.test/b"),
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="task-7")

    assert task.status == TaskStatus.SUCCESS
    assert task.success_requests == 1
    assert task.failed_requests == 1
    assert task.saved_records == 1
    assert task.warnings


def test_default_response_type_for_http_and_api(workspace_tmp_path):
    html_fetcher = FakeFetcher({"https://example.test/list": '<article class="item"><a class="title">A</a></article>'})
    api_fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'})

    CrawlerEngine(store=FileStore(workspace_tmp_path / "html"), fetcher=html_fetcher).run(_html_spider(), task_id="html-task")
    CrawlerEngine(store=FileStore(workspace_tmp_path / "api"), fetcher=api_fetcher).run(_api_spider(), task_id="api-task")

    assert html_fetcher.requests[0].response_type == "html"
    assert api_fetcher.requests[0].response_type == "json"


def test_page_pagination_builds_page_requests_and_merges_records(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/list?page=1"]
    spider.pagination = PaginationOptions(type="page", page_param="page", max_pages=3)
    fetcher = FakeFetcher(
        {
            "https://example.test/list?page=1": '<article class="item"><a class="title">A</a></article>',
            "https://example.test/list?page=2": '<article class="item"><a class="title">B</a></article>',
            "https://example.test/list?page=3": '<article class="item"><a class="title">C</a></article>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="page-task")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 3
    assert [request.params.get("page") for request in fetcher.requests] == [None, 2, 3]
    assert [record["title"] for record in store.read_records("page-task")] == ["A", "B", "C"]


def test_offset_pagination_uses_page_size_and_stops_on_empty_page(workspace_tmp_path):
    spider = _api_spider()
    spider.start_urls = ["https://example.test/api?offset=0&limit=2"]
    spider.pagination = PaginationOptions(type="offset", offset_param="offset", page_size_param="limit", max_pages=4)
    fetcher = FakeFetcher(
        {
            "https://example.test/api?offset=0&limit=2": '{"items":[{"id":1,"title":"A"},{"id":2,"title":"B"}]}',
            "https://example.test/api?limit=2&offset=2": '{"items":[{"id":3,"title":"C"}]}',
            "https://example.test/api?limit=2&offset=4": '{"items":[]}',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="offset-task")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 3
    assert [request.params.get("offset") for request in fetcher.requests] == [None, 2, 4]
    assert [record["id"] for record in store.read_records("offset-task")] == [1, 2, 3]


def test_next_button_pagination_follows_html_link(workspace_tmp_path):
    spider = _html_spider()
    spider.pagination = PaginationOptions(type="next_button", next_selector="a.next", next_attribute="href", max_pages=2)
    fetcher = FakeFetcher(
        {
            "https://example.test/list": """
            <article class="item"><a class="title">A</a></article>
            <a class="next" href="https://example.test/list?page=2">Next</a>
            """,
            "https://example.test/list?page=2": '<article class="item"><a class="title">B</a></article>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="next-task")

    assert task.total_requests == 2
    assert [request.url for request in fetcher.requests] == ["https://example.test/list", "https://example.test/list?page=2"]
    assert [record["title"] for record in store.read_records("next-task")] == ["A", "B"]


def test_cursor_pagination_follows_json_next_url(workspace_tmp_path):
    spider = _api_spider()
    spider.pagination = PaginationOptions(type="cursor", next_json_path="next", max_pages=2)
    fetcher = FakeFetcher(
        {
            "https://example.test/api": '{"items":[{"id":1,"title":"A"}],"next":"https://example.test/api?cursor=abc"}',
            "https://example.test/api?cursor=abc": '{"items":[{"id":2,"title":"B"}],"next":null}',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="cursor-task")

    assert task.total_requests == 2
    assert [request.url for request in fetcher.requests] == ["https://example.test/api", "https://example.test/api?cursor=abc"]
    assert [record["id"] for record in store.read_records("cursor-task")] == [1, 2]


def test_pagination_max_records_stops_after_limit(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/list?page=1"]
    spider.pagination = PaginationOptions(type="page", page_param="page", max_pages=3, max_records=2)
    fetcher = FakeFetcher(
        {
            "https://example.test/list?page=1": """
            <article class="item"><a class="title">A</a></article>
            <article class="item"><a class="title">B</a></article>
            """,
            "https://example.test/list?page=2": '<article class="item"><a class="title">C</a></article>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="limit-task")

    assert task.total_requests == 1
    assert task.total_records == 2
    assert [record["title"] for record in store.read_records("limit-task")] == ["A", "B"]


def test_pagination_max_pages_one_stops_even_when_next_exists(workspace_tmp_path):
    spider = _html_spider()
    spider.pagination = PaginationOptions(type="next_button", next_selector="a.next", next_attribute="href", max_pages=1)
    fetcher = FakeFetcher(
        {
            "https://example.test/list": """
            <article class="item"><a class="title">A</a></article>
            <a class="next" href="https://example.test/list?page=2">Next</a>
            """,
            "https://example.test/list?page=2": '<article class="item"><a class="title">B</a></article>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="max-pages-task")

    assert task.total_requests == 1
    assert len(fetcher.requests) == 1
    assert [record["title"] for record in store.read_records("max-pages-task")] == ["A"]


def test_detail_disabled_does_not_fetch_detail_pages(workspace_tmp_path):
    fetcher = FakeFetcher({"https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>'})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(_html_spider(), task_id="detail-off-task")

    assert task.total_requests == 1
    assert len(fetcher.requests) == 1
    assert "body" not in store.read_records("detail-off-task")[0]


def test_detail_follow_from_url_field_merges_detail_fields(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<main><div class="body">Detail A</div></main>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-field-task")

    assert task.total_requests == 2
    assert task.success_requests == 2
    assert store.read_records("detail-field-task")[0]["body"] == "Detail A"


def test_detail_follow_from_selector_joins_relative_url(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(enabled=True, url_selector="a.title", url_attr="href", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="detail/1">A</a></article>',
            "https://example.test/detail/1": '<main><div class="body">Relative A</div></main>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-selector-task")

    assert [request.url for request in fetcher.requests] == ["https://example.test/list", "https://example.test/detail/1"]
    assert store.read_records("detail-selector-task")[0]["body"] == "Relative A"


def test_detail_keep_list_merges_multiple_detail_pages(workspace_tmp_path):
    spider = _html_spider()
    spider.fields = [
        FieldRule(name="title", type="css", selector="a.title"),
        FieldRule(name="detail_paths", type="attr", selector="a.detail", attribute="href", many=True),
    ]
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_paths",
        fields=[FieldRule(name="note", type="css", selector=".note")],
        merge_strategy="keep_list",
        namespace="detail_pages",
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": """
            <article class="item">
              <a class="title">A</a>
              <a class="detail" href="/detail/1">One</a>
              <a class="detail" href="/detail/2">Two</a>
            </article>
            """,
            "https://example.test/detail/1": '<div class="note">One</div>',
            "https://example.test/detail/2": '<div class="note">Two</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-list-task")
    record = store.read_records("detail-list-task")[0]

    assert task.total_requests == 3
    assert [item["note"] for item in record["detail_pages"]] == ["One", "Two"]


def test_detail_namespace_merge_strategy(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_path",
        fields=_detail_fields(),
        merge_strategy="namespace",
        namespace="detail",
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<div class="body">Namespaced</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-namespace-task")

    assert store.read_records("detail-namespace-task")[0]["detail"] == {"body": "Namespaced"}


def test_detail_override_merge_strategy_replaces_parent_field(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_path",
        fields=[FieldRule(name="title", type="css", selector=".title")],
        merge_strategy="override",
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">Summary Title</a></article>',
            "https://example.test/detail/1": '<h1 class="title">Detail Title</h1>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-override-task")

    assert store.read_records("detail-override-task")[0]["title"] == "Detail Title"


def test_detail_max_depth_one_does_not_follow_nested_detail(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_path",
        fields=[FieldRule(name="body", type="css", selector=".body"), FieldRule(name="more_path", type="attr", selector="a.more", attribute="href")],
        max_depth=1,
        details=[DetailOptions(enabled=True, url_field="more_path", fields=[FieldRule(name="extra", type="css", selector=".extra")])],
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<div class="body">A body</div><a class="more" href="/detail/extra">More</a>',
            "https://example.test/detail/extra": '<div class="extra">Extra</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-depth-one-task")

    assert len(fetcher.requests) == 2
    assert "extra" not in store.read_records("detail-depth-one-task")[0]


def test_detail_max_depth_two_follows_nested_detail(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_path",
        fields=[FieldRule(name="body", type="css", selector=".body"), FieldRule(name="more_path", type="attr", selector="a.more", attribute="href")],
        max_depth=2,
        details=[DetailOptions(enabled=True, url_field="more_path", fields=[FieldRule(name="extra", type="css", selector=".extra")])],
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<div class="body">A body</div><a class="more" href="/detail/extra">More</a>',
            "https://example.test/detail/extra": '<div class="extra">Extra</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-depth-two-task")

    assert len(fetcher.requests) == 3
    assert store.read_records("detail-depth-two-task")[0]["extra"] == "Extra"


def test_detail_cycle_url_does_not_recurse_forever(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(
        enabled=True,
        url_field="detail_path",
        fields=[FieldRule(name="body", type="css", selector=".body"), FieldRule(name="again", type="attr", selector="a.again", attribute="href")],
        max_depth=3,
        details=[DetailOptions(enabled=True, url_field="again", fields=[FieldRule(name="extra", type="css", selector=".body")])],
    )
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<div class="body">A body</div><a class="again" href="/detail/1">Again</a>',
        }
    )
    store = FileStore(workspace_tmp_path)

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-cycle-task")

    assert [request.url for request in fetcher.requests] == ["https://example.test/list", "https://example.test/detail/1"]


def test_detail_failure_warns_and_keeps_parent_when_fail_fast_false(workspace_tmp_path):
    spider = _html_spider()
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/missing">A</a></article>',
            "https://example.test/detail/missing": HttpResponse(url="https://example.test/detail/missing", status_code=404, body="missing"),
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-fail-soft-task")

    assert task.status == TaskStatus.SUCCESS
    assert task.failed_requests == 1
    assert task.warnings
    assert store.read_records("detail-fail-soft-task")[0]["title"] == "A"


def test_detail_failure_fails_task_when_fail_fast_true(workspace_tmp_path):
    spider = _html_spider(fail_fast=True)
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/missing">A</a></article>',
            "https://example.test/detail/missing": HttpResponse(url="https://example.test/detail/missing", status_code=404, body="missing"),
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="detail-fail-fast-task")

    assert task.status == TaskStatus.FAILED
    assert task.failed_requests == 1
    assert store.read_records("detail-fail-fast-task") == []


def test_pagination_with_detail_pages(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/list?page=1"]
    spider.pagination = PaginationOptions(type="page", page_param="page", max_pages=2)
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list?page=1": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/list?page=2": '<article class="item"><a class="title" href="/detail/2">B</a></article>',
            "https://example.test/detail/1": '<div class="body">Detail A</div>',
            "https://example.test/detail/2": '<div class="body">Detail B</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="page-detail-task")

    assert task.total_requests == 4
    assert [record["body"] for record in store.read_records("page-detail-task")] == ["Detail A", "Detail B"]


def test_multiple_start_urls_with_detail_pages(workspace_tmp_path):
    spider = _html_spider()
    spider.start_urls = ["https://example.test/list-a", "https://example.test/list-b"]
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list-a": '<article class="item"><a class="title" href="/detail/a">A</a></article>',
            "https://example.test/list-b": '<article class="item"><a class="title" href="/detail/b">B</a></article>',
            "https://example.test/detail/a": '<div class="body">Detail A</div>',
            "https://example.test/detail/b": '<div class="body">Detail B</div>',
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="multi-start-detail-task")

    assert task.total_requests == 4
    assert [record["body"] for record in store.read_records("multi-start-detail-task")] == ["Detail A", "Detail B"]


def test_cli_run_local_fixture(workspace_tmp_path, capsys):
    exit_code = main(["run", "examples/local_html_list.json", "--data-dir", str(workspace_tmp_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["records_count"] == 1


def test_fastapi_task_run_query_and_results_with_fake_fetcher(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    fetcher = FakeFetcher({"https://example.test/api": '{"items":[{"id":1,"title":"A"}]}'})
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    task_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}" and "GET" in route.methods)
    results_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/results")

    response = run_route.endpoint({"spider": _api_spider().to_dict(), "task_id": "api-task"})

    assert response["status"] == "success"
    assert task_route.endpoint("api-task")["status"] == "success"
    assert results_route.endpoint("api-task")[0]["title"] == "A"


def test_fastapi_task_run_pagination_with_fake_fetcher(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    spider = _api_spider()
    spider.start_urls = ["https://example.test/api?offset=0&limit=1"]
    spider.pagination = PaginationOptions(type="offset", offset_param="offset", page_size_param="limit", max_pages=2)
    fetcher = FakeFetcher(
        {
            "https://example.test/api?offset=0&limit=1": '{"items":[{"id":1,"title":"A"}]}',
            "https://example.test/api?limit=1&offset=1": '{"items":[{"id":2,"title":"B"}]}',
        }
    )
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    task_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}" and "GET" in route.methods)
    results_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/results")

    response = run_route.endpoint({"spider": spider.to_dict(), "task_id": "paged-api-task"})
    task_payload = task_route.endpoint("paged-api-task")
    results = results_route.endpoint("paged-api-task")

    assert response["status"] == "success"
    assert response["total_records"] == 2
    assert task_payload["total_requests"] == 2
    assert task_payload["success_requests"] == 2
    assert task_payload["saved_records"] == 2
    assert [record["title"] for record in results] == ["A", "B"]


def test_fastapi_task_run_detail_follow_with_fake_fetcher(workspace_tmp_path):
    pytest.importorskip("fastapi")
    from crawler_platform.api import create_app

    spider = _html_spider()
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=_detail_fields())
    fetcher = FakeFetcher(
        {
            "https://example.test/list": '<article class="item"><a class="title" href="/detail/1">A</a></article>',
            "https://example.test/detail/1": '<div class="body">Detail A</div>',
        }
    )
    app = create_app(workspace_tmp_path, fetcher=fetcher)
    run_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/run")
    task_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}" and "GET" in route.methods)
    results_route = next(route for route in app.routes if getattr(route, "path", None) == "/tasks/{task_id}/results")

    response = run_route.endpoint({"spider": spider.to_dict(), "task_id": "detail-api-task"})
    task_payload = task_route.endpoint("detail-api-task")
    results = results_route.endpoint("detail-api-task")

    assert response["status"] == "success"
    assert task_payload["total_requests"] == 2
    assert task_payload["success_requests"] == 2
    assert results[0]["body"] == "Detail A"
