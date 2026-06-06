from __future__ import annotations

import json
import logging
import re
import urllib.parse
from collections.abc import Iterable, Iterator
from typing import Any

from .html_query import HTMLDocument, Node
from .models import FieldRule, SpiderConfig

logger = logging.getLogger(__name__)
MISSING = object()


class ExtractorError(ValueError):
    def __init__(self, message: str, *, field: str | None = None, rule_type: str | None = None) -> None:
        self.field = field
        self.rule_type = rule_type
        parts = [message]
        if field:
            parts.append(f"field={field}")
        if rule_type:
            parts.append(f"type={rule_type}")
        super().__init__("; ".join(parts))


class RequiredFieldMissingError(ExtractorError):
    pass


class TransformError(ExtractorError):
    pass


def extract_records(content: str, spider: SpiderConfig, *, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list(iter_records(content, spider, context=context))


def iter_records(content: str, spider: SpiderConfig, *, context: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    data = _try_json(content)
    if data is not None and spider.items_json_path:
        items = json_path(data, spider.items_json_path)
        if not isinstance(items, list):
            items = [items] if items is not None else []
        for item in items:
            yield extract_fields(item, spider.fields, context=context)
        return

    document = HTMLDocument(content)
    scopes: list[Node]
    if spider.item_selector:
        scopes = document.select(spider.item_selector)
    else:
        scopes = [document.root]
    for scope in scopes:
        yield extract_fields(scope, spider.fields, context=context)


def extract_fields(scope: Any, rules: list[FieldRule], *, strict: bool = True, context: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for rule in rules:
        try:
            value = extract_value(scope, rule, strict=strict, context=context)
        except ExtractorError:
            if strict:
                raise
            logger.debug("Extractor skipped field after error", extra={"field": rule.name, "type": rule.type})
            value = rule.default

        target = rule.namespace or rule.name
        if target in record and not rule.override:
            logger.debug("Extractor kept existing field because override is false", extra={"field": target})
            continue
        record[target] = value
    return record


def extract_value(scope: Any, rule: FieldRule, *, strict: bool = True, context: dict[str, Any] | None = None) -> Any:
    if rule.children:
        value = _extract_children(scope, rule, strict=strict, context=context)
    else:
        value = _extract_scalar(scope, rule)

    if _is_missing(value):
        default_from_transform = _default_if_empty_transform(rule)
        if default_from_transform is not MISSING:
            value = default_from_transform

    if _is_missing(value):
        value = rule.default

    if _is_missing(value):
        if rule.required:
            if strict:
                logger.warning("Required extractor field is missing", extra={"field": rule.name, "type": rule.type})
            raise RequiredFieldMissingError("required field is missing", field=rule.name, rule_type=rule.type)
        return None

    value = _apply_transforms(value, rule, context=context)
    if isinstance(value, list) and rule.join_with is not None:
        value = rule.join_with.join("" if item is None else str(item) for item in value)
    return value


def merge_records(base: dict[str, Any], detail: dict[str, Any], *, override: bool = True, namespace: str | None = None) -> dict[str, Any]:
    merged = dict(base)
    if namespace:
        existing = merged.get(namespace, {})
        if not isinstance(existing, dict):
            existing = {}
        merged[namespace] = merge_records(existing, detail, override=override)
        return merged
    for key, value in detail.items():
        if key in merged and not override:
            continue
        merged[key] = value
    return merged


def iter_field_values(scope: Any, rule: FieldRule) -> Iterator[Any]:
    value = extract_value(scope, rule)
    if isinstance(value, list):
        yield from value
    elif value is not None:
        yield value


def json_path(data: Any, path: str) -> Any:
    if not path or path == "$":
        return data
    tokens = [part for part in path.replace("$.", "").split(".") if part]
    return _walk_json_path(data, tokens)


def _extract_scalar(scope: Any, rule: FieldRule) -> Any:
    if rule.type == "json_path":
        value = json_path(scope, rule.json_path or rule.selector or "")
    elif rule.type == "css":
        value = _from_css(scope, rule)
    elif rule.type in {"attribute", "attr"}:
        attr_rule = FieldRule(
            name=rule.name,
            type="css",
            selector=rule.selector,
            attribute=rule.attribute,
            default=rule.default,
            many=rule.many,
        )
        value = _from_css(scope, attr_rule)
    elif rule.type == "xpath":
        value = _from_xpath(scope, rule)
    elif rule.type == "regex":
        value = _from_regex(scope, rule)
    else:
        logger.error("Unsupported extractor field type", extra={"field": rule.name, "type": rule.type})
        raise ExtractorError("unsupported field rule type", field=rule.name, rule_type=rule.type)
    return value


def _extract_children(scope: Any, rule: FieldRule, *, strict: bool, context: dict[str, Any] | None) -> Any:
    child_scopes = _select_child_scopes(scope, rule)
    if not child_scopes:
        return MISSING
    records = [extract_fields(child_scope, rule.children, strict=strict, context=context) for child_scope in child_scopes]
    return records if rule.many else records[0]


def _select_child_scopes(scope: Any, rule: FieldRule) -> list[Any]:
    if rule.type == "json_path":
        selected = json_path(scope, rule.json_path or rule.selector or "")
        if selected is None:
            return []
        return selected if isinstance(selected, list) else [selected]
    if rule.type == "css":
        return _as_document(scope).select(rule.selector)
    if rule.type == "xpath":
        return _as_document(scope).xpath(rule.selector or "")
    value = _extract_scalar(scope, rule)
    if isinstance(value, list):
        return value
    return [] if _is_missing(value) else [value]


def _walk_json_path(current: Any, tokens: list[str]) -> Any:
    if not tokens:
        return current
    raw_part = tokens[0]
    rest = tokens[1:]
    is_array = raw_part.endswith("[]")
    part = raw_part[:-2] if is_array else raw_part

    value = _json_get(current, part)
    if is_array:
        if not isinstance(value, list):
            return []
        if not rest:
            return value
        return [_walk_json_path(item, rest) for item in value]
    if isinstance(value, list) and rest:
        return [_walk_json_path(item, rest) for item in value]
    return _walk_json_path(value, rest)


def _json_get(current: Any, part: str) -> Any:
    if current is None:
        return None
    index_match = re.fullmatch(r"(.+)\[(\d+)\]", part)
    if index_match:
        container = _json_get(current, index_match.group(1))
        if isinstance(container, list):
            index = int(index_match.group(2))
            return container[index] if index < len(container) else None
        return None
    if isinstance(current, dict):
        return current.get(part)
    if isinstance(current, list):
        if part.isdigit():
            index = int(part)
            return current[index] if index < len(current) else None
        return [_json_get(item, part) for item in current]
    return None


def _from_css(scope: Any, rule: FieldRule) -> Any:
    document = _as_document(scope)
    nodes = document.select(rule.selector)
    values = [_node_value(node, rule.attribute) for node in nodes]
    values = [value for value in values if not _is_missing(value)]
    return values if rule.many else (values[0] if values else MISSING)


def _from_xpath(scope: Any, rule: FieldRule) -> Any:
    document = _as_document(scope)
    values = document.xpath(rule.selector or "")
    normalized = [item.text if isinstance(item, Node) else str(item) for item in values]
    normalized = [value for value in normalized if not _is_missing(value)]
    return normalized if rule.many else (normalized[0] if normalized else MISSING)


def _from_regex(scope: Any, rule: FieldRule) -> Any:
    text = _scope_text(scope)
    if not rule.pattern:
        return MISSING
    matches = list(re.finditer(rule.pattern, text, flags=re.S))
    if not matches:
        return MISSING
    values = [match.group(1) if match.groups() else match.group(0) for match in matches]
    return values if rule.many else values[0]


def _apply_transforms(value: Any, rule: FieldRule, *, context: dict[str, Any] | None = None) -> Any:
    result = value
    for transform in rule.transforms:
        result = _apply_transform(result, transform, rule, context=context)
    return result


def _apply_transform(value: Any, transform: Any, rule: FieldRule, *, context: dict[str, Any] | None = None) -> Any:
    transform_type = transform if isinstance(transform, str) else transform.get("type")
    options = {} if isinstance(transform, str) else transform
    try:
        if transform_type == "default_if_empty" and _is_missing(value):
            return options.get("value", options.get("default"))
        if isinstance(value, list) and transform_type not in {"join", "split", "first", "last", "list_index"}:
            return [_apply_transform(item, transform, rule, context=context) for item in value]
        if transform_type == "first":
            if isinstance(value, list):
                return value[0] if value else options.get("default", "")
            return value
        if transform_type == "last":
            if isinstance(value, list):
                return value[-1] if value else options.get("default", "")
            return value
        if transform_type == "list_index":
            if isinstance(value, list):
                index = int(options.get("index", options.get("value", 0)))
                return value[index] if -len(value) <= index < len(value) else options.get("default", "")
            return value
        if transform_type == "strip":
            return str(value).strip()
        if transform_type == "normalize_space":
            return " ".join(str(value).split())
        if transform_type == "lower":
            return str(value).lower()
        if transform_type == "upper":
            return str(value).upper()
        if transform_type == "title":
            return str(value).title()
        if transform_type == "int":
            return int(_number_text(value))
        if transform_type == "float":
            return float(_number_text(value))
        if transform_type == "number_parse":
            return _parse_number(value)
        if transform_type == "currency_parse":
            return _parse_number(str(value).replace("$", "").replace("￥", "").replace("¥", ""))
        if transform_type == "bool":
            return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
        if transform_type == "json":
            return json.loads(str(value))
        if transform_type == "html_to_text":
            return HTMLDocument(str(value)).root.text
        if transform_type == "regex_extract":
            match = re.search(str(options.get("pattern", "")), str(value), flags=re.S)
            if not match:
                return options.get("default", "")
            group = options.get("group", 1 if match.groups() else 0)
            return match.group(int(group)) if isinstance(group, int) or str(group).isdigit() else match.group(str(group))
        if transform_type == "default_if_empty":
            return value
        if transform_type == "urljoin":
            base_url = options.get("base_url") or (context or {}).get("source_url") or (context or {}).get("base_url")
            return urllib.parse.urljoin(str(base_url or ""), str(value)) if base_url else str(value)
        if transform_type == "canonical_url":
            return _canonical_url(str(value))
        if transform_type == "strip_query":
            parsed = urllib.parse.urlsplit(str(value))
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if transform_type == "keep_query":
            return _keep_query(str(value), options.get("keys"))
        if transform_type == "ensure_scheme":
            scheme = str(options.get("scheme", "https")).rstrip(":")
            text = str(value).strip()
            if text.startswith("//"):
                return f"{scheme}:{text}"
            return text if urllib.parse.urlsplit(text).scheme else f"{scheme}://{text}"
        if transform_type == "length":
            return len(value)
        if transform_type == "replace":
            return re.sub(str(options.get("pattern", "")), str(options.get("replacement", "")), str(value))
        if transform_type == "prefix":
            return f"{options.get('value', '')}{value}"
        if transform_type == "suffix":
            return f"{value}{options.get('value', '')}"
        if transform_type == "remove_prefix":
            prefix = str(options.get("value", ""))
            text = str(value)
            return text[len(prefix) :] if prefix and text.startswith(prefix) else text
        if transform_type == "remove_suffix":
            suffix = str(options.get("value", ""))
            text = str(value)
            return text[: -len(suffix)] if suffix and text.endswith(suffix) else text
        if transform_type == "split":
            separator = str(options.get("value", ","))
            return [part.strip() for part in str(value).split(separator) if part.strip()]
        if transform_type == "join":
            separator = str(options.get("value", rule.join_with or ""))
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
                return separator.join(str(item) for item in value)
            return str(value)
    except Exception as exc:
        logger.exception("Extractor transform failed", extra={"field": rule.name, "transform": transform_type})
        raise TransformError(str(exc), field=rule.name, rule_type=rule.type) from exc
    logger.warning("Unknown extractor transform ignored", extra={"field": rule.name, "transform": transform_type})
    return value


def _default_if_empty_transform(rule: FieldRule) -> Any:
    for transform in rule.transforms:
        transform_type = transform if isinstance(transform, str) else transform.get("type")
        if transform_type == "default_if_empty":
            options = {} if isinstance(transform, str) else transform
            return options.get("value", options.get("default"))
    return MISSING


def _number_text(value: Any) -> str:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        raise ValueError(f"not numeric: {value}")
    return match.group(0)


def _parse_number(value: Any) -> int | float:
    text = str(value).replace(",", "").strip()
    number = float(_number_text(text))
    multiplier = 1
    lowered = text.lower()
    if "亿" in text:
        multiplier = 100000000
    elif "万" in text:
        multiplier = 10000
    elif lowered.endswith("k"):
        multiplier = 1000
    elif lowered.endswith("m"):
        multiplier = 1000000
    elif lowered.endswith("b"):
        multiplier = 1000000000
    parsed = number * multiplier
    return int(parsed) if parsed.is_integer() else parsed


def _canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    path = urllib.parse.urljoin("/", parsed.path or "/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _keep_query(value: str, keys: Any) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if not keys:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    wanted = {str(item) for item in (keys if isinstance(keys, list) else [keys])}
    pairs = [(key, item) for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if key in wanted]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(pairs), ""))


def _as_document(scope: Any) -> HTMLDocument:
    if isinstance(scope, HTMLDocument):
        return scope
    if isinstance(scope, Node):
        return HTMLDocument(scope)
    return HTMLDocument(str(scope))


def _node_value(node: Node, attribute: str | None) -> str | None:
    if attribute:
        return node.attrs.get(attribute)
    return node.text


def _scope_text(scope: Any) -> str:
    if isinstance(scope, Node):
        return scope.text
    if isinstance(scope, (dict, list)):
        return json.dumps(scope, ensure_ascii=False)
    return str(scope)


def _try_json(content: str) -> Any | None:
    text = content.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _is_missing(value: Any) -> bool:
    return value is MISSING or value is None or value == "" or value == []
