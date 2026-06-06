from __future__ import annotations

import json
import random
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from .http_client import FetchError, HttpRequest, HttpResponse, HttpStatusError
from .models import AntiBotConfig, ConcurrencyConfig, ProxyConfig, RateLimitConfig, RetryConfig, SpiderConfig, TaskRecord
from .session import SessionAuthError, SessionManager
from .storage import FileStore

FetchCallable = Callable[[HttpRequest], HttpResponse]
ParseCallable = Callable[[HttpResponse], Any]
SleepCallable = Callable[[float], None]
ClockCallable = Callable[[], float]


class TimeoutError(FetchError):
    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message, "timeout", url=url)


class ProxyError(FetchError):
    def __init__(self, message: str, *, url: str | None = None, proxy: str | None = None) -> None:
        super().__init__(message, "proxy", url=url, proxy=proxy)


class RateLimitError(FetchError):
    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message, "rate_limit", url=url)


class RenderError(FetchError):
    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message, "render", url=url)


class RetryExhaustedError(FetchError):
    def __init__(self, cause: Exception, *, request: HttpRequest, attempt: int, proxy: str | None = None) -> None:
        self.cause = cause
        summary = error_summary(cause, request=request, attempt=attempt, proxy=proxy, retryable=False)
        super().__init__(
            f"Retry exhausted for {request.url}: {summary['message']}",
            "retry_exhausted",
            url=request.url,
            status_code=summary.get("status_code"),
            attempt=attempt,
            proxy=proxy,
            retryable=False,
        )


@dataclass(slots=True)
class AttemptSummary:
    url: str
    attempt: int
    success: bool
    error_type: str | None = None
    message: str | None = None
    status_code: int | None = None
    delay_seconds: float = 0
    proxy: str | None = None
    user_agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "attempt": self.attempt,
            "success": self.success,
            "error_type": self.error_type,
            "message": self.message,
            "status_code": self.status_code,
            "delay_seconds": self.delay_seconds,
            "proxy": self.proxy,
            "user_agent": self.user_agent,
        }


@dataclass(slots=True)
class RequestExecutionResult:
    request: HttpRequest
    response: HttpResponse
    parsed: Any = None
    attempts: list[AttemptSummary] = field(default_factory=list)


@dataclass(slots=True)
class ProxyState:
    proxy: str
    failures: int = 0
    cooldown_until: float = 0
    successes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy": self.proxy,
            "failures": self.failures,
            "cooldown_until": self.cooldown_until,
            "successes": self.successes,
        }


class ProxyManager:
    def __init__(self, config: ProxyConfig, *, base_dir: str | Path = ".", clock: ClockCallable | None = None) -> None:
        self.config = config
        self.clock = clock or time.monotonic
        self.proxies = _load_proxies(config, Path(base_dir))
        self.states = {proxy: ProxyState(proxy) for proxy in self.proxies}
        self._lock = threading.Lock()
        self._next_index = 0
        self._sticky: dict[str, str] = {}
        self._random = random.Random(config.random_seed)

    def select(self, request: HttpRequest, spider: SpiderConfig, task: TaskRecord) -> str | None:
        if request.proxy:
            return request.proxy
        if not self.config.enabled or not self.proxies:
            return None
        with self._lock:
            available = self._available_proxies()
            if not available:
                return None
            if self.config.mode == "random":
                return self._random.choice(available)
            if self.config.mode == "sticky":
                key = _sticky_key(self.config.sticky_key, request, spider, task)
                current = self._sticky.get(key)
                if current in available:
                    return current
                selected = available[0]
                self._sticky[key] = selected
                return selected
            selected = available[self._next_index % len(available)]
            self._next_index += 1
            return selected

    def report_success(self, proxy: str | None) -> None:
        if not proxy or proxy not in self.states:
            return
        with self._lock:
            state = self.states[proxy]
            state.failures = 0
            state.cooldown_until = 0
            state.successes += 1

    def report_failure(self, proxy: str | None) -> None:
        if not proxy or proxy not in self.states:
            return
        with self._lock:
            state = self.states[proxy]
            state.failures += 1
            if state.failures >= max(1, self.config.fail_threshold):
                state.cooldown_until = self.clock() + max(0, self.config.cooldown_seconds)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "mode": self.config.mode,
                "states": [state.to_dict() for state in self.states.values()],
            }

    def _available_proxies(self) -> list[str]:
        now = self.clock()
        return [proxy for proxy in self.proxies if self.states[proxy].cooldown_until <= now]


