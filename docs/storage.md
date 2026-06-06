# FileStore Contract

The crawler platform runtime is file-backed. SQL in `docs/schema.sql` is design
documentation only and is not imported or executed by runtime code.

## Directory Structure

`FileStore(root)` creates these paths:

- `spiders/`: canonical spider config JSON files.
- `tasks/`: task metadata JSON files.
- `results/`: task result JSONL files.
- `hashes/`: scoped dedup hash indexes, such as `hashes/global/<dataset>.txt`.
- `watermarks/`: per-spider incremental boundaries.
- `checkpoints/`: resumable task progress state.
- `schedules/`: registered scheduler jobs.
- `scheduler_runs/`: scheduler trigger history and task linkage.
- `queue/`: worker jobs split by queue status.
- `workers/`: worker heartbeat and counter snapshots.
- `worker_runs/`: worker execution evidence linked to jobs and tasks.
- `lifecycle_events/`: task, worker job, and scheduler-run lifecycle events.
- `lifecycle_signals/`: task and worker job cancellation signals.
- `sessions/`: session profiles, cookies, Playwright storage states, account
  references, and redacted session events.
- `locks/`: short-lived lock files for concurrent writes.
- `tmp/`: temporary files used by atomic writes.
- `snapshots/`: snapshot directories with manifests.
- `dead_letters/`: recoverable deletes, corrupted files, and restore backups.
- `exports/`: CLI/API export output.
- `storage_metadata.json`: storage version and feature metadata.

## Atomic Writes

JSON files are written through a temporary file under `tmp/`, flushed, fsynced,
and then moved into place with `os.replace`. If the replace fails, the temporary
file is removed and the existing destination remains intact.

## File Locks

Concurrent write paths use lock files under `locks/`. Lock acquisition uses
exclusive file creation with a short retry loop. Expired lock files are reported
by `check_storage()`.

## JSONL Results

Task records are appended as one JSON object per line in
`results/<task_id>.jsonl`.

- `read_records(task_id, strict=True)` raises `CorruptedFileError` with a line
  number when a line cannot be parsed.
- `read_records(task_id, strict=False)` skips corrupted lines and returns valid
  records.

## Hash Indexes

Dedup hashes are stored as sorted text lines:

```text
hashes/<scope>/<dataset>.txt
```

Supported scopes are caller-defined strings. The engine uses the default
`global` scope. The store provides `has_hash`, `add_hash`, `add_hashes`,
`load_hashes`, and `iter_hashes`. Writes are locked and duplicate hashes are not
written twice.

## Watermarks And Checkpoints

Watermarks are stored as JSON under `watermarks/<spider_id>/<dataset>.json`.
Checkpoints are stored as JSON under `checkpoints/<task_id>.json`. The store
provides `get_watermark`, `update_watermark`, `list_watermarks`,
`save_checkpoint`, `load_checkpoint`, `list_checkpoints`, and
`clear_checkpoint`.

## Task State Machine

Allowed transitions:

- `pending` -> `pending`, `running`, `paused`, `cancelled`
- `running` -> `running`, `success`, `failed`, `cancelling`, `cancelled`
- `paused` -> `paused`, `pending`, `cancelled`
- `failed` and `cancelled` -> themselves, `running` for checkpoint resume, or `retrying`
- `success` -> itself or `rerunning`
- `cancelling` -> `cancelling`, `cancelled`, or `failed`

Invalid transitions raise `InvalidTaskTransitionError` with task and path
context.

Feature 12 lifecycle services add the higher-level `pause`, `resume`, `cancel`,
`retry`, and `rerun` operations and record lifecycle events for those actions.

## Storage Check And Repair

`check_storage()` returns:

```json
{
  "ok": true,
  "errors": [],
  "warnings": [],
  "stats": {}
}
```

It checks required directories, metadata parsing and version, spider/task JSON,
result JSONL lines, hash file lines, schedule JSON, worker queue JSON, worker
state JSON, worker run JSON, lifecycle event JSON, lifecycle signal JSON,
temporary files, expired locks, and the dead-letter directory.

`repair_storage(dry_run=True)` reports a repair plan. It can create missing
directories, move corrupted JSON files to `dead_letters/corrupted`, and remove
temporary files. Dry-run is the default for CLI and API repair entry points.

## Snapshot And Restore

`create_snapshot(name=None, include_results=False)` copies `spiders`, `tasks`,
`hashes`, `watermarks`, `checkpoints`, `schedules`, worker `queue`, `workers`,
`lifecycle_events`, `lifecycle_signals`, `sessions`, and
`storage_metadata.json`. Results are included only when requested.
Scheduler and worker run history are operational evidence and are not included
by default.
Each snapshot has a `manifest.json` containing:

- `snapshot_id`
- `name`
- `created_at`
- `included_paths`
- `file_count`
- `total_bytes`

`restore_snapshot(snapshot_id, dry_run=True)` returns a restore plan by default.
Applying a restore first moves existing targets to `dead_letters/restore_backups`.

## CLI

```powershell
python -m crawler_platform.cli storage check
python -m crawler_platform.cli storage repair --dry-run
python -m crawler_platform.cli storage snapshot create
python -m crawler_platform.cli storage snapshot list
python -m crawler_platform.cli storage snapshot restore <snapshot_id> --dry-run
python -m crawler_platform.cli incremental watermark list
python -m crawler_platform.cli incremental checkpoint list
```

Add `--json` to storage commands for machine-readable output where supported.

## FastAPI

Storage endpoints:

- `GET /storage/health`
- `POST /storage/repair`
- `POST /storage/snapshots`
- `GET /storage/snapshots`
- `POST /storage/snapshots/{snapshot_id}/restore`
- `GET /incremental/watermarks`
- `GET /incremental/checkpoints`
- `POST /incremental/checkpoints/{task_id}/resume`

Repair and restore default to dry-run.

## Database Boundary

No database or ORM dependency is required by runtime code. A future database
migration should replace only the `storage` layer while preserving engine,
extractor, config, API, and export semantics.
