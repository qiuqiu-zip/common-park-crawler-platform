from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

REDACTED = "***REDACTED***"
SENSITIVE_TOKENS = ("password", "secret", "token", "authorization", "cookie", "api_key")
SENSITIVE_EXACT_KEYS = {"sid", "session_id", "sessionid"}


def api_success(data: Any, *, request_id: str | None = None, trace_id: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload_meta = api_meta(request_id=request_id, trace_id=trace_id)
    if meta:
        payload_meta.update(meta)
    return {"ok": True, "data": redact_sensitive(data), "error": None, "meta": payload_meta}


def api_error_response(
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or []},
        "meta": api_meta(request_id=request_id, trace_id=trace_id),
    }


def api_meta(*, request_id: str | None = None, trace_id: str | None = None) -> dict[str, str]:
    selected_request_id = request_id or uuid.uuid4().hex
    return {
        "request_id": selected_request_id,
        "trace_id": trace_id or selected_request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def is_api_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and {"ok", "data", "error", "meta"}.issubset(payload.keys())


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = _redact_value_tree(item)
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _redact_value_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value_tree(item) for item in value]
    if value is None:
        return None
    return REDACTED


def apply_collection_query(items: Any, query: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    if not isinstance(items, list):
        return items, {}
    filtered = [_item for _item in items if _matches_filters(_item, query)]
    filtered = _sort_items(filtered, query.get("sort_by"), str(query.get("sort_order") or "asc"))
    total = len(filtered)
    offset = _non_negative_int(query.get("offset"), 0)
    limit = _non_negative_int(query.get("limit"), total)
    if limit < 0:
        limit = total
    page = filtered[offset:] if query.get("limit") in (None, "") else filtered[offset : offset + limit]
    effective_limit = len(page) if query.get("limit") in (None, "") else limit
    return page, {"pagination": {"total": total, "limit": effective_limit, "offset": offset, "has_more": offset + len(page) < total}}


def _matches_filters(item: Any, query: Mapping[str, Any]) -> bool:
    if not isinstance(item, dict):
        return True
    for field in ("status", "spider_id", "task_id", "job_id", "schedule_id"):
        selected = query.get(field)
        if selected in (None, ""):
            continue
        if str(item.get(field, "")) != str(selected):
            return False
    created_after = query.get("created_after")
    if created_after not in (None, "") and str(item.get("created_at") or item.get("started_at") or "") < str(created_after):
        return False
    created_before = query.get("created_before")
    if created_before not in (None, "") and str(item.get("created_at") or item.get("started_at") or "") > str(created_before):
        return False
    return True


def _sort_items(items: list[Any], sort_by: Any, sort_order: str) -> list[Any]:
    if not sort_by:
        return items
    reverse = sort_order.lower() == "desc"
    return sorted(items, key=lambda item: _sort_value(item, str(sort_by)), reverse=reverse)


def _sort_value(item: Any, field: str) -> Any:
    if not isinstance(item, dict):
        return ""
    value = item.get(field)
    return "" if value is None else value


def _non_negative_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_EXACT_KEYS or any(token in lowered for token in SENSITIVE_TOKENS)
