# Task Lifecycle Control

Feature 12 adds a file-backed lifecycle control layer for tasks and worker jobs. Runtime storage remains database-free: all lifecycle state is stored under the configured `FileStore` root.

## Scope

Included:

- Task pause, resume, cancel, retry, and rerun.
- Worker job pause, resume, cancel, retry, and rerun.
- Lifecycle events for task, job, and scheduler-run state changes.
- Cancellation signals checked by the engine at start URL, page, detail, request, and record boundaries.
- Scheduler-run updates when a scheduler-created worker job is cancelled.
- CLI and FastAPI lifecycle endpoints.

Excluded from this feature:

- Web UI completion.
- Exporter completion.
- Permission system.
- Database migrations.
- External distributed queues.
- System service installation.
- Final platform PASS.

## Storage Layout

Lifecycle files are stored with the same atomic write and file-lock behavior as the rest of `FileStore`.

```text
data/
  lifecycle_events/
    tasks/<task_id>/<event_id>.json
    jobs/<job_id>/<event_id>.json
    scheduler_runs/<run_id>/<event_id>.json
  lifecycle_signals/
    tasks/<task_id>.json
    jobs/<job_id>.json
```

`storage check` validates lifecycle event and signal JSON files. Snapshots include `lifecycle_events` and `lifecycle_signals`.

## Task States

Core task states remain compatible with earlier features:

- `pending`
- `running`
- `success`
- `failed`
- `cancelled`

Feature 12 adds:

- `paused`
- `retrying`
- `rerunning`
- `cancelling`

Typical transitions:

| From | To | Operation |
|---|---|---|
| `pending` | `paused` | `task pause` |
| `paused` | `pending` | `task resume` |
| `pending` / `paused` | `cancelled` | direct cancel |
| `running` | `cancelling` | cancel requested |
| `cancelling` / `running` | `cancelled` | engine observes cancel signal |
| `failed` / `cancelled` | new `pending` task | `task retry` |
| `success` / `failed` / `cancelled` | new `pending` task | `task rerun` |

Illegal transitions raise `InvalidLifecycleTransitionError`. Management code can force a transition through service APIs, and force transitions are recorded as lifecycle events.

## Worker Job States

Feature 11 states remain compatible:

- `queued`
- `leased`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `dead_letter`

Feature 12 adds:

- `paused`
- `retrying`
- `cancelling`

Paused jobs are stored outside the `queued` directory and are not claimed. Resuming a paused job moves it back to `queued`. Running job cancellation writes a job cancellation signal and moves the job to `cancelling`; the worker then marks it `cancelled` when the engine observes the signal.

## Events

Events include:

- `started`
- `paused`
- `resumed`
- `cancel_requested`
- `cancelled`
- `failed`
- `retry_requested`
- `retried`
- `rerun_requested`
- `rerun_created`
- `completed`
- `force_transition`

Each event stores:

- `event_id`
- `target_type`
- `target_id`
- `event_type`
- `from_status`
- `to_status`
- `reason`
- `operator`
- `created_at`
- `metadata`

## Cancel Boundaries

The engine checks lifecycle signals at safe boundaries:

- run start
- each start URL
- each page
- after fetch
- each detail URL
- each detail fetch
- each record batch
- each record before save

When cancellation is detected:

- the task becomes `cancelled`;
- the checkpoint is retained or a cancellation checkpoint is created;
- already written results remain intact;
- pending record batches are not saved after cancellation;
- worker jobs become `cancelled` when the worker observes the cancelled task.

Running pause is intentionally limited. Pending and queued pause are supported. A running pause request is recorded as an event and warning, but the engine is not interrupted.

## Retry vs Rerun

`retry` is for failed or cancelled work. Task retry creates a new pending task with `source_task_id` pointing to the original task. Worker job retry requeues the same job and resets execution attempts to zero.

`rerun` is for intentionally repeating a finished task or job. Task rerun creates a new pending task with `source_task_id`. Worker job rerun creates a new queued job with `source_job_id` metadata.

Deduplication remains controlled by spider config. Global or spider-scoped dedup prevents retry/rerun from writing already saved records.

## Scheduler Coordination

Scheduler-created worker jobs keep `schedule_id` and `scheduler_run_id` metadata. Cancelling a scheduler-created job updates the linked scheduler run to `cancelled` and records a scheduler-run lifecycle event.

Pause boundaries:

- Pause a schedule to stop future triggers.
- Pause a worker job or task to affect already generated work.

## CLI

```bash
python -m crawler_platform.cli task pause <task_id> --data-dir ./data
python -m crawler_platform.cli task resume <task_id> --data-dir ./data
python -m crawler_platform.cli task cancel <task_id> --data-dir ./data
python -m crawler_platform.cli task retry <task_id> --data-dir ./data
python -m crawler_platform.cli task rerun <task_id> --data-dir ./data
python -m crawler_platform.cli task events <task_id> --data-dir ./data

python -m crawler_platform.cli worker job pause <job_id> --data-dir ./data
python -m crawler_platform.cli worker job resume <job_id> --data-dir ./data
python -m crawler_platform.cli worker job cancel <job_id> --data-dir ./data
python -m crawler_platform.cli worker job retry <job_id> --data-dir ./data
python -m crawler_platform.cli worker job rerun <job_id> --data-dir ./data
python -m crawler_platform.cli worker job events <job_id> --data-dir ./data
```

The legacy `worker cancel <job_id>` command is kept for Feature 11 compatibility.

## FastAPI

Task endpoints:

- `POST /tasks/{task_id}/pause`
- `POST /tasks/{task_id}/resume`
- `POST /tasks/{task_id}/cancel`
- `POST /tasks/{task_id}/retry`
- `POST /tasks/{task_id}/rerun`
- `GET /tasks/{task_id}/events`
- `GET /tasks/{task_id}/lifecycle`

Worker job endpoints:

- `POST /worker/jobs/{job_id}/pause`
- `POST /worker/jobs/{job_id}/resume`
- `POST /worker/jobs/{job_id}/cancel`
- `POST /worker/jobs/{job_id}/retry`
- `POST /worker/jobs/{job_id}/rerun`
- `GET /worker/jobs/{job_id}/events`
- `GET /worker/jobs/{job_id}/lifecycle`
