from __future__ import annotations

import gzip
import json as jsonlib
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import PlaywrightOptions, RequestOptions


@dataclass(slots=True)
class RequestContext:
    spider_id: str
    task_id: str
    start_url: str
    response_type: str
    page_role: str = "start"
    attempt: int = 1
    proxy: str | None = None


@dataclass(slots=True)
class HttpRequest:
    method: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    body: str | bytes | None = None
    json: Any = None
    proxy: str | None = None
    timeout: float = 20
    encoding: str | None = None
    response_type: str = "html"
    follow_redirects: bool = True
    context: RequestContext | None = None
    storage_state: dict[str, Any] | None = None
    playwright_options: PlaywrightOptions | None = None
    playwright_strategy_source: str | None = None


@dataclass(slots=True)
class HttpResponse:
    url: str
    status_code: int
    body: str | bytes = ""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = ""
    final_url: str | None = None
    elapsed_ms: float = 0
    encoding: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if isinstance(self.body, bytes):
            return self.body.decode(self.encoding or "utf-8", errors="replace")
        return self.body

    @property
    def content(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        return self.body.encode(self.encoding or "utf-8")


FetchResponse = HttpResponse


class RequestBuildError(RuntimeError):
    pass


class FetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_type: str = "fetch",
        *,
        url: str | None = None,
        status_code: int | None = None,
        attempt: int | None = None,
        proxy: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.url = url
        self.status_code = status_code
        self.attempt = attempt
        self.proxy = proxy
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": str(self),
            "url": self.url,
            "status_code": self.status_code,
            "attempt": self.attempt,
            "proxy": self.proxy,
            "retryable": self.retryable,
        }


class HttpStatusError(FetchError):
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        super().__init__(f"HTTP {response.status_code} for {response.url}", "http_status", url=response.url, status_code=response.status_code)


class ResponseParseError(FetchError):
    def __init__(self, message: str, *, url: str, response_type: str) -> None:
        self.response_type = response_type
        super().__init__(message, "parse", url=url)


class UnsupportedResponseTypeError(ResponseParseError):
    pass


class Fetcher(Protocol):
    def fetch(self, request: HttpRequest) -> HttpResponse:
        ...


class HttpFetcher:
    def __init__(self) -> None:
        self._fixture_attempts: dict[str, int] = {}

    def fetch(self, request: HttpRequest) -> HttpResponse:
        if request.context and request.context.start_url and request.context.start_url != request.url:
            pass
        if _is_local_path(request.url):
            return self._fetch_local_file(request)
        started = time.perf_counter()
        url = _url_with_params(request.url, request.params)
        headers = dict(request.headers)
        if request.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in request.cookies.items())
        data = _request_body(request, headers)
        urllib_request = urllib.request.Request(url, data=data, headers=headers, method=request.method.upper())
        opener = urllib.request.build_opener(_proxy_handler(request.proxy)) if request.proxy else urllib.request.build_opener()
        try:
            with opener.open(urllib_request, timeout=request.timeout) as response:
                raw = _decode_http_body(response.read(), response.headers.get("Content-Encoding", ""))
                encoding = request.encoding or response.headers.get_content_charset() or "utf-8"
                return HttpResponse(
                    url=url,
                    final_url=response.geturl(),
                    status_code=response.status,
                    body=raw if request.response_type == "binary" else raw.decode(encoding, errors="replace"),
                    headers=dict(response.headers.items()),
                    content_type=response.headers.get("Content-Type", ""),
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    encoding=encoding,
                )
        except urllib.error.HTTPError as exc:
            raw = _decode_http_body(exc.read(), exc.headers.get("Content-Encoding", ""))
            encoding = request.encoding or exc.headers.get_content_charset() or "utf-8"
            response = HttpResponse(
                url=url,
                final_url=exc.geturl(),
                status_code=exc.code,
                body=raw.decode(encoding, errors="replace"),
                headers=dict(exc.headers.items()),
                content_type=exc.headers.get("Content-Type", ""),
                elapsed_ms=(time.perf_counter() - started) * 1000,
                encoding=encoding,
            )
            raise HttpStatusError(response) from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"Network error for {url}: {exc.reason}", "network", url=url) from exc

    def _fetch_local_file(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        path = _local_path(request.url)
        raw = path.read_bytes()
        encoding = request.encoding or "utf-8"
        fixture_response = self._fetch_sequence_fixture(request, path, raw, encoding, started)
        if fixture_response is not None:
            return fixture_response
        body: str | bytes = raw if request.response_type == "binary" else raw.decode(encoding, errors="replace")
        return HttpResponse(
            url=request.url,
            final_url=request.url,
            status_code=200,
            body=body,
            headers={},
            content_type=_content_type_for_path(path),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            encoding=encoding,
        )

    def _fetch_sequence_fixture(
        self,
        request: HttpRequest,
        path: Path,
        raw: bytes,
        encoding: str,
        started: float,
    ) -> HttpResponse | None:
        if path.suffix.lower() != ".json":
            return None
        try:
            payload = jsonlib.loads(raw.decode(encoding, errors="replace"))
        except jsonlib.JSONDecodeError:
            return None
        fixture = payload.get("_crawler_platform_fixture") if isinstance(payload, dict) else None
        if not isinstance(fixture, dict) or not isinstance(fixture.get("sequence"), list):
            return None
        key = str(path.resolve())
        attempt = self._fixture_attempts.get(key, 0)
        self._fixture_attempts[key] = attempt + 1
        sequence = fixture["sequence"]
        item = sequence[min(attempt, len(sequence) - 1)] if sequence else {}
        if not isinstance(item, dict):
            return None
        if item.get("error_type"):
            raise FetchError(str(item.get("message") or item["error_type"]), str(item["error_type"]), url=request.url)
        status_code = int(item.get("status_code", 200))
        body = item.get("body", "")
        content_type = str(item.get("content_type") or _content_type_for_path(path))
        headers = item.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}
        return HttpResponse(
            url=request.url,
            final_url=request.url,
            status_code=status_code,
            body=body if request.response_type != "binary" else str(body).encode(encoding),
            headers={str(key): str(value) for key, value in headers.items()},
            content_type=content_type,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            encoding=encoding,
        )


