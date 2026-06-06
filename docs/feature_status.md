# Feature Status

Feature status is scoped. A PASS means the named Feature completed its own
review loop. Feature 21 final acceptance evidence is complete, and the ChatGPT
review loop has returned the current no-database FileStore v1 platform PASS.
Codex does not independently declare acceptance outside that ChatGPT verdict.

| Feature | Status | Scope |
|---|---|---|
| Feature 01 Config Schema | PASS | Canonical spider schema, validation, and schema export. |
| Feature 02 FileStore | PASS | File-backed runtime state, atomic writes, locks, health, repair, snapshots. |
| Feature 03 Extractor | PASS | CSS, attribute, XPath subset, regex, JSON path, transforms, nested fields. |
| Feature 04 HTTP/API Engine | PASS | Deterministic HTTP/API execution with local fixtures and FakeFetcher support. |
| Feature 05 Pagination | PASS | Page, offset, URL list, next-link, cursor, max pages, max records. |
| Feature 06 Detail Follow | PASS | Detail URL resolution, field merge modes, pagination/detail composition. |
| Feature 07 Incremental | PASS | Dedup, watermark, checkpoints, resume behavior. |
| Feature 08 Playwright / BrowserPool | PASS | Optional rendered-page execution and local rendered fixtures. |
| Feature 09 Request Governance | PASS | Retry, proxy, anti-bot, rate limit, concurrency. |
| Feature 10 Scheduler | PASS | Manual, interval, cron schedules and due-job execution. |
| Feature 11 Worker | PASS | Local queue, job claiming, run-once/run-until-empty, stats, dead letters. |
| Feature 12 Lifecycle | PASS | Pause, resume, cancel, retry, rerun, events, signals. |
| Feature 13 Session | PASS | Cookies, storage state, login/refresh flows, redacted session events. |
| Feature 14 Observability | PASS | Logs, metrics, traces, record samples, reports, redaction. |
| Feature 15 Exporter | PASS | JSON, JSONL, CSV, XLSX exports, manifests, field shaping, redaction. |
| Feature 16 FastAPI Management API | PASS | API envelope, errors, endpoint groups, OpenAPI, dependency injection. |
| Feature 17 Web Admin | PASS | Static package-local admin console consuming the management API. |
| Feature 18 Examples / Templates | PASS | Indexed examples/templates, validation, smoke, CLI/API/Web Admin links. |
| Feature 19 Test Matrix / Quality Gate | PASS | Feature/CLI/API matrices, quality gate scripts, JSON reports, guard tests. |
| Feature 20 Documentation System | PASS | README, user/developer docs, architecture, config, CLI, workflow, status, doc tests. |
| Feature 21 Final Acceptance | PASS | Final acceptance report, delivery checklist, final smoke tests, full gate evidence. |
| Feature 22 Production Readiness | IN_PROGRESS | Storage/queue/auth adapter blueprint and production rollout plan. |

## Remaining Boundary

The v1 PASS is scoped to the local-first, no-database FileStore platform in this
repository. Production deployment, full RBAC, database adapters, external
distributed queues, object storage, and release pipelines are in Feature 22
planning/execution and are not included in the current v1 PASS scope.
