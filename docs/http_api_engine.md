# HTTP/API Engine

Feature 04 defined the request execution layer for `type=http` and `type=api`
spiders. Feature 05 added the pagination loop described in
`docs/pagination.md`. Feature 06 added detail-page following described in
`docs/detail_follow.md`. Feature 07 adds incremental crawling described in
`docs/incremental.md`. Feature 08 adds Playwright rendering and browser pools
described in `docs/playwright.md`. This engine still does not implement proxy
pools, anti-bot behavior, scheduling, workers, Web UI, or export enhancements.

## Responsibilities

The engine:

- loads a validated `SpiderConfig`
- creates and updates a FileStore task
- builds requests from `start_urls` and optional pagination rules
- executes requests through an injectable fetcher
- routes Playwright spiders through a browser-pool fetcher
- parses responses as `html`, `json`, `text`, or `binary`
- calls the Feature 03 extractor
- follows configured detail pages and merges detail records
- applies configured dedup and watermark rules
- writes result JSONL records to FileStore
- saves checkpoints for resume
- updates task request and record counters

## Request Configuration

`request` fields include:

- `method`: `GET`, `POST`, `PUT`, `PATCH`, or `DELETE`
- `url`: optional fallback URL when no `start_urls` are present
- `params`: query parameters
- `headers`: request headers
- `cookies`: cookie key/value pairs
- `body`: raw request body
- `json`: JSON body
- `timeout_seconds`: request timeout
- `encoding`: preferred response encoding
- `response_type`: `html`, `json`, `text`, or `binary`
- `follow_redirects`: currently uses the underlying urllib redirect behavior
- `fail_fast`: stop after the first failed request when true

`start_urls` are the entry URL list. `request` is shared configuration applied
to every entry. When both `start_urls` and `request.url` are present, each
`start_urls` value is used as the concrete URL and `request.url` is ignored.

## Response Types

- `type=http` defaults to `response_type=html`
- `type=api` defaults to `response_type=json`
- explicit `request.response_type` overrides the default

JSON parse failures raise `ResponseParseError`. Unsupported response types raise
`UnsupportedResponseTypeError`.

## Error Classification

- `RequestBuildError`: invalid request method or shape
- `FetchError`: network/fetch failure
- `HttpStatusError`: non-2xx response status
- `ResponseParseError`: response parse failure
- `UnsupportedResponseTypeError`: unsupported response type
- `CrawlerEngineError`: engine-level configuration failure

If all requests fail, the task status becomes `failed`. If at least one request
succeeds and `fail_fast` is false, the task can finish `success` with warnings
and failed request counters.

## Fetcher Testing

The engine accepts a fetcher implementing:

```python
def fetch(request: HttpRequest) -> HttpResponse:
    ...
```

Tests use `FakeFetcher`, so they never access the network. CLI fixture examples
use local files under `examples/fixtures/` for the same reason.

## FileStore Flow

The engine persists:

- spider config through `FileStore.save_spider`
- task state transitions: `pending -> running -> success/failed`
- extracted results to `results/<task_id>.jsonl`
- dedup hashes through FileStore hash indexes
- watermarks through FileStore watermark JSON files
- checkpoints through FileStore checkpoint JSON files

Each saved record includes extracted data plus:

- `source_url`
- `fetched_at`
- `response_status`
- `spider_id`
- `task_id`
- `unique_hash`
- `_dedup` when configured dedup is enabled

## No Database

Runtime code remains file-backed and does not import or execute SQL. Database
schema notes remain limited to `docs/schema.sql`.
