from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable

from .extractor import json_path
from .http_client import FetchError, HttpRequest, HttpResponse
from .models import SessionConfig, SpiderConfig, TaskRecord
from .observability import log_event, record_metric, safe_observe, trace_id_from_task
from .storage import FileStore

FetchCallable = Callable[[HttpRequest], HttpResponse]


class SessionError(FetchError):
    def __init__(self, message: str, *, url: str | None = None, error_type: str = "session") -> None:
        super().__init__(message, error_type, url=url)


class SessionAuthError(SessionError):
    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message, url=url, error_type="auth")


@dataclass(slots=True)
class CookieJar:
    cookies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "CookieJar":
        if isinstance(payload, CookieJar):
            return cls(dict(payload.cookies))
        if isinstance(payload, dict):
            return cls({str(key): str(value) for key, value in payload.items()})
        if isinstance(payload, list):
            values: dict[str, str] = {}
            for item in payload:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    values[str(item["name"])] = str(item["value"])
            return cls(values)
        return cls()

    @classmethod
    def from_set_cookie_headers(cls, headers: dict[str, str]) -> "CookieJar":
        jar = cls()
        for key, value in headers.items():
            if key.lower() != "set-cookie":
                continue
            cookie = SimpleCookie()
            try:
                cookie.load(value)
            except Exception:
                continue
            for name, morsel in cookie.items():
                jar.cookies[str(name)] = str(morsel.value)
        return jar

    def merge(self, other: Any) -> "CookieJar":
        merged = dict(self.cookies)
        merged.update(CookieJar.from_payload(other).cookies)
        return CookieJar(merged)

    def to_dict(self) -> dict[str, str]:
        return dict(self.cookies)