class AntiBotPolicy:
    def __init__(self, config: AntiBotConfig, *, base_dir: str | Path = ".", sleep: SleepCallable | None = None) -> None:
        self.config = config
        self.base_dir = Path(base_dir)
        self.sleep = sleep or time.sleep
        self._random = random.Random(config.random_seed)
        self._agent_index = 0
        self._headers_index = 0
        self._cookie_file_cache: dict[str, str] | None = None
        self.previous_url: str | None = None

    def apply(self, request: HttpRequest) -> HttpRequest:
        if not self.config.enabled:
            return request
        headers: dict[str, str] = {}
        selected_pool_headers = self._next_headers()
        headers.update(selected_pool_headers)
        headers.update(request.headers)
        user_agent = headers.get("User-Agent") or self._next_user_agent()
        if user_agent:
            headers["User-Agent"] = user_agent
        referer = self._referer_for(request.url)
        if referer and "Referer" not in headers:
            headers["Referer"] = referer

        cookies = {}
        cookies.update(self.config.cookies)
        cookies.update(self._cookie_file())
        cookies.update(request.cookies)

        delay = self._delay_seconds()
        if delay > 0:
            self.sleep(delay)
        return replace(request, headers=headers, cookies=cookies)

    def mark_success(self, response: HttpResponse) -> None:
        self.previous_url = response.final_url or response.url

    def _next_user_agent(self) -> str | None:
        if not self.config.user_agents:
            return None
        value = self.config.user_agents[self._agent_index % len(self.config.user_agents)]
        self._agent_index += 1
        return value

    def _next_headers(self) -> dict[str, str]:
        if not self.config.headers_pool:
            return {}
        headers = self.config.headers_pool[self._headers_index % len(self.config.headers_pool)]
        self._headers_index += 1
        return dict(headers)

    def _delay_seconds(self) -> float:
        if not self.config.random_delay:
            return 0
        minimum = max(0, float(self.config.min_delay_seconds))
        maximum = max(minimum, float(self.config.max_delay_seconds))
        if minimum == maximum:
            return minimum
        return self._random.uniform(minimum, maximum)

    def _referer_for(self, url: str) -> str | None:
        if not self.previous_url or self.config.referer_policy == "none":
            return None
        if self.config.referer_policy == "previous_url":
            return self.previous_url
        if self.config.referer_policy == "origin":
            parsed = urllib.parse.urlsplit(self.previous_url)
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        return None

    def _cookie_file(self) -> dict[str, str]:
        if self._cookie_file_cache is not None:
            return dict(self._cookie_file_cache)
        if not self.config.cookie_file:
            self._cookie_file_cache = {}
            return {}
        path = Path(self.config.cookie_file)
        if not path.is_absolute():
            path = self.base_dir / path
        if not path.exists():
            self._cookie_file_cache = {}
            return {}
        text = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            cookies = {str(key): str(value) for key, value in payload.items()}
        elif isinstance(payload, list):
            cookies = {str(item["name"]): str(item["value"]) for item in payload if isinstance(item, dict) and "name" in item and "value" in item}
        else:
            cookies = {}
            for line in text.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    cookies[key.strip()] = value.strip()
        self._cookie_file_cache = cookies
        return dict(cookies)


class RateLimiter:
    def __init__(self, config: RateLimitConfig, *, sleep: SleepCallable | None = None, clock: ClockCallable | None = None) -> None:
        self.config = config
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, url: str) -> float:
        if not self.config.enabled or self.config.requests_per_second <= 0:
            return 0
        key = _rate_key(url, self.config.per_domain)
        interval = 1.0 / float(self.config.requests_per_second)
        with self._lock:
            now = self.clock()
            next_allowed = self._next_allowed.get(key, now)
            delay = max(0.0, next_allowed - now)
            self._next_allowed[key] = max(now, next_allowed) + interval
        if delay > 0:
            self.sleep(delay)
        return delay


