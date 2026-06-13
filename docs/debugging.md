# Debugging

Use dry-run when a spider config needs selector, transform, or field quality
feedback before a formal crawl run.

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/debug-dry-run
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/debug-dry-run --json
python -m crawler_platform.cli debug dry-run examples/debug_transforms.json --data-dir test-output/debug-transforms --save-report
```

Dry-run defaults to the first `start_urls` entry, one page, at most five preview
records, and at most five sample values. It does not save spider configs, tasks,
formal results, hashes, watermarks, or checkpoints. It may write debug reports
under `debug_reports/` when `--save-report` is used, and it writes failure
artifacts under `debug_artifacts/tasks/<dry_run_id>/` when fetch, parse, or
extraction diagnostics fail.

## Selector And Local Extract

For smaller checks that should not fetch or save anything:

```powershell
python -m crawler_platform.cli debug selector examples/fixtures/crawl_policy_page.html --selector ".title" --selector-type css --json
python -m crawler_platform.cli debug selector examples/fixtures/crawl_policy_page.html --selector "//a" --selector-type xpath --json
python -m crawler_platform.cli debug extract examples/debug_extract_local.json --input-file examples/fixtures/debug_extract_items.json --json
```

`debug selector` reports `matched_count` and `sample_values` for one local HTML
file. `debug extract` runs the configured fields against one local HTML or JSON
file and returns `field_quality`, missing required fields, and sample records.
Neither command writes formal crawl results or accesses the network.

## Report Fields

The JSON report includes:

- `target_url`, `final_url`, `status_code`, `response_type`, and `duration_ms`
- `item_count` and `sample_records`
- `field_diagnostics` with field name, selector type, selector or path, attr,
  match count, missing count, required flag, raw sample, transformed sample,
  warnings, errors, and child field diagnostics
- `field_quality` with total records, non-empty count, empty count, missing
  rate, sample values, required flag, a human hint, and `ok`, `warning`,
  `failed`, or `unknown` status
- `artifact_path` and `report_path` when those files were written

When no records are extracted, `field_quality` now reports `status=unknown`
with a hint that completeness was not evaluated, instead of reporting a false
positive `ok`.

## Debug Artifacts

Failure artifacts are grouped by run id:

```text
data/debug_artifacts/tasks/<dry_run_id>/
  responses/
  failed_pages/
  screenshots/
  metadata.json
```

`metadata.json` records URL, final URL, status code, redacted request and
response headers, error type, message, response text prefix, and file paths for
captured response bodies. Sensitive request and cookie-like fields are redacted.
Screenshots are reserved for rendered-page fetchers.

For rendered pages, also inspect `response.metadata.playwright_readiness` in
task reports or debug output. It records which page role ran (`start`,
`pagination`, `detail`, `debug`), where the effective strategy came from,
whether `wait_for_selector` matched, how long that wait took, how many scrolls
ran, and the final DOM length collected by `page.content()`.

## Transforms

The extractor supports the original transforms plus these debugging-oriented
cleaning transforms:

- URL: `urljoin`, `canonical_url`, `strip_query`, `keep_query`, `ensure_scheme`
- Text and numeric cleanup: `currency_parse`, `number_parse`, `html_to_text`,
  `regex_extract`, `default_if_empty`, `remove_prefix`, `remove_suffix`

`urljoin` uses the current response URL as `source_url`, so relative links can
be previewed in dry-run and extracted during formal runs. `number_parse` handles
plain numbers, comma-separated numbers, and compact Chinese units such as
`1.2万`.

## Local Examples

All debugging examples are offline:

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/debug-dry-run
python -m crawler_platform.cli dry-run examples/debug_urljoin.json --data-dir test-output/debug-urljoin
python -m crawler_platform.cli dry-run examples/debug_transforms.json --data-dir test-output/debug-transforms
python -m crawler_platform.cli dry-run examples/debug_quality_report.json --data-dir test-output/debug-quality
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli validate examples/playwright_per_page_wait_local.json
python -m crawler_platform.cli validate examples/playwright_space_style_scroll_local.json
```
