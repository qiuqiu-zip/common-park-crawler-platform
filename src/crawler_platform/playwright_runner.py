from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .http_client import FetchError, FetchResponse, HttpRequest, HttpResponse
from .models import PlaywrightOptions, RequestOptions


class PlaywrightUnavailableError(FetchError):
    def __init__(self, message: str = "Playwright is not installed; install the playwright extra and browsers.") -> None:
        super().__init__(message, "playwright_unavailable")


class RenderBackend(Protocol):
    def open_browser(self, options: PlaywrightOptions):
        ...

    def render(self, browser, request: HttpRequest, options: PlaywrightOptions) -> FetchResponse:
        ...

    def close_browser(self, browser) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(slots=True)
class BrowserPoolStats:
    pool_size: int
    active: int
    idle: int
    opened: int
    reused: int
    requests: int
    failures: int


@dataclass(slots=True)
class _BrowserSlot:
    browser: object
    active: bool = False


class BrowserPool:
    def __init__(self, options: PlaywrightOptions, backend: RenderBackend | None = None) -> None:
        self.options = options
        self.backend = backend or PlaywrightRenderBackend()
        self.pool_size = max(1, int(options.browser_pool_size or 1))
        self._slots: list[_BrowserSlot] = []
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._opened = 0
        self._reused = 0
        self._requests = 0
        self._failures = 0
        self._closed = False

    def fetch(self, request: HttpRequest) -> FetchResponse:
        slot = self._acquire()
        try:
            response = self.backend.render(slot.browser, request, self.options)
            with self._lock:
                self._requests += 1
            return response
        except Exception:
            with self._lock:
                self._failures += 1
            raise
        finally:
            self._release(slot)

    def fetch_many(self, requests: list[HttpRequest]) -> list[FetchResponse]:
        workers = min(self.pool_size, max(1, len(requests)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self.fetch, requests))

    def stats(self) -> BrowserPoolStats:
        with self._lock:
            active = sum(1 for slot in self._slots if slot.active)
            return BrowserPoolStats(
                pool_size=self.pool_size,
                active=active,
                idle=len(self._slots) - active,
                opened=self._opened,
                reused=self._reused,
                requests=self._requests,
                failures=self._failures,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            slots = list(self._slots)
            self._slots.clear()
            self._closed = True
            self._condition.notify_all()
        for slot in slots:
            self.backend.close_browser(slot.browser)
        self.backend.close()

    def _acquire(self) -> _BrowserSlot:
        with self._condition:
            while True:
                if self._closed:
                    raise FetchError("browser pool is closed", "playwright_pool_closed")
                for slot in self._slots:
                    if not slot.active:
                        slot.active = True
                        self._reused += 1
                        return slot
                if len(self._slots) < self.pool_size:
                    browser = self.backend.open_browser(self.options)
                    slot = _BrowserSlot(browser=browser, active=True)
                    self._slots.append(slot)
                    self._opened += 1
                    return slot
                self._condition.wait(timeout=0.05)

    def _release(self, slot: _BrowserSlot) -> None:
        with self._condition:
            slot.active = False
            self._condition.notify()


class PlaywrightFetcher:
    def __init__(self, options: PlaywrightOptions, backend: RenderBackend | None = None) -> None:
        self.pool = BrowserPool(options, backend=backend)

    def fetch(self, request: HttpRequest) -> FetchResponse:
        try:
            return self.pool.fetch(request)
        except PlaywrightUnavailableError:
            if _is_local_path(request.url):
                return _fetch_local_rendered(request)
            raise

    def close(self) -> None:
        self.pool.close()

    def stats(self) -> BrowserPoolStats:
        return self.pool.stats()


class PlaywrightRenderBackend:
    def __init__(self) -> None:
        self._manager = None

    def open_browser(self, options: PlaywrightOptions):
        manager = self._manager_or_raise()
        return manager.chromium.launch(headless=options.headless)

    def render(self, browser, request: HttpRequest, options: PlaywrightOptions) -> FetchResponse:
        context = browser.new_context(
            user_agent=request.headers.get("User-Agent") if request.headers else None,
            extra_http_headers=request.headers or None,
            proxy={"server": request.proxy} if request.proxy else None,
            storage_state=request.storage_state if request.storage_state else None,
        )
        try:
            if request.cookies:
                context.add_cookies(_cookies_for_url(request.url, request.cookies))
            page = context.new_page()
            try:
                response = page.goto(request.url, wait_until=options.wait_until, timeout=int(request.timeout * 1000))
                readiness = _apply_page_readiness_controls(page, request.url, options)
                body = page.content()
                final_url = page.url
                status_code = response.status if response is not None else 200
                storage_state = context.storage_state()
                return HttpResponse(
                    url=request.url,
                    final_url=final_url,
                    status_code=status_code,
                    body=body,
                    content_type="text/html",
                    metadata={"storage_state": storage_state, "playwright_readiness": readiness},
                )
            except FetchError:
                raise
            except Exception as exc:
                raise FetchError(f"Playwright render failed for {request.url}: {exc}", "playwright_render", url=request.url) from exc
        finally:
            context.close()

    def close_browser(self, browser) -> None:
        browser.close()

    def close(self) -> None:
        if self._manager is not None:
            self._manager.stop()
            self._manager = None

    def _manager_or_raise(self):
        if self._manager is not None:
            return self._manager
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - exercised when optional dependency is absent
            raise PlaywrightUnavailableError() from exc
        self._manager = sync_playwright().start()
        return self._manager


def fetch_with_playwright(url: str, request: RequestOptions, playwright: PlaywrightOptions) -> FetchResponse:
    fetcher = PlaywrightFetcher(playwright)
    try:
        return fetcher.fetch(_request_from_options(url, request))
    finally:
        fetcher.close()


def _request_from_options(url: str, request: RequestOptions) -> HttpRequest:
    headers = dict(request.headers)
    if request.user_agent:
        headers["User-Agent"] = request.user_agent
    return HttpRequest(
        method=request.method,
        url=url,
        params=dict(request.params),
        headers=headers,
        cookies=dict(request.cookies),
        body=request.body,
        json=request.json,
        proxy=request.proxy,
        timeout=request.timeout_seconds,
        encoding=request.encoding,
        response_type=request.response_type or "html",
        storage_state=None,
    )


def _cookies_for_url(url: str, cookies: dict[str, str]) -> list[dict[str, str]]:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.hostname or "localhost"
    return [{"name": key, "value": value, "domain": domain, "path": "/"} for key, value in cookies.items()]


def _fetch_local_rendered(request: HttpRequest) -> FetchResponse:
    path = _local_path(request.url)
    raw = path.read_bytes()
    encoding = request.encoding or "utf-8"
    return HttpResponse(
        url=request.url,
        final_url=request.url,
        status_code=200,
        body=raw.decode(encoding, errors="replace"),
        headers={},
        content_type="text/html",
        elapsed_ms=0,
        encoding=encoding,
    )


def _is_local_path(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "file" or (not parsed.scheme and Path(url).exists())


def _local_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(url)


def _apply_page_readiness_controls(page, url: str, options: PlaywrightOptions) -> dict[str, object]:
    readiness: dict[str, object] = {
        "wait_until": options.wait_until,
        "wait_for_selector": options.wait_for_selector,
        "post_load_wait_ms": options.post_load_wait_ms,
        "scroll_strategy": {
            "enabled": options.scroll_strategy.enabled,
            "mode": options.scroll_strategy.mode,
            "max_scrolls": options.scroll_strategy.max_scrolls,
            "scroll_pause_ms": options.scroll_strategy.scroll_pause_ms,
            "stop_selector": options.scroll_strategy.stop_selector,
        },
    }
    if options.wait_for_selector:
        try:
            page.wait_for_selector(
                options.wait_for_selector,
                state="attached",
                timeout=int(options.wait_for_selector_timeout_ms),
            )
            readiness["wait_for_selector_matched"] = True
        except Exception as exc:
            raise FetchError(
                f"Playwright wait_for_selector timed out or failed for {url}: {options.wait_for_selector}",
                "playwright_render",
                url=url,
            ) from exc
    if options.post_load_wait_ms > 0:
        page.wait_for_timeout(int(options.post_load_wait_ms))
    readiness["scrolls_performed"] = _apply_scroll_strategy(page, url, options)
    return readiness


def _apply_scroll_strategy(page, url: str, options: PlaywrightOptions) -> int:
    strategy = options.scroll_strategy
    if not strategy.enabled or strategy.mode == "none":
        return 0
    scrolls = 0
    previous_state: tuple[int, int] | None = None
    for _ in range(max(0, int(strategy.max_scrolls))):
        if _selector_present(page, url, strategy.stop_selector):
            break
        state = _scroll_page(page, url, strategy.mode)
        scrolls += 1
        if strategy.scroll_pause_ms > 0:
            page.wait_for_timeout(int(strategy.scroll_pause_ms))
        if _selector_present(page, url, strategy.stop_selector):
            break
        current_state = (int(state.get("scroll_top", 0)), int(state.get("scroll_height", 0)))
        if previous_state is not None and current_state == previous_state:
            break
        previous_state = current_state
    return scrolls


def _selector_present(page, url: str, selector: str | None) -> bool:
    if not selector:
        return False
    try:
        return page.locator(selector).count() > 0
    except Exception as exc:
        raise FetchError(f"Playwright stop_selector failed for {url}: {selector}", "playwright_render", url=url) from exc


def _scroll_page(page, url: str, mode: str) -> dict[str, int]:
    try:
        return page.evaluate(
            """
            (scrollMode) => {
              const doc = document.documentElement || document.body;
              const body = document.body || document.documentElement;
              const viewportHeight = window.innerHeight || doc.clientHeight || 0;
              if (scrollMode === "bottom") {
                window.scrollTo(0, Math.max(doc.scrollHeight, body.scrollHeight));
              } else if (scrollMode === "incremental") {
                window.scrollBy(0, Math.max(Math.floor(viewportHeight / 2), 200));
              } else {
                window.scrollBy(0, viewportHeight || 800);
              }
              return {
                scroll_top: Math.floor(window.scrollY || doc.scrollTop || body.scrollTop || 0),
                scroll_height: Math.floor(Math.max(doc.scrollHeight || 0, body.scrollHeight || 0)),
                viewport_height: Math.floor(viewportHeight || 0),
              };
            }
            """,
            mode,
        )
    except Exception as exc:
        raise FetchError(f"Playwright scroll strategy failed for {url}: {mode}", "playwright_render", url=url) from exc


class FakeRenderBackend:
    def __init__(
        self,
        responses: dict[str, str | FetchResponse | Exception],
        *,
        delay_seconds: float = 0,
        storage_state_after_render: dict | None = None,
    ) -> None:
        self.responses = responses
        self.delay_seconds = delay_seconds
        self.storage_state_after_render = storage_state_after_render
        self.opened = 0
        self.closed = 0
        self.rendered_urls: list[str] = []
        self.storage_states: list[dict | None] = []
        self.launch_options: list[PlaywrightOptions] = []
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def open_browser(self, options: PlaywrightOptions):
        self.opened += 1
        self.launch_options.append(options)
        return {"browser": self.opened}

    def render(self, browser, request: HttpRequest, options: PlaywrightOptions) -> FetchResponse:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            self.rendered_urls.append(request.url)
            self.storage_states.append(request.storage_state)
            value = self.responses.get(request.url)
            if isinstance(value, Exception):
                raise value
            if isinstance(value, HttpResponse):
                if self.storage_state_after_render is not None and "storage_state" not in value.metadata:
                    value.metadata["storage_state"] = self.storage_state_after_render
                return value
            if value is None:
                raise FetchError(f"No fake rendered response for {request.url}", "network", url=request.url)
            metadata = {"storage_state": self.storage_state_after_render} if self.storage_state_after_render is not None else {}
            return HttpResponse(url=request.url, final_url=request.url, status_code=200, body=value, content_type="text/html", metadata=metadata)
        finally:
            with self._lock:
                self._active -= 1

    def close_browser(self, browser) -> None:
        self.closed += 1

    def close(self) -> None:
        pass
