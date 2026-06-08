import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from crawler_platform.api import create_app


class AsgiResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.content = body
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class AsgiTestClient:
    def __init__(self, app):
        self.app = app

    def get(self, url, *, headers=None):
        return asyncio.run(self._request("GET", url, headers=headers))

    async def _request(self, method, url, *, headers=None):
        parsed = urlsplit(url)
        path = parsed.path or "/"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": [(b"accept", b"text/html")] + [(key.lower().encode("ascii"), str(value).encode("utf-8")) for key, value in (headers or {}).items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }
        sent = False
        status_code = None
        response_headers = {}
        chunks = []

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(status_code, response_headers, b"".join(chunks))


def _client(workspace_tmp_path):
    pytest.importorskip("fastapi")
    return AsgiTestClient(create_app(workspace_tmp_path))


def _admin_root():
    return Path(__file__).resolve().parents[1] / "src" / "crawler_platform" / "web" / "admin"


def _asset_text(name):
    return (_admin_root() / "assets" / name).read_text(encoding="utf-8")


def test_admin_entry_and_static_assets_are_served(workspace_tmp_path):
    client = _client(workspace_tmp_path)

    page = client.get("/admin")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert 'id="view"' in page.text
    assert "Crawler Platform Admin" in page.text
    assert "{ok,data,error,meta}" not in page.text

    page_slash = client.get("/admin/")
    assert page_slash.status_code == 200
    assert "Crawler Platform Admin" in page_slash.text

    for path, expected in [
        ("/admin/assets/app.js", "text/javascript"),
        ("/admin/assets/api.js", "text/javascript"),
        ("/admin/assets/components.js", "text/javascript"),
        ("/admin/assets/styles.css", "text/css"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert expected in response.headers["content-type"]
        assert response.text


def test_admin_static_resources_are_local_and_relative():
    html = (_admin_root() / "index.html").read_text(encoding="utf-8")
    javascript = "\n".join(_asset_text(name) for name in ["api.js", "components.js", "app.js"])
    css = _asset_text("styles.css")
    all_text = "\n".join([html, javascript, css])

    assert "cdn" not in all_text.lower()
    assert "unpkg" not in all_text.lower()
    assert "jsdelivr" not in all_text.lower()
    assert "cdnjs" not in all_text.lower()
    assert re.search(r"https?://", all_text) is None
    assert "C:\\" not in all_text
    assert "fetch(`${API_BASE}${path}`" in javascript
    assert 'src="/admin/assets/app.js?v=20260608b"' in html
    assert 'href="/admin/assets/styles.css?v=20260608b"' in html


def test_admin_javascript_covers_required_api_paths():
    javascript = "\n".join(_asset_text(name) for name in ["api.js", "components.js", "app.js"])
    required_paths = [
        "/runtime/info",
        "/runtime/capabilities",
        "/runtime/storage",
        "/storage/health",
        "/spiders",
        "/spiders/validate",
        "/tasks/run",
        "/tasks",
        "/tasks/${encodeURIComponent(selectedId)}/results",
        "/scheduler/schedules",
        "/scheduler/run-due",
        "/scheduler/enqueue-due",
        "/worker/jobs",
        "/worker/run-once",
        "/worker/run-until-empty",
        "/worker/recover",
        "/worker/stats",
        "/worker/dead-letters",
        "/sessions",
        "/sessions/events",
        "/observability/logs",
        "/observability/metrics",
        "/observability/reports/tasks",
        "/observability/reports/jobs",
        "/observability/reports/scheduler",
        "/observability/traces",
        "/exports",
        "/exports/tasks",
        "/exports/jobs",
        "/exports/scheduler",
        "/exports/observability/logs",
    ]
    for path in required_paths:
        assert path in javascript


def test_admin_first_use_and_quickstart_controls_exist():
    app = _asset_text("app.js")
    html = (_admin_root() / "index.html").read_text(encoding="utf-8")
    styles = _asset_text("styles.css")

    assert 'id="languageToggleButton"' in html
    assert "Start here" in app
    assert "Try local example" in app
    assert "quickstartExampleIds" in app
    assert "Quickstart examples" in app
    assert "save-example-spider" in app
    assert "run-selected-example" in app
    assert "crawler-platform-admin-locale" in app
    assert "toggleLocale" in app
    assert "spiderStartUrl" in app
    assert "run-spider-with-start-url" in app
    assert "apply-spider-start-url" in app
    assert "start_url_help" in app
    assert 'setActiveView("exports")' in app
    assert "export_created" in app
    assert 'apiPost("/spiders"' in app
    assert 'apiPost("/tasks/run"' in app
    assert "run-local-api-example" in app
    assert "open-latest-task" in app
    assert "export-latest-task" in app
    assert "No tasks yet - run a local example first." in app
    assert "No exports yet - select a task and export it." in app
    assert ".start-here" in styles
    assert ".seed-runner" in styles


def test_admin_envelope_and_json_editor_helpers_exist():
    api_js = _asset_text("api.js")
    components_js = _asset_text("components.js")

    assert "export function unwrapEnvelope" in api_js
    assert "payload.ok === false" in api_js
    assert "throw new ApiClientError" in api_js
    assert "***REDACTED***" in api_js
    assert "export function parseJsonEditor" in components_js
    assert "Invalid JSON" in components_js
    assert "export function renderPager" in components_js


def test_admin_summary_panels_and_selection_states_exist():
    app = _asset_text("app.js")
    components_js = _asset_text("components.js")
    styles = _asset_text("styles.css")

    assert "Task Summary" in app
    assert "Schedule Summary" in app
    assert "Worker Queue Summary" in app
    assert "Worker Job Summary" in app
    assert "Storage Summary" in app
    assert "Snapshot Recovery" in app
    assert "Session Summary" in app
    assert "Example Summary" in app
    assert "Spider Summary" in app
    assert "Export Summary" in app
    assert "Selected Log" in app
    assert "Metrics Snapshot" in app
    assert "state.selectedLog" in app
    assert "observabilityRowId" in app
    assert "renderObservabilityPayload" in app
    assert "renderExportSummary" in app
    assert "rememberObservabilityInputs" in app
    assert "rememberExportInputs" in app
    assert "selected-row" in components_js
    assert "raw-json-collapsible" in components_js
    assert ".summary-panel" in styles
    assert ".panel-stack" in styles
    assert ".compact-input-row" in styles
    assert ".selected-row" in styles


def test_admin_openapi_and_api_envelope_remain_intact(workspace_tmp_path):
    client = _client(workspace_tmp_path)

    schema = client.get("/openapi.json").json()
    assert "ApiResponse" in schema["components"]["schemas"]
    assert "/admin" not in schema["paths"]
    assert {"/runtime/info", "/spiders", "/tasks", "/worker/jobs", "/exports"}.issubset(schema["paths"])

    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["data"]["database"]["enabled"] is False
    assert "request_id" in health["meta"]
