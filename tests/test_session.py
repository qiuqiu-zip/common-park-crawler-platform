import re
from pathlib import Path

import pytest

from crawler_platform.api import create_app
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FetchError, HttpRequest, HttpResponse
from crawler_platform.models import (
    AntiBotConfig,
    AuthCheckConfig,
    FieldRule,
    PlaywrightOptions,
    RequestOptions,
    SessionConfig,
    SessionFlowConfig,
    SpiderConfig,
    TaskRecord,
    TaskStatus,
)
from crawler_platform.playwright_runner import FakeRenderBackend, PlaywrightFetcher
from crawler_platform.request_governance import RequestPipeline
from crawler_platform.session import CookieJar, SessionError, redact_sensitive
from crawler_platform.storage import FileStore
from crawler_platform.validation import validate_spider_config


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


def _html(title="A"):
    return f'<article class="item"><span class="title">{title}</span></article>'


def _html_spider(**overrides):
    spider = SpiderConfig(
        id=overrides.pop("id", "session-html"),
        name="Session HTML",
        type=overrides.pop("type", "http"),
        start_urls=overrides.pop("start_urls", ["https://example.test/list"]),
        item_selector="article.item",
        fields=[FieldRule(name="title", type="css", selector=".title")],
        unique_fields=["title"],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _session(profile="demo", **overrides):
    config = SessionConfig(enabled=True, profile=profile)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_session_config_validate_success():
    spider = _html_spider(
        session=_session(
            auth_check=AuthCheckConfig(enabled=True, type="status_code", expected_status=200),
            login_flow=SessionFlowConfig(enabled=True, steps=[{"type": "set_cookie", "name": "login", "value": "ok"}]),
        )
    )

    result = validate_spider_config(spider.to_dict())

    assert result.valid


def test_invalid_auth_check_type_reports_error():
    spider = _html_spider(session=_session(auth_check=AuthCheckConfig(enabled=True, type="not-real")))

    result = validate_spider_config(spider.to_dict())

    assert not result.valid
    assert any(issue.path == "session.auth_check.type" for issue in result.issues)


def test_cookie_jar_save_load_and_merge(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    jar = CookieJar({"a": "1"}).merge({"b": "2"})

    store.save_cookies("demo", jar.to_dict())
    merged = store.merge_cookies("demo", {"a": "override", "c": "3"})

    assert store.load_cookies("demo") == {"a": "override", "b": "2", "c": "3"}
    assert merged["c"] == "3"


def test_session_cookies_are_injected_into_http_request(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.save_cookies("demo", {"sid": "stored"})
    spider = _html_spider(session=_session())
    fetcher = SequenceFetcher({"https://example.test/list": [_html()]})

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="session-cookie")

    assert task.status == TaskStatus.SUCCESS
    assert fetcher.requests[0].cookies["sid"] == "stored"
    assert task.session_loads == 1


def test_explicit_cookies_override_session_and_antibot(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.save_cookies("demo", {"sid": "session", "shared": "session"})
    spider = _html_spider(
        request=RequestOptions(cookies={"sid": "explicit"}),
        anti_bot=AntiBotConfig(enabled=True, cookies={"sid": "anti", "bot": "yes", "shared": "anti"}),
        session=_session(),
    )
    fetcher = SequenceFetcher({"https://example.test/list": [_html()]})

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="cookie-priority")

    assert fetcher.requests[0].cookies == {"sid": "explicit", "bot": "yes", "shared": "session"}


def test_set_cookie_response_is_saved_to_session(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _html_spider(session=_session())
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [
                HttpResponse(url="https://example.test/list", final_url="https://example.test/list", status_code=200, body=_html(), headers={"Set-Cookie": "sid=abc; Path=/"})
            ]
        }
    )

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="set-cookie")

    assert task.session_saves == 1
    assert store.load_cookies("demo") == {"sid": "abc"}
    assert store.list_session_events("demo")


