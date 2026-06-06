# Pagination

Feature 05 adds pagination to the HTTP/API engine while keeping the runtime
file-backed and dependency-light.

## Supported Types

- `none`: fetch each `start_urls` entry once.
- `page`: increment `page_param` from the current request value or page index.
- `offset`: increment `offset_param` by `page_size`, a `page_size_param` value,
  or the number of records extracted from the current page.
- `url_list`: fetch explicit `pagination.urls` after the start URL.
- `next_button`: extract the first HTML link from `next_selector` and
  `next_attribute`.
- `cursor`: follow `next_json_path` as a next URL, or use `cursor_json_path`
  as a token in `page_param` (default `cursor`).

`pagination.urls` can also provide concrete local fixture pages for `page` and
`offset` examples. The engine still validates and supports generated
page/offset requests; tests cover both generated query parameters and fixture
flows.

## Limits

- `max_pages` caps requests per start URL.
- `max_records` caps records saved for a task.
- Empty extracted pages stop the current pagination chain.
- Failed page requests are recorded as warnings when at least one request has
  already succeeded and `request.fail_fast` is false.

## Flow

For each start URL, the engine fetches the current page, parses the response,
passes the response to the Feature 03 extractor, writes enriched records to
FileStore JSONL, then builds the next page request from the configured
pagination rule.

This feature does not add detail-page following, Playwright rendering, proxy or
anti-bot systems, scheduling, workers, Web UI, or exporter completion.