class HttpClient(HttpFetcher):
    def fetch(self, request: HttpRequest | str, options: RequestOptions | None = None) -> HttpResponse:
        if isinstance(request, str):
            if options is None:
                options = RequestOptions()
            request = build_http_request(request, options, response_type=options.response_type or "html")
        if request.context is None and request.method.upper() == "GET":
            pass
        if options and options.delay_seconds:
            time.sleep(options.delay_seconds)
        return super().fetch(request)


class FakeFetcher:
    def __init__(self, responses: dict[str, HttpResponse | str | bytes | Exception]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    def fetch(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        request_url = _url_with_params(request.url, request.params)
        value = self.responses.get(request_url, self.responses.get(request.url))
        if isinstance(value, Exception):
            raise value
        if isinstance(value, HttpResponse):
            return value
        if value is None:
            raise FetchError(f"No fake response for {request_url}", "network", url=request_url)
        return HttpResponse(url=request_url, final_url=request_url, status_code=200, body=value)


def build_http_request(
    url: str,
    options: RequestOptions,
    *,
    response_type: str,
    context: RequestContext | None = None,
    playwright_options: PlaywrightOptions | None = None,
    playwright_strategy_source: str | None = None,
) -> HttpRequest:
    method = options.method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise RequestBuildError(f"Unsupported HTTP method: {options.method}")
    headers = dict(options.headers)
    if options.user_agent:
        headers["User-Agent"] = options.user_agent
    return HttpRequest(
        method=method,
        url=url,
        params=dict(options.params),
        headers=headers,
        cookies=dict(options.cookies),
        body=options.body,
        json=options.json,
        proxy=options.proxy,
        timeout=options.timeout_seconds,
        encoding=options.encoding,
        response_type=response_type,
        follow_redirects=options.follow_redirects,
        context=context,
        storage_state=None,
        playwright_options=playwright_options,
        playwright_strategy_source=playwright_strategy_source,
    )


def parse_response(response: HttpResponse, response_type: str) -> Any:
    if response_type == "html":
        return response.text
    if response_type == "text":
        return response.text
    if response_type == "binary":
        return response.content
    if response_type == "json":
        try:
            return jsonlib.loads(response.text)
        except jsonlib.JSONDecodeError as exc:
            raise ResponseParseError(f"JSON parse failed for {response.url}: {exc}", url=response.url, response_type=response_type) from exc
    raise UnsupportedResponseTypeError(f"Unsupported response_type: {response_type}", url=response.url, response_type=response_type)


def join_url(base: str, maybe_relative: str | None) -> str | None:
    if not maybe_relative:
        return None
    return urllib.parse.urljoin(base, maybe_relative)


def _request_body(request: HttpRequest, headers: dict[str, str]) -> bytes | None:
    if request.json is not None:
        headers.setdefault("Content-Type", "application/json")
        return jsonlib.dumps(request.json).encode(request.encoding or "utf-8")
    if isinstance(request.body, bytes):
        return request.body
    if request.body is not None:
        return str(request.body).encode(request.encoding or "utf-8")
    return None


def _proxy_handler(proxy: str) -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


def _url_with_params(url: str, params: dict[str, Any]) -> str:
    if not params:
        return url
    separator = "&" if urllib.parse.urlsplit(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(params, doseq=True)}"


def _is_local_path(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "file" or (not parsed.scheme and Path(url).exists())


def _local_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(url)


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html"
    if suffix == ".json":
        return "application/json"
    if suffix == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _decode_http_body(raw: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or "").strip().lower()
    if not encoding:
        return raw
    if encoding == "gzip":
        return gzip.decompress(raw)
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw
