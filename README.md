# Common Park Crawler Platform

Common Park is a no-database, file-backed configurable crawler platform. It
combines spider configuration, HTTP/API and optional Playwright execution,
extraction, pagination, detail follow, incremental state, scheduling, workers,
sessions, observability, exports, a FastAPI management API, a static Web Admin,
examples/templates, and quality gates.

Runtime state is stored under `data/` through `FileStore`. SQL is kept only as
future migration documentation in `docs/schema.sql`; it is not executed by the
runtime.

The current delivery baseline is the no-database `FileStore` v1 release:
Feature 21 final acceptance evidence is complete, and the ChatGPT review loop
has returned the platform-level PASS for this v1 scope. Production deployment,
RBAC, database adapters, external distributed queues, and object storage remain
future stages outside this release.

## Quick Start

Use editable install when you want the console script:

```powershell
python -m pip install -e ".[api,dev]"
python -m crawler_platform.cli --help
crawler-platform --help
```

For a source checkout that is not installed yet, set `PYTHONPATH` in
PowerShell and keep using `python -m`:

```powershell
$env:PYTHONPATH='src'
python -m crawler_platform.cli --help
```

Run the deterministic local API example with a fixed task id:

```powershell
python -m crawler_platform.cli doctor --data-dir ./data-demo
python -m crawler_platform.cli validate examples/local_api_json.json
python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-demo --data-dir ./data-demo
python -m crawler_platform.cli list tasks --data-dir ./data-demo
python -m crawler_platform.cli task show quickstart-demo --paths --data-dir ./data-demo
python -m crawler_platform.cli observability report task quickstart-demo --data-dir ./data-demo
python -m crawler_platform.cli export task quickstart-demo --format json --data-dir ./data-demo
python -m crawler_platform.cli storage check --data-dir ./data-demo
```

The run writes `./data-demo/tasks/quickstart-demo.json`,
`./data-demo/results/quickstart-demo.jsonl`,
`./data-demo/observability/reports/tasks/quickstart-demo.json`, logs, metrics,
and export manifests under `./data-demo/exports/`.

Find the recommended first examples and create a starter config:

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples list --quickstart
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli init spider --type api --output ./data-demo/my-api-spider.json
python -m crawler_platform.cli examples smoke --data-dir ./data-demo/examples-smoke
```

All default examples use local fixtures under `examples/fixtures/`.

Rendered-page override examples are available for offline validation:

```powershell
python -m crawler_platform.cli validate examples/playwright_per_page_wait_local.json
python -m crawler_platform.cli validate examples/playwright_space_style_scroll_local.json
```

## Debug Dry Run

Preview selectors, transforms, samples, and field quality before a formal run:

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/debug-dry-run
python -m crawler_platform.cli debug dry-run examples/debug_transforms.json --data-dir test-output/debug-transforms --json
```

Dry-run reads local fixtures, prints field diagnostics and quality summaries,
and does not write formal results, hashes, watermarks, or checkpoints. Saved
debug reports go under `debug_reports/`; failure artifacts go under
`debug_artifacts/tasks/<dry_run_id>/`. See [docs/debugging.md](docs/debugging.md).

## Crawl Policy And Plan

Preview URL scope, robots rules, normalization, and request limits without
fetching pages:

```powershell
python -m crawler_platform.cli validate examples/crawl_policy_local.json
python -m crawler_platform.cli plan examples/crawl_policy_local.json --json
python -m crawler_platform.cli debug selector examples/fixtures/crawl_policy_page.html --selector ".title" --selector-type css --json
python -m crawler_platform.cli debug extract examples/debug_extract_local.json --input-file examples/fixtures/debug_extract_items.json --json
python -m crawler_platform.cli run examples/crawl_policy_local.json --task-id policy-demo --data-dir ./test-output/enhancement01-policy
```

See [docs/crawl_policy.md](docs/crawl_policy.md) for the `crawl_policy`
configuration and warn/block behavior.

## Run The Web Admin

```powershell
python -m pip install -e ".[api]"
uvicorn crawler_platform.api:create_app --factory --reload
```

Open `http://127.0.0.1:8000/admin`. JSON API responses use the
`{ok,data,error,meta}` envelope; static Web Admin assets are served without the
envelope.

## Quality Gates

```powershell
python -m compileall -q src tests scripts
pytest -q
python scripts/quality_gate.py --quick --json-report ./test-output/delivery/quality-quick.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/delivery/matrix-quick.json
```