def test_session_profile_list_show_and_clear(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo", "headers": {"Authorization": "Bearer token"}})
    store.save_cookies("demo", {"sid": "abc"})

    assert store.list_session_profiles()[0]["profile_id"] == "demo"
    assert store.get_session_profile("demo")["headers"]["Authorization"] == "Bearer token"
    removed = store.delete_session_profile("demo")

    assert removed["removed"]
    assert store.list_session_profiles() == []


def test_auth_check_status_and_body_and_json_path(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    task = TaskRecord(id="auth", spider_id="auth")
    pipeline = RequestPipeline(_html_spider(session=_session(auth_check=AuthCheckConfig(enabled=True, type="body_contains", body_contains="ok"))), task, store=store)
    response = HttpResponse(url="https://example.test/list", status_code=200, body="ok")

    assert pipeline.session.check_authenticated(pipeline.spider, task, response).authenticated

    pipeline.spider.session.auth_check = AuthCheckConfig(enabled=True, type="json_path", json_path="logged_in", expected_value=True)
    json_response = HttpResponse(url="https://example.test/list", status_code=200, body='{"logged_in": true}')

    assert pipeline.session.check_authenticated(pipeline.spider, task, json_response).authenticated


def test_auth_check_failure_triggers_refresh_flow_and_retries(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _html_spider(
        session=_session(
            auth_check=AuthCheckConfig(enabled=True, type="status_code", expected_status=200),
            refresh_flow=SessionFlowConfig(
                enabled=True,
                steps=[
                    {"type": "request", "url": "https://example.test/refresh", "response_type": "json"},
                    {"type": "extract", "name": "token", "json_path": "token"},
                    {"type": "set_cookie", "name": "sid", "from": "token"},
                    {"type": "save_session"},
                ],
            ),
        )
    )
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [
                HttpResponse(url="https://example.test/list", status_code=401, body="expired"),
                HttpResponse(url="https://example.test/list", status_code=200, body=_html("Fresh")),
            ],
            "https://example.test/refresh": [HttpResponse(url="https://example.test/refresh", status_code=200, body='{"token":"fresh"}')],
        }
    )

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="refresh")

    assert task.status == TaskStatus.SUCCESS
    assert task.auth_check_failures == 1
    assert task.refresh_flow_runs == 1
    assert [request.url for request in fetcher.requests] == ["https://example.test/list", "https://example.test/refresh", "https://example.test/list"]
    assert fetcher.requests[-1].cookies["sid"] == "fresh"


def test_refresh_flow_failure_raises_structured_error(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _html_spider(
        session=_session(
            auth_check=AuthCheckConfig(enabled=True, type="status_code", expected_status=200),
            refresh_flow=SessionFlowConfig(enabled=True, steps=[{"type": "request", "url": "https://example.test/refresh"}]),
        )
    )
    task = TaskRecord(id="refresh-fail", spider_id=spider.id)
    pipeline = RequestPipeline(spider, task, store=store)
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [HttpResponse(url="https://example.test/list", status_code=401, body="expired")],
            "https://example.test/refresh": [HttpResponse(url="https://example.test/refresh", status_code=500, body="nope")],
        }
    )

    with pytest.raises(SessionError):
        pipeline.execute(HttpRequest("GET", "https://example.test/list"), fetcher.fetch)


def test_login_flow_runs_before_first_request(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _html_spider(
        session=_session(
            login_flow=SessionFlowConfig(
                enabled=True,
                steps=[
                    {"type": "request", "url": "https://example.test/login", "response_type": "json"},
                    {"type": "extract", "name": "token", "json_path": "token"},
                    {"type": "set_header", "name": "Authorization", "value": "Bearer fake"},
                    {"type": "set_cookie", "name": "sid", "from": "token"},
                    {"type": "save_session"},
                ],
            )
        )
    )
    fetcher = SequenceFetcher(
        {
            "https://example.test/login": [HttpResponse(url="https://example.test/login", status_code=200, body='{"token":"login-token"}')],
            "https://example.test/list": [_html()],
        }
    )

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="login")

    assert task.login_flow_runs == 1
    assert fetcher.requests[0].url == "https://example.test/login"
    assert fetcher.requests[1].cookies["sid"] == "login-token"
    assert fetcher.requests[1].headers["Authorization"] == "Bearer fake"


