# Test Matrix

Feature 19 stores executable matrix metadata in JSON files under `tests/`.
These files are read by pytest and by `scripts/run_test_matrix.py`; they are not
free-form documentation.

## Matrix Files

- `tests/feature_matrix.json`: Feature 01-18 regression coverage.
- `tests/cli_matrix.json`: CLI smoke and full command coverage.
- `tests/api_matrix.json`: FastAPI endpoint and static admin coverage.

## Feature Coverage

The Feature matrix covers:

- Feature 01: Config schema / validation
- Feature 02: FileStore
- Feature 03: Extractor
- Feature 04: HTTP/API Engine
- Feature 05: Pagination
- Feature 06: Detail follow
- Feature 07: Incremental / dedup / watermark / checkpoint
- Feature 08: Playwright / BrowserPool
- Feature 09: Request governance
- Feature 10: Scheduler
- Feature 11: Worker
- Feature 12: Lifecycle
- Feature 13: Session
- Feature 14: Observability
- Feature 15: Exporter
- Feature 16: FastAPI management API
- Feature 17: Web Admin
- Feature 18: Examples / templates

Every Feature entry records `feature_id`, `feature_name`, `unit_tests`,
`integration_tests`, `cli_tests`, `api_tests`, `examples`, `docs`,
`required_commands`, `offline_only`, and `database_free`. Empty categories must
be explained through `not_applicable`.

## CLI Matrix

The CLI matrix includes quick checks for help output, config validation, storage
health, examples list/validate, scheduler listing, worker listing/stats, session
listing/events, observability logs/metrics, and export listing.

Full-mode entries document heavier or stateful flows such as running a local
example, examples smoke, scheduler register/run-due, worker enqueue/run-once,
observability report, and export task/show. Stateful entries that require a
prepared task or export manifest are documented with `execute=false`.

## API Matrix

The API matrix covers runtime, spiders, tasks, storage, scheduler, worker,
sessions, observability, exports, examples, Web Admin, and OpenAPI generation.
JSON API endpoints use the `{ok,data,error,meta}` envelope. Static resources
such as `/admin` and `/openapi.json` are intentionally outside the envelope.

## Examples Smoke Matrix

The examples smoke subset remains local-only and must include coverage tags for
HTTP, API, pagination, detail follow, incremental behavior, scheduler, worker,
session, observability, and export. The smoke subset is validated by
`scripts/quality_gate.py` and `tests/test_examples_matrix.py`.

## Optional Playwright

Playwright coverage is split between local rendered fixtures and optional real
browser tests. Missing Playwright browsers should skip optional tests with a
clear reason. The default quick matrix does not require a real browser install.

## Windows And Linux Paths

The matrix includes a Windows-safe FileStore deep path regression through
pytest. This protects shortened lock naming and path handling while keeping the
same file-backed runtime behavior for Linux paths.

## Boundaries

This matrix is a Feature 19 quality layer. It does not declare the whole project
complete, does not perform final acceptance, and does not add deployment,
database migration, RBAC, or new crawler business features.