`--quick` runs deterministic offline checks. `--full` also runs the full pytest
subprocess from the quality gate. See [docs/testing.md](docs/testing.md) and
[docs/test_matrix.md](docs/test_matrix.md).

On Windows, `scripts/verify_delivery.ps1` runs the same checks in a safe
sequence and writes reports under `test-output/delivery/`:

```powershell
.\scripts\verify_delivery.ps1
.\scripts\verify_delivery.ps1 -Full
.\scripts\verify_delivery.ps1 -SkipInstall
```

Do not run `quality_gate.py` and `run_test_matrix.py` in parallel when they use
the default report directories; both scripts create local state. Use a short
project path on Windows, and set `$env:PYTHONIOENCODING='utf-8'` when viewing
output that may contain non-ASCII currency or fixture text.

## Architecture

```text
SpiderConfig
  -> CrawlerEngine
      -> HTTP/API fetcher or optional Playwright fetcher
      -> Extractor
      -> Pagination / Detail / Incremental
      -> FileStore

Scheduler -> Worker -> CrawlerEngine
FastAPI API -> services -> FileStore
Web Admin -> FastAPI API
```

Core modules:

- `config_loader` and `validation`: canonical SpiderConfig loading and checks.
- `engine`: task execution for HTTP, API, and optional rendered pages.
- `extractor`: CSS, attribute, XPath subset, regex, JSON path, transforms, and
  nested field extraction.
- `storage`: file-backed state, locks, snapshots, results, hashes, sessions,
  observability, and exports.
- `scheduler` and `worker`: scheduled and queued execution using local files.
- `session`: cookies, storage state, login/refresh flows, and redacted events.
- `observability`: logs, metrics, traces, record samples, and run reports.
- `exporter`: JSON, JSONL, CSV, and XLSX exports with redaction.
- `api` and `web/admin`: management API and static admin console.
- `examples`: indexed local examples and templates.

See [docs/architecture.md](docs/architecture.md).

## Feature Status

Features 01-21 have been implemented as scoped Feature passes:

- Feature 01: Config Schema
- Feature 02: FileStore
- Feature 03: Extractor
- Feature 04: HTTP/API Engine
- Feature 05: Pagination
- Feature 06: Detail Follow
- Feature 07: Incremental State
- Feature 08: Playwright / BrowserPool
- Feature 09: Request Governance
- Feature 10: Scheduler
- Feature 11: Worker
- Feature 12: Lifecycle
- Feature 13: Session
- Feature 14: Observability
- Feature 15: Exporter
- Feature 16: FastAPI Management API
- Feature 17: Web Admin
- Feature 18: Examples / Templates
- Feature 19: Test Matrix / Quality Gate
- Feature 20: Documentation System
- Feature 21: Final Acceptance

Feature 21 records the final local acceptance evidence, and the ChatGPT review
loop has returned the current v1 platform PASS. This PASS is scoped to the
no-database `FileStore` v1 delivery described here; production deployment,
RBAC, database adapters, external distributed queues, and object storage remain
future work. See [docs/feature_status.md](docs/feature_status.md).

## Directory Structure

```text
src/crawler_platform/        runtime package
src/crawler_platform/web/    package-local Web Admin static assets
examples/                    indexed local examples and templates
examples/fixtures/           local fixture payloads and rendered pages
docs/                        architecture, user, developer, API, CLI, and tests
tests/                       pytest suite and matrix metadata
scripts/                     quality gate and matrix runner
data/                        default FileStore root when running locally
test-output/                 ignored local verification output
```

## Spider Config

Canonical config fields use `type` for the crawler type and `scheduler` for
schedule settings. Legacy compatibility fields are accepted by validation but
are not the primary documentation contract.

Common fields:

- `version`: config schema version, currently `1.0`.
- `type`: `http`, `api`, or `playwright`.
- `start_urls`: local fixture paths or entry URLs.
- `request`: method, params, headers, cookies, body, JSON payload, timeout,
  response type, retry, proxy, and rate settings.
- `fields`: extraction rules for CSS, attribute, XPath subset, regex, JSON path,
  transforms, nested children, and defaults.
- `pagination`: page, offset, explicit URL list, next-link, cursor, max pages,
  and max records.
- `detail`: detail follow rules and merge mode.
- `request.playwright`, `pagination.request.playwright`, and
  `detail.request.playwright`: page-role render readiness overrides.