def test_playwright_storage_state_injected_and_saved(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.save_storage_state("demo", {"cookies": [{"name": "sid", "value": "old"}], "origins": []})
    spider = _html_spider(
        type="playwright",
        session=_session(storage_state=str(workspace_tmp_path / "external_state.json")),
        playwright=PlaywrightOptions(enabled=True, browser_pool_size=1),
    )
    backend = FakeRenderBackend(
        {"https://example.test/list": _html("Rendered")},
        storage_state_after_render={"cookies": [{"name": "sid", "value": "new"}], "origins": []},
    )
    fetcher = PlaywrightFetcher(spider.playwright, backend=backend)

    task = CrawlerEngine(store=store, playwright_fetcher=fetcher).run(spider, task_id="pw-session")

    assert task.status == TaskStatus.SUCCESS
    assert backend.storage_states[0]["cookies"][0]["value"] == "old"
    assert store.load_storage_state("demo")["cookies"][0]["value"] == "new"


def test_cli_session_list_show_clear_events(workspace_tmp_path, capsys):
    from crawler_platform.cli import main

    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.record_session_event({"event_id": "event-1", "profile_id": "demo", "event_type": "session_loaded"})

    assert main(["session", "list", "--data-dir", str(workspace_tmp_path)]) == 0
    assert "demo" in capsys.readouterr().out
    assert main(["session", "show", "demo", "--data-dir", str(workspace_tmp_path)]) == 0
    assert "storage_state=no" in capsys.readouterr().out
    assert main(["session", "events", "--profile-id", "demo", "--data-dir", str(workspace_tmp_path)]) == 0
    assert "event-1" in capsys.readouterr().out
    assert main(["session", "clear", "demo", "--data-dir", str(workspace_tmp_path)]) == 0


def test_fastapi_sessions_list_show_delete_events(workspace_tmp_path):
    pytest.importorskip("fastapi")
    app = create_app(workspace_tmp_path)
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.save_cookies("demo", {"sid": "abc"})
    store.record_session_event({"event_id": "event-1", "profile_id": "demo", "event_type": "session_saved"})

    list_route = next(route for route in app.routes if getattr(route, "path", None) == "/sessions" and "GET" in route.methods)
    show_route = next(route for route in app.routes if getattr(route, "path", None) == "/sessions/{profile_id}" and "GET" in route.methods)
    events_route = next(route for route in app.routes if getattr(route, "path", None) == "/sessions/events")
    delete_route = next(route for route in app.routes if getattr(route, "path", None) == "/sessions/{profile_id}" and "DELETE" in route.methods)

    assert list_route.endpoint()[0]["profile_id"] == "demo"
    assert show_route.endpoint("demo")["cookies"]["sid"] == "abc"
    assert events_route.endpoint(profile_id="demo")[0]["event_id"] == "event-1"
    assert delete_route.endpoint("demo")["profile_id"] == "demo"


def test_sensitive_fields_are_redacted_in_session_events(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _html_spider(session=_session())
    fetcher = SequenceFetcher(
        {
            "https://example.test/list": [
                HttpResponse(
                    url="https://example.test/list",
                    status_code=200,
                    body=_html(),
                    headers={"Set-Cookie": "sid=secret; Path=/", "Authorization": "Bearer raw-token"},
                )
            ]
        }
    )

    CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="redact")
    events_text = str(store.list_session_events("demo"))

    assert "raw-token" not in events_text
    assert redact_sensitive({"password": "x", "nested": {"access_token": "y"}})["password"] == "***REDACTED***"


def test_storage_check_and_snapshot_cover_sessions(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    store.save_session_profile({"profile_id": "demo"})
    store.save_cookies("demo", {"sid": "abc"})
    store.save_storage_state("demo", {"cookies": [], "origins": []})
    store.record_session_event({"event_id": "event-1", "profile_id": "demo", "event_type": "session_saved"})

    check = store.check_storage()
    manifest = store.create_snapshot("sessions")

    assert check["ok"]
    assert check["stats"]["session_profiles"] == 1
    assert "sessions" in manifest["included_paths"]


def test_runtime_has_no_database_dependency_references_for_session_feature():
    pattern = re.compile(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", re.I)
    paths = list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]

    matches = [str(path) for path in paths if pattern.search(path.read_text(encoding="utf-8"))]

    assert matches == []
