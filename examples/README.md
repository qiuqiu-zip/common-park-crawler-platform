# Crawler Platform Examples

This directory contains local, deterministic examples for the crawler platform.
Use `index.json` as the manifest and `templates/` as copy-ready starter configs.

Recommended first examples:

- `local-api-json`: smallest API/JSON path with a local fixture.
- `local-html-list`: smallest HTML/CSS extraction path with a local fixture.
- `pagination-page`: local pagination without external network access.

Useful commands:

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples list --quickstart
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir ./test-output/feature18-examples
python -m crawler_platform.cli examples copy template-http-basic --to ./my-spider.json
python -m crawler_platform.cli init spider --type api --output ./my-api-spider.json
```

Debug examples:

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir ./test-output/debug-dry-run
python -m crawler_platform.cli dry-run examples/debug_urljoin.json --data-dir ./test-output/debug-urljoin
python -m crawler_platform.cli dry-run examples/debug_transforms.json --data-dir ./test-output/debug-transforms
python -m crawler_platform.cli dry-run examples/debug_quality_report.json --data-dir ./test-output/debug-quality
```

All smoke examples use `examples/fixtures` and require no external network. The
default smoke subset covers basic HTTP/API, pagination, detail follow,
incremental dedup, scheduler, worker, session, observability, and export.

Templates:

- `templates/http_basic.json`
- `templates/api_basic.json`
- `templates/playwright_basic.json`
- `templates/pagination.json`
- `templates/detail_follow.json`
- `templates/incremental.json`
- `templates/scheduler.json`
- `templates/worker_job.json`
- `templates/session.json`
- `templates/observability.json`
- `templates/export.json`

See `docs/examples.md` for the manifest schema, API endpoints, Web admin
reference, and contribution rules.
