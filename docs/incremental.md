# Incremental Crawling

Feature 07 adds file-backed incremental crawling. It covers configurable dedup,
watermarks, checkpoints, and resume. It does not add Playwright Engine, Browser
Pool, Proxy Manager, Anti-bot, Scheduler, Worker queues, Web UI completion, or a
database.

## DedupConfig

`dedup.enabled=false` disables dedup and writes every extracted record.
When `dedup.enabled=true`, the engine computes a hash after list extraction and
detail-page merging.

Fields:

- `dataset`: logical hash index name. Defaults to the spider id.
- `keys`: field paths used in the hash, such as `id` or `detail.id`.
- `hash_method`: `sha256` or `md5`.
- `scope`: `global`, `spider`, or `task`.
- `skip_existing`: when true, duplicate hashes are skipped; when false,
  duplicates are written and marked.
- `missing_key_policy`: `error`, `warn`, `skip`, or `allow_empty`.
- `include_source_url`: include the source URL in the hash payload.

Saved records include `_dedup` metadata:

```json
{
  "_dedup": {
    "hash": "...",
    "dataset": "articles",
    "scope": "spider",
    "is_duplicate": false,
    "keys": ["detail.id"]
  }
}
```

Legacy `unique_fields` remains a compatibility path when no explicit `dedup`
block is provided.

## Watermark

Watermarks are stored under `data/watermarks/<spider_id>/<dataset>.json`.

```json
{
  "watermark": {
    "enabled": true,
    "dataset": "articles",
    "field": "published_at",
    "strategy": "max",
    "type": "datetime",
    "format": "%Y-%m-%d",
    "stop_when_older": true
  }
}
```

Supported `strategy` values are `max` and `min`. Supported `type` values are
`string`, `int`, `float`, and `datetime`. When `stop_when_older=true`, pagination
stops after the first page that crosses the stored boundary.

## Checkpoint And Resume

The engine saves checkpoints under `data/checkpoints/<task_id>.json` after each
page or batch. A checkpoint records the task, spider, current page, next URL,
pagination params, offset/cursor hints, and watermark candidate. Successful tasks
mark the checkpoint as `completed`.

Failed or cancelled tasks can be resumed when their checkpoint is not completed:

```powershell
python -m crawler_platform.cli --data-dir data incremental checkpoint resume <task_id>
```

Resume uses the saved next URL and existing hash index, so already written
records are not written again.

## CLI

```powershell
python -m crawler_platform.cli run examples/incremental_dedup.json --data-dir test-output/feature07-dedup
python -m crawler_platform.cli --data-dir test-output/feature07-dedup incremental watermark list
python -m crawler_platform.cli --data-dir test-output/feature07-dedup incremental checkpoint list
python -m crawler_platform.cli --data-dir test-output/feature07-dedup incremental checkpoint resume <task_id>
```

## FastAPI

- `GET /incremental/watermarks`
- `GET /incremental/checkpoints`
- `POST /incremental/checkpoints/{task_id}/resume`

These endpoints use the same FileStore state as CLI runs and require no
permission system.

## Examples

- `examples/incremental_dedup.json`: duplicate run skips existing records.
- `examples/incremental_watermark.json`: datetime watermark update.
- `examples/resume_checkpoint.json`: checkpoint state for resume.
- `examples/pagination_detail_incremental.json`: pagination, detail following,
  nested-field dedup, and file-backed hash indexes together.

All examples use local fixtures and do not access the network.

## No Database

Runtime code remains file-backed. SQL in `docs/schema.sql` is still design
documentation only and is not imported or executed.