- `dedup`, `watermark`, and checkpoints: incremental state.
- `playwright`: optional rendered-page settings.
- `scheduler`, `worker`, `session`, `observability`, and `export`: operational
  behavior.

See [docs/config.md](docs/config.md) and
[docs/spider_config.schema.json](docs/spider_config.schema.json).

## CLI

Examples:

```powershell
python -m crawler_platform.cli validate examples/local_api_json.json
python -m crawler_platform.cli run examples/local_html_list.json --data-dir test-output/readme-html
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir test-output/readme-examples
python -m crawler_platform.cli --data-dir test-output/readme-store storage check
python -m crawler_platform.cli --data-dir test-output/readme-scheduler scheduler list
python -m crawler_platform.cli --data-dir test-output/readme-worker worker stats
python -m crawler_platform.cli --data-dir test-output/readme-session session list
python -m crawler_platform.cli --data-dir test-output/readme-observability observability logs
python -m crawler_platform.cli export list --data-dir test-output/readme-export
```

See [docs/cli.md](docs/cli.md).

## API

Important endpoint groups:

- Runtime: `/health`, `/runtime/info`, `/runtime/capabilities`
- Spiders: `/spiders`, `/spiders/validate`
- Tasks: `/tasks`, `/tasks/run`, task results, reports, logs, metrics
- Storage: `/storage/health`, repair, snapshots, incremental state
- Scheduler: `/scheduler/schedules`, `/scheduler/run-due`
- Worker: `/worker/jobs`, `/worker/run-once`, `/worker/stats`
- Sessions: `/sessions`, `/sessions/events`
- Observability: `/observability/logs`, metrics, reports, traces
- Exports: `/exports`, task/job/scheduler/log exports
- Examples: `/examples`, `/examples/validate`, `/examples/smoke`
- Web Admin: `/admin`

OpenAPI is available at `/openapi.json`. See [docs/api.md](docs/api.md).

## Examples And Templates

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir test-output/examples-smoke
python -m crawler_platform.cli examples copy template-api-basic --to test-output/template-api-basic.json
```

See [docs/examples.md](docs/examples.md) and
[examples/README.md](examples/README.md).

## Optional Playwright

The core crawler does not require Playwright. Install `.[playwright]` only when
real rendered-page crawling is needed. Local rendered fixture examples can run
without a browser install. Optional real-browser tests skip when browsers are
missing.

See [docs/playwright.md](docs/playwright.md).

## No-Database Boundary

Runtime code must not import database drivers or ORM libraries. The database
dependency scan is:

```powershell
rg -n -i "\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b" src pyproject.toml
```

Expected result: no matches. Future persistence work should replace or extend
the `storage` layer through an adapter while preserving engine, API, CLI, and
export semantics.

## Documentation Index

- [Quick Start](docs/quick_start.md)
- [Architecture](docs/architecture.md)
- [User Guide](docs/user_guide.md)
- [Developer Guide](docs/developer_guide.md)
- [CLI](docs/cli.md)
- [API](docs/api.md)
- [Web Admin](docs/web_admin.md)
- [Config](docs/config.md)
- [Storage](docs/storage.md)
- [HTTP/API Engine](docs/http_api_engine.md)
- [Pagination](docs/pagination.md)
- [Detail Follow](docs/detail_follow.md)
- [Incremental](docs/incremental.md)
- [Request Governance](docs/request_governance.md)
- [Playwright](docs/playwright.md)
- [Scheduler](docs/scheduler.md)
- [Worker](docs/worker.md)
- [Lifecycle](docs/lifecycle.md)
- [Session](docs/session.md)
- [Observability](docs/observability.md)
- [Exporter](docs/exporter.md)
- [Examples](docs/examples.md)
- [Debugging](docs/debugging.md)
- [Testing](docs/testing.md)
- [Test Matrix](docs/test_matrix.md)
- [Final Acceptance](docs/final_acceptance.md)
- [Delivery Checklist](docs/delivery_checklist.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Codex Workflow](docs/codex_workflow.md)
- [Feature Status](docs/feature_status.md)

## Troubleshooting

Start with [docs/troubleshooting.md](docs/troubleshooting.md). Common checks:

- Use `PYTHONPATH=src` or install editable mode when local imports fail.
- Run local examples from `examples/fixtures/`, not real network targets.
- Use `dry-run` or `debug dry-run` to inspect selectors and field quality
  before a formal run.
- Treat pytest cache warnings as local cache directory issues when tests pass.
- Keep SQL in `docs/schema.sql` only.
