# Architecture

The crawler platform is a no-database, file-backed runtime. The main contract is
that a canonical `SpiderConfig` flows through execution services and persists
state through `FileStore`.

## Module Map

```text
SpiderConfig
  |
  v
CrawlerEngine
  |-- HTTP/API fetcher
  |-- optional Playwright fetcher
  |-- Request governance
  |-- Session manager
  |-- Pagination
  |-- Detail follow
  |-- Incremental state
  v
Extractor
  |
  v
FileStore

Scheduler -> Worker -> CrawlerEngine
FastAPI -> services -> FileStore
Web Admin -> FastAPI
Exporter -> FileStore
Observability -> FileStore
```

## Main Flow

1. `config_loader` reads JSON or YAML-compatible JSON into a `SpiderConfig`.
2. `validation` normalizes legacy inputs and validates canonical fields such as
   `type` and `scheduler`.
3. `CrawlerEngine` creates task state, executes entry requests, handles
   pagination and detail follow, and writes records.
4. `Extractor` turns HTML or JSON payloads into records through field rules.
5. `FileStore` stores spiders, tasks, results, dedup hashes, watermarks,
   checkpoints, schedules, jobs, sessions, observability data, exports, and
   snapshots.

## Execution Paths

- HTTP path: local file fixture or HTTP-style request handled by the fetcher.
- API path: JSON payload extraction with `items_json_path` and JSON path fields.
- Playwright path: optional rendered fetcher. Local rendered fixtures remain
  deterministic and do not require a real browser install.

## Composition

Pagination expands the request set. Detail follow enriches list records.
Incremental dedup and watermark logic decide whether records should be skipped,
stored, or checkpointed. These features can be combined in one spider as long as
the config validates.

Request governance, sessions, and observability are cross-cutting:

- Request governance applies retry, proxy, anti-bot headers, rate limits, and
  concurrency before fetches.
- Session management injects and persists cookies, headers, and storage state.
- Observability records logs, metrics, traces, samples, and run reports.

## Scheduler And Worker

`SchedulerService` stores manual, interval, and cron schedules. It can execute
due schedules immediately or enqueue them into `WorkerService`. Workers claim
local file-backed jobs and execute them through the same `CrawlerEngine` path as
manual runs.

## API And Web Admin

FastAPI constructs the same services used by CLI flows. The Web Admin is a
package-local static console that calls API endpoints; it does not own separate
business logic or a separate build system.

## Database Boundary

The current runtime intentionally avoids database dependencies. `docs/schema.sql`
is a future migration note only. If a database is added later, it should be
implemented as a `StorageAdapter` or `FileStore` replacement so the engine,
extractor, scheduler, worker, API, CLI, examples, and exporters keep the same
semantic contracts.
