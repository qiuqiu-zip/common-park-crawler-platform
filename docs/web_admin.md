# Web Admin Console

Feature 17 adds a package-local Web management console at `/admin`. The console
is served by FastAPI from `src/crawler_platform/web/admin` and has no separate
build step, CDN dependency, database dependency, permission system, or external
network requirement.

## Entry Points

- `GET /admin`
- `GET /admin/`
- `GET /admin/assets/app.js`
- `GET /admin/assets/api.js`
- `GET /admin/assets/components.js`
- `GET /admin/assets/styles.css`

Run it with:

```powershell
python -m pip install -e ".[api]"
uvicorn crawler_platform.api:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/admin`.

## Static Resource Structure

```text
src/crawler_platform/web/admin/
  index.html
  README.md
  assets/
    api.js
    app.js
    components.js
    styles.css
```

The HTML loads local CSS and JavaScript through `/admin/assets/...`. JavaScript
uses root-relative API paths such as `/spiders`, `/tasks`, and `/worker/jobs`.
There are no CDN imports, package-manager assets, or hard-coded local absolute
paths.

## API Envelope Handling

`assets/api.js` centralizes API calls. It unwraps Feature 16 responses shaped as
`{ok,data,error,meta}` and raises `ApiClientError` when `ok` is false. The same
module redacts sensitive keys such as password, token, authorization, cookie,
api_key, and session before values are rendered.

The FastAPI middleware skips `/admin` and `/admin/assets/...`, so static HTML,
CSS, and JavaScript are returned as normal static resources. JSON API endpoints
continue to use the Feature 16 envelope.

## Page Modules

Dashboard:

- Start here actions for trying the local API example, opening examples,
  opening spiders, and opening tasks
- Quickstart example table using local fixtures
- Runtime info
- Capabilities
- Storage health
- Task, worker, scheduler, and export counts
- Recent observability logs

Spider management:

- List spiders
- Select detail
- JSON editor
- Format JSON
- Validate config through `POST /spiders/validate`
- Save through `POST /spiders` or `PUT /spiders/{spider_id}`
- Delete through `DELETE /spiders/{spider_id}`
- Run through `POST /tasks/run`

Task and result management:

- List tasks with `limit`, `offset`, `status`, and `spider_id`
- Select task detail
- View results with `limit` and `offset`
- View report, logs, and metrics
- Pause, resume, cancel, retry, and rerun
- Export task results

Scheduler and worker management:

- List schedules and scheduler runs
- Create schedules from JSON
- Run due, enqueue due, trigger, pause, resume, and disable
- List worker jobs
- Enqueue a job from JSON
- Run once, run until empty, recover, stats, dead letters, job events
- Pause, resume, cancel, and retry jobs

Storage and session management:

- Storage health
- Repair dry-run
- Snapshot create/list/restore dry-run
- Session list/detail
- Session clear/delete
- Session events

Observability and export management:

- Logs and metrics
- Task, job, scheduler, and trace lookups
- Export list
- Create task, job, scheduler, and log exports
- Manifest display
- Download link
- Delete export

Examples:

- Quickstart examples surfaced before the full index
- Examples index
- Example/template detail JSON
- Validate examples
- Smoke local runnable examples
- Copy JSON
- Save selected example as a spider
- Run selected example and jump to the task view

## Boundary

This feature does not add login, RBAC, database migration runtime, external
object storage, message notification, distributed deployment, desktop client, or
final platform acceptance. Runtime state remains file-backed through `FileStore`;
SQL remains documentation-only in `docs/schema.sql`.

## Testing

Feature 17 uses ASGI tests and static resource checks rather than browser
automation:

```powershell
pytest -q tests/test_web_admin.py -p no:cacheprovider
```

The tests cover `/admin`, static JS/CSS availability, local-only resources,
relative API paths, OpenAPI generation, API envelope error handling helpers, JSON
editor helper presence, module API path coverage, and the no-database runtime
constraint.
