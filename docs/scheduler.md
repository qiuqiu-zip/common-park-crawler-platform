# Scheduler

Feature 10 adds a file-backed scheduler for spider configs. It does not add a
worker queue, Web UI, distributed scheduler, service installer, permission
system, or runtime database.

## SchedulerConfig

Spider configs use the canonical `scheduler` field:

- `enabled`: when `false`, `scheduler register` saves the spider but does not
  create an automatic schedule.
- `type`: `manual`, `interval`, or `cron`.
- `interval_seconds`: positive integer required by `interval`.
- `cron`: five-field cron expression required by `cron`: minute, hour, day,
  month, weekday.
- `timezone`: IANA timezone name, default `UTC`.
- `start_at`: optional ISO datetime. Naive values are interpreted in
  `timezone`.
- `end_at`: optional ISO datetime after which new runs are not scheduled.
- `misfire_policy`: `skip`, `run_once`, or `catch_up`.
- `max_instances`: concurrent run cap for one schedule.
- `jitter_seconds`: deterministic per-schedule offset applied to computed run
  times.

`max_concurrency` is accepted as a legacy input alias for `max_instances`, but
canonical dumps and examples use `max_instances`.

## Schedule Types

`manual` schedules never run through `run_due_jobs`; use `trigger` to run them.

`interval` schedules compute `next_run_at` from `start_at` when present, or from
the current time plus `interval_seconds`.

`cron` schedules use a lightweight five-field parser. The parser supports `*`,
comma lists, ranges, and step syntax such as `*/5`. Weekday `0` and `7` both
mean Sunday.

## Timezone And Next Run

All stored datetimes are normalized to UTC ISO strings. Cron matching and naive
`start_at`/`end_at` inputs are evaluated in the configured timezone. Around DST
boundaries, the platform relies on Python `zoneinfo`; configs that need exact
wall-clock behavior should use explicit timezone-aware ISO timestamps in tests.

## Misfires

When a due time is earlier than the supplied `now`:

- `skip`: no crawl is run; a skipped scheduler run is recorded and
  `next_run_at` advances beyond `now`.
- `run_once`: one crawl is run for the missed window.
- `catch_up`: several missed windows can run in one tick. The current service
  caps catch-up to a small bounded number and records a warning when capped.

## FileStore Layout

Schedules are stored under `data/schedules/<schedule_id>.json`. Scheduler run
records are stored under
`data/scheduler_runs/<schedule_id>/<scheduler_run_id>.json`.

`FileStore.check_storage()` validates both directories. Snapshots include
`schedules/`; scheduler run history is operational evidence and is not included
by default.

## Engine Relationship

`SchedulerService.run_due_jobs()` and `trigger_schedule_now()` create a task id,
record a scheduler run, and call the existing `CrawlerEngine`. HTTP/API,
pagination, detail following, incremental state, Playwright, and request
governance all stay in the Engine path.

The scheduler is a synchronous tick service by default. It does not install a
background daemon. Feature 11 adds an optional bridge that can enqueue due
schedules into the local worker queue instead of running them immediately:

```python
SchedulerService.enqueue_due_jobs(now="2026-06-03T00:00:00Z")
SchedulerService.run_due_jobs(now="2026-06-03T00:00:00Z", enqueue=True)
```

The enqueue bridge stores `source="scheduler"` worker jobs and deduplicates
each due run by `schedule_id + due_at`.

Feature 12 links scheduler-created worker jobs back to scheduler run lifecycle.
If a queued or running scheduler source job is cancelled, the linked
`scheduler_run` is updated to `cancelled` and a scheduler-run lifecycle event is
recorded. Pausing a schedule stops future triggers; pausing a task or worker
job only affects already generated work.

## CLI

```powershell
python -m crawler_platform.cli scheduler register examples/scheduled_interval.json --data-dir ./data
python -m crawler_platform.cli scheduler list --data-dir ./data
python -m crawler_platform.cli scheduler run-due --now 2026-06-03T00:00:00Z --data-dir ./data
python -m crawler_platform.cli scheduler enqueue-due --now 2026-06-03T00:00:00Z --data-dir ./data
python -m crawler_platform.cli scheduler trigger scheduled-interval-demo --data-dir ./data
python -m crawler_platform.cli scheduler pause scheduled-interval-demo --data-dir ./data
python -m crawler_platform.cli scheduler resume scheduled-interval-demo --data-dir ./data
python -m crawler_platform.cli scheduler disable scheduled-interval-demo --data-dir ./data
python -m crawler_platform.cli scheduler runs --data-dir ./data
```

Use `--json` on scheduler commands for machine-readable output.

## FastAPI

- `GET /scheduler/schedules`
- `POST /scheduler/schedules`
- `GET /scheduler/schedules/{schedule_id}`
- `POST /scheduler/schedules/{schedule_id}/trigger`
- `POST /scheduler/schedules/{schedule_id}/pause`
- `POST /scheduler/schedules/{schedule_id}/resume`
- `POST /scheduler/schedules/{schedule_id}/disable`
- `POST /scheduler/run-due`
- `POST /scheduler/enqueue-due`
- `GET /scheduler/runs`

`POST /scheduler/schedules` accepts either a spider config payload or
`{"spider": {...}}`.

## No Database

Runtime scheduler state is entirely file-backed. SQL remains only in
`docs/schema.sql` for future migration planning and is not imported or executed.