class ConcurrencyLimiter:
    def __init__(self, config: ConcurrencyConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._active: dict[str, int] = {}
        self.peak = 0

    def acquire(self, url: str) -> Callable[[], None]:
        if not self.config.enabled:
            return lambda: None
        key = _rate_key(url, self.config.per_domain)
        semaphore = self._semaphore(key)
        semaphore.acquire()
        with self._lock:
            self._active[key] = self._active.get(key, 0) + 1
            self.peak = max(self.peak, sum(self._active.values()))

        def release() -> None:
            with self._lock:
                self._active[key] = max(0, self._active.get(key, 1) - 1)
            semaphore.release()

        return release

    def _semaphore(self, key: str) -> threading.Semaphore:
        with self._lock:
            if key not in self._semaphores:
                self._semaphores[key] = threading.Semaphore(max(1, int(self.config.max_concurrent_requests)))
            return self._semaphores[key]


class RequestPipeline:
    def __init__(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        *,
        sleep: SleepCallable | None = None,
        clock: ClockCallable | None = None,
        base_dir: str | Path = ".",
        store: FileStore | None = None,
    ) -> None:
        self.spider = spider
        self.task = task
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.retry = spider.retry
        self.proxy_manager = ProxyManager(spider.proxy, base_dir=base_dir, clock=self.clock)
        self.anti_bot = AntiBotPolicy(spider.anti_bot, base_dir=base_dir, sleep=self.sleep)
        self.rate_limiter = RateLimiter(spider.rate_limit, sleep=self.sleep, clock=self.clock)
        self.concurrency = ConcurrencyLimiter(spider.concurrency)
        self.session = SessionManager(store or FileStore(base_dir), base_dir=base_dir) if spider.session.enabled else None
        self._session_login_bootstrapped = False
        self._random = random.Random(spider.retry.random_seed)
        self.attempts: list[AttemptSummary] = []

    def execute(self, request: HttpRequest, fetch: FetchCallable, parser: ParseCallable | None = None) -> RequestExecutionResult:
        attempts: list[AttemptSummary] = []
        max_attempts = max(1, int(self.retry.max_attempts if self.retry.enabled else 1))
        last_error: Exception | None = None
        last_request = request
        last_proxy: str | None = None

        for attempt in range(1, max_attempts + 1):
            proxy = self.proxy_manager.select(request, self.spider, self.task)
            last_proxy = proxy
            governed = self._prepare_request(request, proxy=proxy, attempt=attempt)
            last_request = governed
            release = self.concurrency.acquire(governed.url)
            delay = 0.0
            try:
                delay = self.rate_limiter.wait(governed.url)
                self._record_rate_delay(delay)
                governed, response = self._fetch_with_session(request, governed, fetch)
                if response.status_code < 200 or response.status_code >= 300:
                    raise HttpStatusError(response)
                parsed = parser(response) if parser is not None else None
                self.proxy_manager.report_success(proxy)
                self.anti_bot.mark_success(response)
                summary = AttemptSummary(
                    url=governed.url,
                    attempt=attempt,
                    success=True,
                    status_code=response.status_code,
                    delay_seconds=delay,
                    proxy=proxy,
                    user_agent=governed.headers.get("User-Agent"),
                )
                self._record_attempt(summary, attempts)
                if attempt > 1:
                    self.task.retry_successes += 1
                self._sync_task_snapshot()
                return RequestExecutionResult(request=governed, response=response, parsed=parsed, attempts=attempts)
            except Exception as exc:
                last_error = exc
                retryable = self._retryable(exc, attempt=attempt, max_attempts=max_attempts)
                self._report_proxy_failure(proxy, exc)
                summary_data = error_summary(exc, request=governed, attempt=attempt, proxy=proxy, retryable=retryable)
                summary = AttemptSummary(
                    url=governed.url,
                    attempt=attempt,
                    success=False,
                    error_type=summary_data["error_type"],
                    message=summary_data["message"],
                    status_code=summary_data.get("status_code"),
                    delay_seconds=delay,
                    proxy=proxy,
                    user_agent=governed.headers.get("User-Agent"),
                )
                self._record_attempt(summary, attempts)
                if retryable and attempt < max_attempts:
                    self.task.retry_attempts += 1
                    backoff_delay = self._backoff_delay(attempt)
                    if backoff_delay > 0:
                        self.sleep(backoff_delay)
                    continue
                if self.retry.enabled and (attempt > 1 or retryable or max_attempts > 1):
                    self.task.retry_failures += 1
                self._sync_task_snapshot()
                if self.retry.enabled and attempt >= max_attempts and last_error is not None and max_attempts > 1:
                    raise RetryExhaustedError(last_error, request=last_request, attempt=attempt, proxy=last_proxy) from last_error
                raise
            finally:
                release()
                self.task.concurrent_requests_peak = max(self.task.concurrent_requests_peak, self.concurrency.peak)

        if last_error is not None:
            raise RetryExhaustedError(last_error, request=last_request, attempt=max_attempts, proxy=last_proxy) from last_error
        raise FetchError(f"No request attempt executed for {request.url}", "request")

    def fetch_many(self, requests: list[HttpRequest], fetch: FetchCallable) -> list[RequestExecutionResult]:
        workers = min(max(1, self.spider.concurrency.max_concurrent_requests), max(1, len(requests)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(lambda req: self.execute(req, fetch), requests))

    def snapshot(self) -> dict[str, Any]:
        return {
            "retry": {
                "enabled": self.retry.enabled,
                "attempts": self.task.retry_attempts,
                "successes": self.task.retry_successes,
                "failures": self.task.retry_failures,
            },
            "rate_limit": {
                "enabled": self.spider.rate_limit.enabled,
                "waits": self.task.rate_limit_waits,
                "wait_seconds": self.task.rate_limit_wait_seconds,
            },
            "concurrency": {
                "enabled": self.spider.concurrency.enabled,
                "peak": self.task.concurrent_requests_peak,
            },
            "proxy": self.proxy_manager.snapshot(),
            "session": {
                "enabled": self.spider.session.enabled,
                "loads": self.task.session_loads,
                "saves": self.task.session_saves,
                "auth_check_failures": self.task.auth_check_failures,
                "login_flow_runs": self.task.login_flow_runs,
                "refresh_flow_runs": self.task.refresh_flow_runs,
            },
            "attempts": [attempt.to_dict() for attempt in self.attempts[-100:]],
        }

    def _prepare_request(self, request: HttpRequest, *, proxy: str | None, attempt: int) -> HttpRequest:
        governed = self.anti_bot.apply(request)
        context = governed.context
        if context is not None:
            context = replace(context, attempt=attempt, proxy=proxy)
        governed = replace(governed, proxy=proxy, context=context)
        if self.session is not None:
            governed = self.session.load_for_request(self.spider, self.task, request, governed)
        return governed

    def _fetch_with_session(self, original: HttpRequest, governed: HttpRequest, fetch: FetchCallable) -> tuple[HttpRequest, HttpResponse]:
        if self.session is not None and self.spider.session.login_flow.enabled and not self._session_login_bootstrapped:
            self.task.login_flow_runs += 1
            self.session.run_login_flow(self.spider, self.task, fetch, failed_request=governed, failed_response=None)
            self._session_login_bootstrapped = True
            governed = self._prepare_request(original, proxy=governed.proxy, attempt=governed.context.attempt if governed.context else 1)
        response = fetch(governed)
        if self.session is None:
            return governed, response
        self.session.save_from_response(self.spider, self.task, response)
        auth = self.session.check_authenticated(self.spider, self.task, response)
        if auth.authenticated:
            return governed, response
        self.session.recover_auth(self.spider, self.task, governed, response, fetch)
        retry_request = self._prepare_request(original, proxy=governed.proxy, attempt=governed.context.attempt if governed.context else 1)
        retry_response = fetch(retry_request)
        self.session.save_from_response(self.spider, self.task, retry_response)
        retry_auth = self.session.check_authenticated(self.spider, self.task, retry_response)
        if not retry_auth.authenticated:
            raise SessionAuthError(retry_auth.reason or "authentication check failed after session flow", url=retry_request.url)
        return retry_request, retry_response

    def _status_is_retryable(self, status_code: int) -> bool:
        return self.retry.enabled and status_code in set(self.retry.retry_on_status)

    def _retryable(self, exc: Exception, *, attempt: int, max_attempts: int) -> bool:
        if not self.retry.enabled or attempt >= max_attempts:
            return False
        error_type = normalized_error_type(exc)
        if error_type == "http_status":
            status = getattr(getattr(exc, "response", None), "status_code", getattr(exc, "status_code", None))
            return status in set(self.retry.retry_on_status) and "http_status" in set(self.retry.retry_on_errors)
        return error_type in set(self.retry.retry_on_errors)

    def _backoff_delay(self, attempt: int) -> float:
        if self.retry.backoff == "none":
            return 0
        base = max(0.0, float(self.retry.backoff_base_seconds))
        if self.retry.backoff == "fixed":
            delay = base
        elif self.retry.backoff == "exponential":
            delay = base * (2 ** max(0, attempt - 1))
        else:
            delay = 0
        if self.retry.backoff_max_seconds >= 0:
            delay = min(delay, float(self.retry.backoff_max_seconds))
        if self.retry.jitter and delay > 0:
            delay *= self._random.uniform(0.5, 1.5)
        return delay

    def _report_proxy_failure(self, proxy: str | None, exc: Exception) -> None:
        if normalized_error_type(exc) in {"network", "timeout", "http_status", "render", "proxy", "retry_exhausted"}:
            self.proxy_manager.report_failure(proxy)

    def _record_rate_delay(self, delay: float) -> None:
        if delay > 0:
            self.task.rate_limit_waits += 1
            self.task.rate_limit_wait_seconds += delay

    def _record_attempt(self, summary: AttemptSummary, attempts: list[AttemptSummary]) -> None:
        attempts.append(summary)
        self.attempts.append(summary)

    def _sync_task_snapshot(self) -> None:
        self.task.concurrent_requests_peak = max(self.task.concurrent_requests_peak, self.concurrency.peak)
        self.task.request_governance = self.snapshot()


def normalized_error_type(exc: Exception | None) -> str:
    if exc is None:
        return "unknown"
    error_type = getattr(exc, "error_type", None)
    if error_type in {"playwright_render", "render_error"}:
        return "render"
    if error_type:
        return str(error_type)
    if isinstance(exc, json.JSONDecodeError):
        return "parse"
    return "unknown"


def error_summary(exc: Exception, *, request: HttpRequest, attempt: int, proxy: str | None, retryable: bool | None) -> dict[str, Any]:
    if isinstance(exc, FetchError):
        status_code = getattr(exc, "status_code", None)
        if status_code is None and hasattr(exc, "response"):
            status_code = getattr(exc.response, "status_code", None)
        return {
            "error_type": normalized_error_type(exc),
            "message": str(exc),
            "url": getattr(exc, "url", None) or request.url,
            "status_code": status_code,
            "attempt": attempt,
            "proxy": proxy or getattr(exc, "proxy", None),
            "retryable": retryable,
        }
    return {
        "error_type": normalized_error_type(exc),
        "message": str(exc),
        "url": request.url,
        "status_code": None,
        "attempt": attempt,
        "proxy": proxy,
        "retryable": retryable,
    }


def _load_proxies(config: ProxyConfig, base_dir: Path) -> list[str]:
    proxies = [str(item).strip() for item in config.proxies if str(item).strip()]
    if config.proxy_file:
        path = Path(config.proxy_file)
        if not path.is_absolute():
            path = base_dir / path
        if path.exists():
            proxies.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for proxy in proxies:
        if proxy not in seen:
            seen.add(proxy)
            unique.append(proxy)
    return unique


def _sticky_key(config_key: str | None, request: HttpRequest, spider: SpiderConfig, task: TaskRecord) -> str:
    if config_key == "domain":
        return _domain(request.url)
    if config_key == "task":
        return task.id
    if config_key == "spider":
        return spider.id
    if config_key:
        return str(config_key)
    return _domain(request.url)


def _rate_key(url: str, per_domain: bool) -> str:
    return _domain(url) if per_domain else "global"


def _domain(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return parsed.netloc or parsed.path.split("/", 1)[0] or "local"
