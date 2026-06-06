# Configuration Guide

Spider configs use schema version `1.0` and canonical field names. Prefer
`type` over legacy mode aliases and `scheduler` over legacy schedule aliases.

## SpiderConfig

Core fields:

- `version`: schema version, defaulting to `1.0`.
- `id`: stable spider id.
- `name`: display name.
- `type`: `http`, `api`, or `playwright`.
- `start_urls`: entry fixture paths or URLs.
- `fields`: extraction rules.
- `unique_fields`: fields used for record hashes.

## RequestConfig

`request` controls method, params, headers, cookies, body, JSON payload,
encoding, timeout, response type, fail-fast behavior, retry, proxy, anti-bot,
rate limit, and concurrency.

## FieldConfig

Supported field types include `css`, `attribute`, `xpath`, `regex`,
`json_path`, and `attr`. Fields can use `required`, `default`, `many`,
`transforms`, `join_with`, `children`, `namespace`, and `override`.

## PaginationConfig

Pagination supports page numbers, offsets, explicit URL lists, next-link
selectors, cursors, `max_pages`, and `max_records`.

## DetailConfig

`detail` can follow a URL stored in a list record or discovered through a
selector, extract detail fields, and merge with `override`, `namespace`, or
`keep_list`.

## DedupConfig

`dedup` controls hash keys, scope, hash method, missing-key policy, and whether
existing records are skipped.

## Watermark

`watermark` stores incremental boundaries for string, numeric, or datetime
fields and can be paired with checkpoints.

## Retry / Proxy / AntiBot / RateLimit / Concurrency

Request governance settings live under request-related config sections and are
applied consistently to HTTP/API and optional Playwright fetches.

## CrawlPolicy

`crawl_policy` is optional. Old configs without this section keep existing
runtime behavior. When the section is present, `enabled` defaults to `true`.

Key fields:

- `robots.enabled`, `robots.mode`, `robots.unavailable_policy`, and local
  `robots.rules`
- `allowed_domains`, `denied_domains`, and `allow_cross_domain`
- `include_url_patterns` and `exclude_url_patterns`
- `normalize_url`, `remove_fragment`, `remove_tracking_params`, and
  `tracking_params`
- `max_requests`, `max_depth`, and `max_duration_seconds`

See `docs/crawl_policy.md` for defaults, warn/block behavior, and `plan`.

## Playwright

`playwright` controls rendered-page behavior, including headless mode, pool size,
wait settings, local rendered fixtures, and optional browser behavior.

## Scheduler

`scheduler` supports manual, interval, and cron schedules with timezone, misfire
policy, max instances, start time, and jitter.

## Worker

Worker behavior is configured through queue metadata and CLI/API enqueue
options. Worker jobs reuse the same SpiderConfig execution path.

## Lifecycle

Task and worker job lifecycle operations are service-level controls for pause,
resume, cancel, retry, rerun, events, and signals.

## Session

`session` supports profile ids, cookie files, storage state, login flow, refresh
flow, request steps, extract steps, header/cookie setting, and persistence.

## Observability

`observability` enables local logs, metrics, traces, record samples, redaction,
and run reports.

## Export

`export` controls output format, selected fields, excluded fields, flattening,
metadata, dedup data, redaction, and manifest behavior.

## Examples / Templates

Examples and templates are indexed by `examples/index.json`. Default runnable
examples must be offline and use local fixtures.
