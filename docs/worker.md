# Worker

Feature 11 adds a local file-backed worker queue and worker executor. It does
not add a Web UI, exporter workflow, permission system, service installer,
runtime database, or distributed queue.

## Job Model

Worker jobs are stored as JSON and use these core fields:

- `job_id`: unique queue id.
- `job_type`: currently `spider_run`.
- `spider_id` and `spider_config`: the canonical spider config snapshot.
- `task_id`: optional task id; generated as `worker-<job_id>` when omitted.
- `schedule_id`: optional scheduler linkage.
- `source`: `manual`, `scheduler`, or `api`.
- `status`: `queued`, `leased`, `running`, `succeeded`, `failed`,
  `cancelled`, or `dead_letter`. Feature 12 also adds `paused`, `retrying`,
  and `cancelling`.
- `priority`: larger numbers are claimed first.
- `run_after`: ISO datetime; future jobs cannot be claimed yet.
- `attempt` and `max_attempts`: worker execution attempts.
- `lease_owner`, `lease_expires_at`, `heartbeat_at`: worker lease state.
- `created_at`, `updated_at`, `started_at`, `finished_at`.
- `error`, `warnings`, and `metadata`.

`max_attempts` is the worker execution retry budget. It is separate from
request retry in Feature 09: HTTP status, network, proxy, rate limit, and render
retry still happen inside the Engine request governance layer.

## Queue Layout

The runtime remains no-database. Queue state is stored under:

```text
data/queue/
  queued/
  leased/
  running/
  paused/
  retrying/
  cancelling/
  succeeded/
  failed/
  cancelled/
  dead_letters/
data/workers/
data/worker_runs/
```

`FileStore` uses atomic JSON writes and file locks for enqueue, claim, lease
updates, completion, failure, cancellation, and lease recovery. A job can be
claimed by only one worker.

## Lease And Heartbeat

`claim_job()` moves a due queued job to `running`, assigns `lease_owner`, sets
`lease_expires_at`, and increments `attempt`. `heartbeat_job()` updates
`heartbeat_at` and extends the lease. `requeue_expired_leases()` moves expired
`leased` or `running` jobs back to `queued`.

## Priority And Delay

Due jobs are sorted by priority descending, then `run_after`, then creation
time. Jobs with a future `run_after` stay queued but are not claimable.

## Dead Letters

When worker execution fails, the job is requeued while `attempt <
max_attempts`. Once the attempt budget is exhausted, the job moves to
`dead_letter` with the error payload and warning metadata.

## Lifecycle Controls

Feature 12 adds job-level lifecycle operations on top of the Feature 11 queue:

- queued jobs can be paused, resumed, or cancelled;
- paused jobs are not claimable;
- running jobs can receive a cancellation signal and move to `cancelling`;
- failed, cancelled, and dead-lettered jobs can be retried into `queued`;
- any existing job can be rerun by creating a new queued job with `source_job_id`
  metadata.

Lifecycle operations write events under `data/lifecycle_events/jobs/`. Running
cancellation is checked by the engine through the worker's lifecycle signal, so
already written task results and checkpoints remain intact.

## Scheduler Link

The scheduler keeps its old synchronous behavior by default:

```python
SchedulerService.run_due_jobs()
```

To enqueue due schedules instead, use:

```python
SchedulerService.enqueue_due_jobs(now="2026-06-03T00:00:00Z")
SchedulerService.run_due_jobs(now="2026-06-03T00:00:00Z", enqueue=True)
```

Scheduler enqueue uses `schedule_id + due_at` as a dedupe key, records a queued
`scheduler_run`, and stores the linked `job_id`. When the worker executes the
job, the scheduler run is updated with the final task result.

## CLI

```powershell
python -m crawler_platform.cli worker enqueue examples/worker_api_job.json --data-dir ./data
python -m crawler_platform.cli worker run-once --data-dir ./data
python -m crawler_platform.cli worker run-until-empty --data-dir ./data
python -m crawler_platform.cli worker jobs --data-dir ./data
python -m crawler_platform.cli worker stats --data-dir ./data
python -m crawler_platform.cli worker recover --data-dir ./data
python -m crawler_platform.cli worker dead-letters --data-dir ./data
python -m crawler_platform.cli worker job pause <job_id> --data-dir ./data
python -m crawler_platform.cli worker job resume <job_id> --data-dir ./data
python -m crawler_platform.cli worker job cancel <job_id> --data-dir ./data
python -m crawler_platform.cli worker job retry <job_id> --data-dir ./data
python -m crawler_platform.cli worker job rerun <job_id> --data-dir ./data
python -m crawler_platform.cli worker job events <job_id> --data-dir ./data
python -m crawler_platform.cli scheduler enqueue-due --now 2026-06-03T00:00:00Z --data-dir ./data
```

Use `--json` on worker commands for machine-readable output.

## FastAPI

- `POST /worker/jobs`
- `GET /worker/jobs`
- `GET /worker/jobs/{job_id}`
- `POST /worker/run-once`
- `POST /worker/run-until-empty`
- `POST /worker/jobs/{job_id}/cancel`
- `POST /worker/jobs/{job_id}/pause`
- `POST /worker/jobs/{job_id}/resume`
- `POST /worker/jobs/{job_id}/retry`
- `POST /worker/jobs/{job_id}/rerun`
- `GET /worker/jobs/{job_id}/events`
- `GET /worker/jobs/{job_id}/lifecycle`
- `POST /worker/recover`
- `GET /worker/stats`
- `GET /worker/dead-letters`
- `POST /scheduler/enqueue-due`

`POST /worker/jobs` accepts either a spider config payload or
`{"spider": {...}, "source": "api", "priority": 10}`.

## Scope Boundary

This feature is the local execution layer between Scheduler and later
management surfaces. It does not include Web UI, exporter completion,
permission policy, runtime database migrations, Redis, Celery, Kafka,
RabbitMQ, service installation, or a deployed daemon.
