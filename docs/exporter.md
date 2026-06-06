# Exporter

Feature 15 adds a local Exporter for task results, run reports, lifecycle events, and observability logs. It writes files under FileStore and records an export manifest for list/show/download/delete workflows.

This feature is local only. It does not add Web UI pages, a permission system, external object storage, or runtime SQL/ORM storage.

## Config

Every spider may define `export`:

```json
{
  "export": {
    "enabled": true,
    "formats": ["json", "jsonl", "csv", "xlsx"],
    "default_format": "jsonl",
    "output_dir": null,
    "include_fields": [],
    "exclude_fields": [],
    "field_aliases": {},
    "include_metadata": false,
    "include_dedup": true,
    "redact_sensitive": true,
    "nested_strategy": "flatten_dot",
    "list_strategy": "json_string",
    "join_separator": ",",
    "include_observability": false,
    "include_lifecycle": false,
    "manifest_enabled": true
  }
}
```

Supported formats are `json`, `jsonl`, `csv`, and `xlsx`. `default_format` must be present in `formats`.

## Field Shaping

Nested dict values support these strategies:

- `flatten_dot`: `detail.author.name`
- `flatten_underscore`: `detail_author_name`
- `json_string`: one JSON string value in the parent column

List values support:

- `json_string`: JSON encoded list
- `join`: scalar values joined with `join_separator`

`include_fields` keeps only selected shaped field names. `exclude_fields` removes selected shaped field names. `field_aliases` renames final columns.

Metadata keys beginning with `_` are hidden by default except `_dedup`, which is included when `include_dedup` is true. Set `include_metadata` to include all metadata, including task/job metadata attached by exporter workflows.

Sensitive fields containing `password`, `secret`, `token`, `authorization`, or `cookie`, plus exact `session_id`, `sessionid`, or `sid`, are redacted as `***REDACTED***` when `redact_sensitive` is true.

## Export Sources

Exporter supports:

- task result records
- task run reports
- worker job results and job reports
- scheduler run reports
- observability logs
- lifecycle events

Exports create a manifest with `export_id`, `source_type`, `source_id`, `format`, `path`, `rows_count`, `columns`, `status`, `created_at`, and file size.

## FileStore Layout

```text
data/exports/
  files/
  manifests/
```

`storage check` counts export files and validates export manifests. Snapshots include the `exports` directory.

## CLI

```bash
python -m crawler_platform.cli export task <task_id> --format json --data-dir ./data
python -m crawler_platform.cli export task <task_id> --format csv --include-fields id title --data-dir ./data
python -m crawler_platform.cli export task <task_id> --format csv --limit 1000 --offset 0 --data-dir ./data
python -m crawler_platform.cli export job <job_id> --format xlsx --include-metadata --data-dir ./data
python -m crawler_platform.cli export scheduler <scheduler_run_id> --format json --data-dir ./data
python -m crawler_platform.cli export observability logs --task-id <task_id> --format jsonl --data-dir ./data
python -m crawler_platform.cli export lifecycle --target-type task --target-id <task_id> --format json --data-dir ./data
python -m crawler_platform.cli export list --data-dir ./data
python -m crawler_platform.cli export show <export_id> --data-dir ./data
python -m crawler_platform.cli export delete <export_id> --data-dir ./data
```

Use `--output` to choose an output file or directory. Use `--json` to print the complete manifest.
For large task result files, prefer `jsonl` or windowed exports with `--limit`
and `--offset` for inspection batches. Full JSON, CSV, and XLSX outputs still
materialize the selected rows into the output file, so choose a window before
opening very large result sets in desktop tools.

## FastAPI

```http
POST /exports/tasks/{task_id}
POST /exports/jobs/{job_id}
POST /exports/scheduler/{scheduler_run_id}
POST /exports/observability/logs
GET  /exports
GET  /exports/{export_id}
GET  /exports/{export_id}/download
DELETE /exports/{export_id}
```

Create endpoints accept JSON options such as `format`, `output`, `include_fields`, `exclude_fields`, `flatten`, `include_metadata`, and nested `config`.

## Boundaries

Exporter reads existing FileStore data and writes local files plus manifests. It does not mutate crawler result records, does not add external storage, and does not add a UI or permission layer.
