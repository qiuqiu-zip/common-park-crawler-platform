from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .models import SpiderConfig


def collect_request_urls(spider: SpiderConfig) -> list[str]:
    if spider.start_urls:
        return _dedupe_urls(list(spider.start_urls))
    if spider.request.url:
        return [str(spider.request.url)]
    return _dedupe_urls(collect_seed_urls(spider.seed))


def collect_seed_urls(seed: Any) -> list[str]:
    if seed is None:
        return []
    if isinstance(seed, list):
        return _coerce_urls(seed, "seed")
    if not isinstance(seed, dict):
        raise ValueError("seed must be an array or object")

    raw = dict(seed)
    seed_type = str(raw.get("type") or raw.get("kind") or "urllist").strip().lower().replace("-", "_")
    if seed_type in {"url_list", "urllist", "list", "sourcelist"}:
        return _coerce_urls(raw.get("urls") or raw.get("items") or raw.get("start_urls"), "seed.urls")

    if seed_type == "sitemap":
        source = _resolve_seed_source(raw, seed_type)
        return _coerce_urls(_load_sitemap_urls(source), "seed")

    if seed_type == "csv":
        source = _resolve_seed_source(raw, seed_type)
        return _load_csv_urls(
            source,
            column=_resolve_csv_column(raw),
            has_header=bool(raw.get("has_header", True)),
            delimiter=raw.get("delimiter") or ",",
        )

    if seed_type == "json":
        source = _resolve_seed_source(raw, seed_type)
        return _load_json_urls(source, field=raw.get("field") or raw.get("url_field") or "url")

    raise ValueError(f"unsupported seed type: {seed_type}")


def inspect_seed(seed: Any, *, max_urls: int | None = None) -> dict[str, Any]:
    urls = collect_seed_urls(seed)
    if max_urls is not None and max_urls > 0:
        urls = urls[:max_urls]
    return {
        "source_count": len(urls),
        "urls": urls,
        "source": _seed_summary(seed),
    }


def _seed_summary(seed: Any) -> dict[str, Any]:
    if isinstance(seed, list):
        return {"type": "url_list", "value_count": len(seed)}
    if not isinstance(seed, dict):
        return {"type": "invalid"}
    raw = dict(seed)
    source = _resolve_seed_source(raw, str(raw.get("type") or raw.get("kind") or "urllist")).replace("\\", "/")
    return {
        "type": str(raw.get("type") or raw.get("kind") or "urllist").strip().lower().replace("-", "_"),
        "source": source,
    }


def _coerce_urls(items: Any, path: str) -> list[str]:
    if not isinstance(items, list):
        raise ValueError(f"{path} must be a list of strings")
    values: list[str] = []
    for index, value in enumerate(items):
        if not isinstance(value, str):
            raise ValueError(f"{path}[{index}] must be a string")
        cleaned = value.strip()
        if cleaned:
            values.append(cleaned)
    return _dedupe_urls(values)


def _load_csv_urls(path: str, *, column: str, has_header: bool, delimiter: str) -> list[str]:
    text = _read_text_source(path)
    result: list[str] = []
    if len(delimiter) != 1:
        raise ValueError("seed.csv delimiter must be a single character")
    if has_header:
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(f"csv seed file requires column '{column}'")
        for row in reader:
            value = row.get(column)
            if value:
                result.append(value.strip())
        return _dedupe_urls(result)

    fallback = csv.reader(text.splitlines(), delimiter=delimiter)
    for row in fallback:
        if not row:
            continue
        value = row[0].strip()
        if value:
            result.append(value)
    return _dedupe_urls(result)


def _load_json_urls(path: str, *, field: str) -> list[str]:
    payload = _read_json_source(path)
    if isinstance(payload, list):
        return _extract_urls(payload, field=field)
    if isinstance(payload, dict):
        if field in payload and isinstance(payload[field], list):
            return _extract_urls(payload[field], field=field)
        if "urls" in payload and isinstance(payload["urls"], list):
            return _extract_urls(payload["urls"], field=field)
        candidate = payload.get("seed")
        if isinstance(candidate, list):
            return _extract_urls(candidate, field=field)
    raise ValueError("json seed source must be an array of url strings or a container with urls")


def _extract_urls(values: Any, *, field: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("json seed list must be an array")
    urls: list[str] = []
    for index, item in enumerate(values):
        if isinstance(item, str):
            urls.append(item.strip())
            continue
        if isinstance(item, dict):
            value = item.get("url") or item.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"json seed[{index}].{field} must be a string")
                urls.append(value.strip())
            continue
        raise ValueError("json seed items must be strings or objects with url field")
    return _dedupe_urls(urls)


def _load_sitemap_urls(path: str) -> list[str]:
    text = _read_text_source(path)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML in sitemap source: {exc}") from exc
    urls = []
    for element in root.findall(".//{*}loc"):
        if element.text:
            value = element.text.strip()
            if value:
                urls.append(value)
    if not urls:
        raise ValueError("sitemap source contains no <loc> entries")
    return _dedupe_urls(urls)


def _resolve_seed_source(seed: dict[str, Any], seed_type: str) -> str:
    raw_source = seed.get("source") or seed.get("path") or seed.get("url") or seed.get("file") or seed.get("uri")
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise ValueError(f"{seed_type} seed requires source/path/url/file/uri")
    return raw_source.strip()


def _resolve_csv_column(seed: dict[str, Any]) -> str:
    return str(seed.get("column") or seed.get("field") or "url").strip() or "url"


def _read_text_source(path: str) -> str:
    source = _resolve_source_path(path)
    if _is_http_url(source):
        with urllib.request.urlopen(source, timeout=10) as response:
            return response.read().decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


def _read_json_source(path: str) -> Any:
    text = _read_text_source(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON source: {exc}") from exc


def _resolve_source_path(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme:
        return value
    return str(Path(value))


def _is_http_url(value: str) -> bool:
    scheme = urllib.parse.urlsplit(value).scheme.lower()
    return scheme in {"http", "https", "file"}


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result
