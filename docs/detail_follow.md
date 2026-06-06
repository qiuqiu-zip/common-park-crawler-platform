# Detail Following

Feature 06 adds detail-page and multilevel following to the HTTP/API engine. It
does not add Playwright rendering, proxy or anti-bot systems, scheduling,
workers, Web UI, exporter completion, retry features, or a database runtime.
Incremental dedup, watermark, checkpoint, and resume are covered separately in
`docs/incremental.md`.

## Detail Config

`detail.enabled` turns the feature on. When enabled, the config needs at least
one URL source:

- `url_field`: reads one URL or a list of URLs from each extracted parent
  record.
- `url_selector`: extracts links from the current HTML scope.
- `url_attr`: attribute used with `url_selector`, default `href`.
- `link_selector` and `link_attribute`: legacy aliases for `url_selector` and
  `url_attr`.

Detail pages use `detail.request` as their own request configuration. If
`detail.request.response_type` is omitted, detail responses default to `html`
for HTTP spiders and `json` for API spiders.

`detail.fields` are extracted with the existing Feature 03 extractor. The
extractor core is reused rather than rewritten.

## Merge Strategies

- `override`: detail fields are merged into the parent record and can replace
  parent keys.
- `namespace`: detail output is stored under `detail.namespace`, default
  `detail`.
- `keep_list`: all followed detail outputs are stored as a list under
  `detail.namespace`, default `details`.

## Multilevel

Nested follow rules live in `detail.details`. `max_depth` limits how far a
branch may recurse. `max_depth=1` fetches only the first detail level;
`max_depth=2` allows a child detail page.

Visited URLs are tracked per parent record, so cyclic detail links are skipped
and cannot recurse forever.

## URL Joining

Relative detail URLs are joined against the response URL that produced them.
Local fixture paths such as `examples/fixtures/list.html` can therefore link to
`detail_a.html` without network access.

## Failure Handling

Detail requests count toward task request counters. With `request.fail_fast`
false, failed detail pages add warnings and the parent record can still be
saved. With `request.fail_fast` true, a detail failure fails the task.

## Pagination Combination

Pagination runs first for each list page. Each page's records are then enriched
through the detail rules before being written to FileStore JSONL.

## Examples

```powershell
python -m crawler_platform.cli run examples/detail_follow.json --data-dir test-output/feature06-detail
python -m crawler_platform.cli run examples/multilevel_detail.json --data-dir test-output/feature06-multilevel
python -m crawler_platform.cli run examples/pagination_with_detail.json --data-dir test-output/feature06-pagination-detail
```

FastAPI accepts the same spider payload via `POST /tasks/run`; results are
available through `GET /tasks/{task_id}/results`.

The runtime remains file-backed and has no SQLite, MySQL, Postgres, SQLAlchemy,
psycopg, or ORM dependency.
