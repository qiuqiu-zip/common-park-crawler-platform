# Final Acceptance

This document is the Feature 21 local acceptance report for the crawler
platform. It records the final handoff scope, acceptance commands, expected
artifacts, known boundaries, and the ChatGPT review-loop PASS for the current
v1 delivery baseline.

Feature 21 local acceptance evidence is complete, and the ChatGPT review loop
has returned the platform-level PASS for the no-database FileStore v1 scope.
Codex does not independently extend that judgment to future production,
database, RBAC, queue, storage, or deployment stages.

## Project Scope

The crawler platform is a local-first, file-backed crawler toolkit with:

- JSON spider configuration and validation.
- FileStore runtime state for spiders, tasks, results, checkpoints, schedules,
  jobs, sessions, observability data, exports, snapshots, and repair checks.
- HTTP/API and optional Playwright execution paths.
- Pagination, detail follow, incremental dedup, checkpoints, and resume.
- Request governance for retry, proxy metadata, anti-bot headers, rate limit,
  and concurrency.
- Scheduler and worker flows for local job execution.
- Task/job lifecycle controls.
- Session state, observability reports, and export workflows.
- FastAPI management API and package-local static Web Admin.
- Offline examples, templates, test matrix, quality gate scripts, and docs.

## Feature 01-20 Status

| Feature | Status | Evidence |
|---|---|---|
| Feature 01 Config Schema | PASS | Schema validation, schema export, tests. |
| Feature 02 FileStore | PASS | Atomic writes, locks, health, repair, snapshots. |
| Feature 03 Extractor | PASS | CSS, attribute, XPath subset, regex, JSON path, transforms. |
| Feature 04 HTTP/API Engine | PASS | Local fixtures and FakeFetcher execution. |
| Feature 05 Pagination | PASS | Page, offset, URL list, next-link, cursor, caps. |
| Feature 06 Detail Follow | PASS | Detail URL resolution and field merge modes. |
| Feature 07 Incremental | PASS | Dedup, watermark, checkpoint, resume. |
| Feature 08 Playwright / BrowserPool | PASS | Optional rendered-page path with local fixtures. |
| Feature 09 Request Governance | PASS | Retry, proxy, anti-bot, rate limit, concurrency. |
| Feature 10 Scheduler | PASS | Manual, interval, cron, due-job execution. |
| Feature 11 Worker | PASS | Local queue, claim, run-once, run-until-empty, stats. |
| Feature 12 Lifecycle | PASS | Pause, resume, cancel, retry, rerun, events. |
| Feature 13 Session | PASS | Cookies, storage state, login/refresh, redaction. |
| Feature 14 Observability | PASS | Logs, metrics, traces, samples, reports, redaction. |
| Feature 15 Exporter | PASS | JSON, JSONL, CSV, XLSX, manifests, shaping, redaction. |
| Feature 16 FastAPI Management API | PASS | API envelope, errors, endpoint groups, OpenAPI. |
| Feature 17 Web Admin | PASS | Static local admin console and API references. |
| Feature 18 Examples / Templates | PASS | Indexed examples, validation, smoke, templates. |
| Feature 19 Test Matrix / Quality Gate | PASS | Matrix JSON, quality scripts, JSON reports, guard tests. |
| Feature 20 Documentation System | PASS | README, architecture, user/developer docs, doc tests. |

## Feature 21 Acceptance Scope

Feature 21 verifies the completed platform without adding new business
features. The scope is:

- Full pytest.
- Quick and full quality gate reports where runtime allows.
- Quick and full test matrix reports where runtime allows.
- Examples validation and smoke.
- CLI local smoke.
- FastAPI OpenAPI and endpoint smoke.
- Web Admin static resource smoke.
- FileStore health, repair dry-run, and snapshot restore dry-run.
- Scheduler, worker, and engine linked smoke.
- Session, observability, export, result, and API linked smoke.
- Runtime database and ORM dependency scan.
- External network, sensitive value, doc link, and command consistency checks.
- Windows-safe path regression through data directories with spaces.

## Acceptance Commands

```powershell
python -m compileall -q src tests scripts
pytest -q
python scripts/quality_gate.py --quick --json-report ./test-output/feature21-final/quality-quick.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/feature21-final/matrix-quick.json
python -c "from crawler_platform.api import create_app; app = create_app(); print(type(app).__name__); print(bool(app.openapi()))"
python scripts/quality_gate.py --full --json-report ./test-output/feature21-final/quality-full.json
python scripts/run_test_matrix.py --full --json-report ./test-output/feature21-final/matrix-full.json
rg -n -i "\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b" src pyproject.toml
```

On Windows, use the `py` launcher if `python` is not available. For a source
tree that has not been installed in editable mode, set `PYTHONPATH=src` before
the one-line FastAPI import probe.

## Acceptance Result Summary

Feature 21 local acceptance is considered complete when the commands above pass
or when an unavailable full-mode check is explicitly explained. Reports should
be written under `test-output/feature21-final/` and excluded from commits.

The final Codex result sent to ChatGPT records the exact command results for the
current run. This document should be read together with those generated JSON
reports and the ChatGPT RESULT_REVIEW verdict for the v1 baseline.

## Known Boundaries

- Runtime storage is file-backed; there is no database runtime.
- SQL exists only as [docs/schema.sql](docs/schema.sql) for future migration
  discussion and is not executed by runtime code.
- Playwright real-browser execution is optional; local rendered fixtures remain
  the default offline verification path.
- RBAC and permission systems are not included.
- External distributed queues and object storage are not included.
- Real third-party login, captcha bypass, or remote target crawling is not part
  of default acceptance.
- Production deployment scripts, service installers, and release pipelines are
  not included.

## Delivery Artifacts

- Source package under `src/crawler_platform/`.
- CLI entry via `python -m crawler_platform.cli`.
- FastAPI app factory `crawler_platform.api:create_app`.
- Static Web Admin under `src/crawler_platform/web/admin/`.
- Examples and templates under `examples/`.
- Documentation under `README.md`, `docs/`, and `examples/README.md`.
- Quality scripts under `scripts/`.
- Pytest suite and matrix metadata under `tests/`.

## Run And Cleanup

Use `--data-dir` or `create_app(data_dir=...)` to keep runtime files in a
known local directory. Delete temporary directories under `test-output/` after
inspection if the artifacts are no longer needed.

Generated runtime output such as `test-output/`, `data/`, `.pytest_cache/`, and
temporary export files should not be committed.

## Final Judgment Scope

The ChatGPT review loop has accepted the current v1 baseline after Feature 21
evidence. This judgment covers the local-first no-database FileStore crawler
platform and excludes production deployment, RBAC, database adapters, external
distributed queues, object storage, and release automation.