@dataclass(slots=True)
class SessionProfile:
    profile_id: str
    account_ref: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionProfile":
        raw = dict(data)
        raw.setdefault("headers", {})
        raw.setdefault("metadata", {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AuthCheckResult:
    authenticated: bool
    check_type: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionManager:
    def __init__(self, store: FileStore, *, base_dir: str | Path = ".") -> None:
        self.store = store
        self.base_dir = Path(base_dir)

    def load_for_request(self, spider: SpiderConfig, task: TaskRecord, original: HttpRequest, governed: HttpRequest) -> HttpRequest:
        config = spider.session
        if not config.enabled or not config.load_before_request:
            return governed
        profile = self.ensure_profile(config)
        task.session_loads += 1
        session_cookies = self._load_cookies(config).to_dict()
        merged_cookies = dict(governed.cookies)
        merged_cookies.update(session_cookies)
        merged_cookies.update(original.cookies)
        headers = dict(profile.headers)
        headers.update(governed.headers)
        storage_state = self.load_storage_state(config) if _uses_playwright(spider) else None
        self.record_event(config, "session_loaded", task=task, metadata={"cookies": sorted(session_cookies), "storage_state": bool(storage_state)})
        return _replace_request(governed, headers=headers, cookies=merged_cookies, storage_state=storage_state)

    def save_from_response(self, spider: SpiderConfig, task: TaskRecord, response: HttpResponse) -> None:
        config = spider.session
        if not config.enabled or not config.persist or not config.save_after_request:
            return
        saved = False
        cookies = CookieJar.from_set_cookie_headers(response.headers)
        if cookies.cookies:
            existing = self._load_cookies(config)
            self._save_cookies(config, existing.merge(cookies).to_dict())
            task.session_saves += 1
            saved = True
        storage_state = response.metadata.get("storage_state") if isinstance(response.metadata, dict) else None
        if storage_state is not None:
            self.save_storage_state(config, storage_state)
            task.session_saves += 1
            saved = True
        if saved:
            self.record_event(config, "session_saved", task=task, metadata={"url": response.url, "headers": response.headers})

    def check_authenticated(self, spider: SpiderConfig, task: TaskRecord, response: HttpResponse) -> AuthCheckResult:
        config = spider.session
        check = config.auth_check
        if not config.enabled or not check.enabled:
            return AuthCheckResult(True, "disabled")
        result = _check_response_auth(response, check)
        if not result.authenticated:
            task.auth_check_failures += 1
            self.record_event(config, "auth_check_failed", task=task, metadata=result.to_dict())
        return result

    def recover_auth(self, spider: SpiderConfig, task: TaskRecord, failed_request: HttpRequest, failed_response: HttpResponse, fetch: FetchCallable) -> None:
        config = spider.session
        if config.refresh_flow.enabled:
            task.refresh_flow_runs += 1
            self.run_refresh_flow(spider, task, fetch, failed_request=failed_request, failed_response=failed_response)
            return
        if config.login_flow.enabled:
            task.login_flow_runs += 1
            self.run_login_flow(spider, task, fetch, failed_request=failed_request, failed_response=failed_response)
            return
        raise SessionAuthError(f"authentication check failed for {failed_request.url}", url=failed_request.url)

    def run_login_flow(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        fetch: FetchCallable,
        *,
        failed_request: HttpRequest | None = None,
        failed_response: HttpResponse | None = None,
    ) -> None:
        if self._run_flow("login_flow", spider, task, spider.session.login_flow.steps, fetch, failed_request=failed_request, failed_response=failed_response):
            task.session_saves += 1

    def run_refresh_flow(
        self,
        spider: SpiderConfig,
        task: TaskRecord,
        fetch: FetchCallable,
        *,
        failed_request: HttpRequest | None = None,
        failed_response: HttpResponse | None = None,
    ) -> None:
        if self._run_flow("refresh_flow", spider, task, spider.session.refresh_flow.steps, fetch, failed_request=failed_request, failed_response=failed_response):
            task.session_saves += 1

    def ensure_profile(self, config: SessionConfig) -> SessionProfile:
        profile_id = _profile_id(config)
        try:
            return SessionProfile.from_dict(self.store.get_session_profile(profile_id))
        except FileNotFoundError:
            now = _now()
            profile = SessionProfile(profile_id=profile_id, account_ref=config.account_ref, created_at=now, updated_at=now)
            self.store.save_session_profile(profile.to_dict())
            return profile

    def load_storage_state(self, config: SessionConfig) -> dict[str, Any] | None:
        path = _optional_path(config.storage_state, self.base_dir)
        if path is not None and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            return self.store.load_storage_state(_profile_id(config))
        except FileNotFoundError:
            return None

    def save_storage_state(self, config: SessionConfig, state: dict[str, Any]) -> None:
        self.store.save_storage_state(_profile_id(config), state)
        path = _optional_path(config.storage_state, self.base_dir)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_event(self, config: SessionConfig, event_type: str, *, task: TaskRecord | None = None, metadata: dict[str, Any] | None = None) -> None:
        profile_id = _profile_id(config)
        payload = {
            "event_id": uuid.uuid4().hex,
            "profile_id": profile_id,
            "event_type": event_type,
            "task_id": task.id if task else None,
            "spider_id": task.spider_id if task else None,
            "created_at": _now(),
            "metadata": redact_sensitive(metadata or {}),
        }
        self.store.record_session_event(payload)
        if task is not None:
            safe_observe(
                log_event,
                self.store,
                None,
                level="INFO" if event_type != "auth_check_failed" else "WARNING",
                component="session",
                event_type=event_type,
                message=f"Session {event_type} for {profile_id}",
                trace_id=trace_id_from_task(task),
                task_id=task.id,
                spider_id=task.spider_id,
                metadata=payload,
            )
            metric_name = {
                "session_loaded": "session_loads",
                "session_saved": "session_saves",
                "auth_check_failed": "auth_check_failures",
            }.get(event_type)
            if metric_name:
                safe_observe(
                    record_metric,
                    self.store,
                    None,
                    scope="tasks",
                    target_id=task.id,
                    name=metric_name,
                    value=1,
                    trace_id=trace_id_from_task(task),
                )

    def _run_flow(
        self,
        flow_name: str,
        spider: SpiderConfig,
        task: TaskRecord,
        steps: list[dict[str, Any]],
        fetch: FetchCallable,
        *,
        failed_request: HttpRequest | None,
        failed_response: HttpResponse | None,
    ) -> bool:
        config = spider.session
        self.ensure_profile(config)
        variables: dict[str, Any] = {}
        pending_cookies: dict[str, str] = {}
        pending_headers: dict[str, str] = {}
        last_response = failed_response
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise SessionError(f"{flow_name} step {index} must be an object", url=failed_request.url if failed_request else None)
            step_type = str(step.get("type") or "")
            if step_type == "request":
                request = _request_from_step(step, failed_request)
                request = _replace_request(request, cookies={**self._load_cookies(config).to_dict(), **pending_cookies}, headers={**pending_headers, **request.headers})
                last_response = fetch(request)
                if last_response.status_code < 200 or last_response.status_code >= 300:
                    raise SessionError(f"{flow_name} request step failed with HTTP {last_response.status_code}", url=request.url, error_type="auth_flow")
                response_cookies = CookieJar.from_set_cookie_headers(last_response.headers).to_dict()
                if response_cookies:
                    pending_cookies.update(response_cookies)
                continue
            if step_type == "extract":
                name = str(step.get("name") or step.get("target") or "")
                if not name:
                    raise SessionError(f"{flow_name} extract step requires name", url=failed_request.url if failed_request else None)
                variables[name] = _extract_step_value(step, last_response)
                continue
            if step_type == "set_cookie":
                name = str(step.get("name") or "")
                if not name:
                    raise SessionError(f"{flow_name} set_cookie step requires name", url=failed_request.url if failed_request else None)
                pending_cookies[name] = str(_value_from_step(step, variables))
                continue
            if step_type == "set_header":
                name = str(step.get("name") or "")
                if not name:
                    raise SessionError(f"{flow_name} set_header step requires name", url=failed_request.url if failed_request else None)
                pending_headers[name] = str(_value_from_step(step, variables))
                continue
            if step_type == "save_session":
                self._commit_flow_state(config, pending_cookies, pending_headers)
                continue
            raise SessionError(f"unsupported {flow_name} step type: {step_type}", url=failed_request.url if failed_request else None, error_type="auth_flow")
        self._commit_flow_state(config, pending_cookies, pending_headers)
        self.record_event(config, f"{flow_name}_completed", task=task, metadata={"steps": len(steps), "cookies": sorted(pending_cookies), "headers": sorted(pending_headers)})
        return bool(pending_cookies or pending_headers)

    def _commit_flow_state(self, config: SessionConfig, cookies: dict[str, str], headers: dict[str, str]) -> None:
        if cookies:
            existing = self._load_cookies(config)
            self._save_cookies(config, existing.merge(cookies).to_dict())
        if headers:
            profile = self.ensure_profile(config)
            profile.headers.update(headers)
            profile.updated_at = _now()
            self.store.save_session_profile(profile.to_dict())

    def _load_cookies(self, config: SessionConfig) -> CookieJar:
        cookies = CookieJar()
        path = _optional_path(config.cookie_file, self.base_dir)
        if path is not None and path.exists():
            cookies = cookies.merge(json.loads(path.read_text(encoding="utf-8")))
        try:
            cookies = cookies.merge(self.store.load_cookies(_profile_id(config)))
        except FileNotFoundError:
            pass
        return cookies

    def _save_cookies(self, config: SessionConfig, cookies: dict[str, str]) -> None:
        self.store.save_cookies(_profile_id(config), cookies)
        path = _optional_path(config.cookie_file, self.base_dir)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _check_response_auth(response: HttpResponse, check) -> AuthCheckResult:
    check_type = check.type
    if check_type == "status_code":
        authenticated = response.status_code == int(check.expected_status)
        return AuthCheckResult(authenticated, check_type, None if authenticated else f"expected status {check.expected_status}, got {response.status_code}")
    if check_type == "body_contains":
        needle = check.body_contains or str(check.expected_value or "")
        authenticated = needle in response.text
        return AuthCheckResult(authenticated, check_type, None if authenticated else f"body missing {needle!r}")
    if check_type == "body_not_contains":
        needle = check.body_not_contains or str(check.expected_value or "")
        authenticated = needle not in response.text
        return AuthCheckResult(authenticated, check_type, None if authenticated else f"body contains {needle!r}")
    if check_type == "header_exists":
        header = check.header or str(check.expected_value or "")
        lower = header.lower()
        authenticated = any(key.lower() == lower for key in response.headers)
        return AuthCheckResult(authenticated, check_type, None if authenticated else f"header missing {header!r}")
    if check_type == "json_path":
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            return AuthCheckResult(False, check_type, "response is not JSON")
        value = json_path(data, check.json_path or "")
        if check.expected_value is None:
            authenticated = value not in (None, False, "", [], {})
        else:
            authenticated = value == check.expected_value
        return AuthCheckResult(authenticated, check_type, None if authenticated else f"json_path {check.json_path!r} did not match")
    return AuthCheckResult(False, check_type, f"unsupported auth check type {check_type}")


def _request_from_step(step: dict[str, Any], failed_request: HttpRequest | None) -> HttpRequest:
    url = str(step.get("url") or (failed_request.url if failed_request else ""))
    if not url:
        raise SessionError("request step requires url", error_type="auth_flow")
    return HttpRequest(
        method=str(step.get("method", "GET")).upper(),
        url=url,
        params=dict(step.get("params", {})),
        headers={str(key): str(value) for key, value in dict(step.get("headers", {})).items()},
        cookies={str(key): str(value) for key, value in dict(step.get("cookies", {})).items()},
        body=step.get("body"),
        json=step.get("json"),
        timeout=float(step.get("timeout", failed_request.timeout if failed_request else 20)),
        response_type=str(step.get("response_type", "json")),
        context=failed_request.context if failed_request else None,
    )


def _extract_step_value(step: dict[str, Any], response: HttpResponse | None) -> Any:
    if response is None:
        return None
    source = str(step.get("source", "json"))
    if source == "header":
        header = str(step.get("header") or "")
        for key, value in response.headers.items():
            if key.lower() == header.lower():
                return value
        return None
    if source == "body":
        return response.text
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        data = {}
    return json_path(data, str(step.get("json_path") or step.get("path") or "$"))


def _value_from_step(step: dict[str, Any], variables: dict[str, Any]) -> Any:
    if "value" in step:
        return step["value"]
    ref = step.get("from") or step.get("from_var")
    if ref:
        return variables.get(str(ref), "")
    return ""


def _replace_request(
    request: HttpRequest,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    storage_state: dict[str, Any] | None = None,
) -> HttpRequest:
    return HttpRequest(
        method=request.method,
        url=request.url,
        params=dict(request.params),
        headers=dict(headers if headers is not None else request.headers),
        cookies=dict(cookies if cookies is not None else request.cookies),
        body=request.body,
        json=request.json,
        proxy=request.proxy,
        timeout=request.timeout,
        encoding=request.encoding,
        response_type=request.response_type,
        follow_redirects=request.follow_redirects,
        context=request.context,
        storage_state=storage_state if storage_state is not None else request.storage_state,
    )


def _optional_path(value: str | None, base_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _profile_id(config: SessionConfig) -> str:
    return str(config.profile or config.account_ref or "default")


def _uses_playwright(spider: SpiderConfig) -> bool:
    return spider.type == "playwright" or spider.playwright.enabled


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in {"session_id", "sessionid", "sid"} or any(token in lowered for token in ("password", "secret", "token", "authorization", "cookie"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
