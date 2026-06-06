import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from crawler_platform.api import create_app
from crawler_platform.cli import main
from crawler_platform.examples import TEMPLATE_FILES, get_example, list_examples, smoke_examples, validate_examples


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

    def post(self, url, *, json=None, headers=None):
        return asyncio.run(self._request("POST", url, json_body=json, headers=headers))

    async def _request(self, method, url, *, json_body=None, headers=None):
        parsed = urlsplit(url)
        path = parsed.path or "/"
        body = b""
        request_headers = [(b"accept", b"application/json")]
        for key, value in (headers or {}).items():
            request_headers.append((key.lower().encode("ascii"), str(value).encode("utf-8")))
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers.append((b"content-type", b"application/json"))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": request_headers,
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
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(status_code, response_headers, b"".join(chunks))


def _ok(response, status=200):
    assert response.status_code == status
    payload = response.json()
    assert payload["ok"] is True
    assert payload["error"] is None
    return payload["data"]


def test_examples_index_validate_templates_and_local_boundaries():
    payload = validate_examples()
    assert payload["valid"] is True
    examples = list_examples()
    ids = [entry["id"] for entry in examples]
    assert len(ids) == len(set(ids))
    assert {"local-api-json", "pagination-with-detail", "worker-api-job", "template-export"}.issubset(ids)
    assert all(not entry["requires_external_network"] for entry in examples)
    assert all((Path(entry["path"]).is_absolute() or (Path.cwd() / entry["path"]).exists()) for entry in examples)
    assert all((Path.cwd() / f"examples/templates/{name}").exists() for name in TEMPLATE_FILES)

    template_text = "\n".join((Path.cwd() / f"examples/templates/{name}").read_text(encoding="utf-8") for name in TEMPLATE_FILES)
    assert not re.search(r"\b(password|token|secret)\b", template_text, re.IGNORECASE)


def test_examples_detail_and_smoke_subset(workspace_tmp_path):
    detail = get_example("local-api-json")
    assert detail["config"]["id"] == "local-api-json-demo"
    assert "examples/fixtures/local_api_json.json" in detail["fixture_paths"]

    smoke = smoke_examples(workspace_tmp_path / "examples-smoke")
    assert smoke["valid"] is True
    statuses = {item["id"]: item["status"] for item in smoke["results"]}
    assert statuses["local-api-json"] == "success"
    assert statuses["worker-api-job"] == "success"
    assert statuses["export-task-results"] == "success"


def test_examples_cli_list_show_validate_smoke_and_copy(workspace_tmp_path, capsys):
    assert main(["examples", "list"]) == 0
    assert "local-api-json" in capsys.readouterr().out

    assert main(["examples", "show", "local-api-json", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["id"] == "local-api-json"
    assert shown["config"]["id"] == "local-api-json-demo"

    assert main(["examples", "validate"]) == 0
    assert "valid=True" in capsys.readouterr().out

    assert main(["examples", "smoke", "--data-dir", str(workspace_tmp_path / "cli-smoke")]) == 0
    assert "examples smoke valid=True" in capsys.readouterr().out

    target = workspace_tmp_path / "copied.json"
    assert main(["examples", "copy", "template-api-basic", "--to", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == "template-api-basic"


def test_examples_api_endpoints(workspace_tmp_path):
    client = AsgiTestClient(create_app(workspace_tmp_path))
    examples = _ok(client.get("/examples"))
    assert any(entry["id"] == "local-api-json" for entry in examples)
    detail = _ok(client.get("/examples/local-api-json"))
    assert detail["config"]["id"] == "local-api-json-demo"
    validation = _ok(client.post("/examples/validate", json={}))
    assert validation["valid"] is True
    smoke = _ok(client.post("/examples/smoke", json={"data_dir": str(workspace_tmp_path / "api-smoke")}))
    assert smoke["valid"] is True
    schema = client.get("/openapi.json").json()
    assert "/examples" in schema["paths"]
    tags = {tag for path in schema["paths"].values() for operation in path.values() if isinstance(operation, dict) for tag in operation.get("tags", [])}
    assert "examples" in tags


def test_web_admin_references_examples_api():
    index = Path("src/crawler_platform/web/admin/index.html").read_text(encoding="utf-8")
    app = Path("src/crawler_platform/web/admin/assets/app.js").read_text(encoding="utf-8")
    assert 'data-view="examples"' in index
    assert 'apiGet("/examples")' in app
    assert 'apiPost("/examples/validate"' in app
    assert 'apiPost("/examples/smoke"' in app


def test_examples_feature_does_not_add_database_dependencies():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    text += "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    assert not re.search(r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b", text, re.IGNORECASE)
