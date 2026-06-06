# User Guide

## Create A Spider

Start from a bundled template:

```powershell
python -m crawler_platform.cli examples copy template-api-basic --to test-output/my-api-spider.json
```

Edit the copied file and keep canonical fields such as `type`, `start_urls`,
`fields`, and `scheduler`.

## Validate Configuration

```powershell
python -m crawler_platform.cli validate test-output/my-api-spider.json
python -m crawler_platform.cli validate examples/local_api_json.json
```

## Run Tasks

```powershell
python -m crawler_platform.cli run examples/local_api_json.json --data-dir test-output/user-api
python -m crawler_platform.cli run examples/local_html_list.json --data-dir test-output/user-html
```

Task metadata is written under `tasks/`; records are written under `results/`.

## View Results

```powershell
python -m crawler_platform.cli --data-dir test-output/user-api list tasks
```

Result files are JSONL files under `test-output/user-api/results/`.

## Export Results

```powershell
python -m crawler_platform.cli export list --data-dir test-output/user-api
```

After a successful task run, export the task with:

```powershell
python -m crawler_platform.cli export task <task_id> --format jsonl --data-dir test-output/user-api
```

## Web Admin

Run FastAPI and open the admin console:

```powershell
uvicorn crawler_platform.api:create_app --factory --reload
```

Use Web Admin to inspect runtime status, spiders, tasks, scheduler runs, worker
jobs, sessions, observability data, exports, and examples.

## Scheduler

```powershell
python -m crawler_platform.cli --data-dir test-output/user-scheduler scheduler register examples/scheduled_interval.json
python -m crawler_platform.cli --data-dir test-output/user-scheduler scheduler list
python -m crawler_platform.cli --data-dir test-output/user-scheduler scheduler run-due --now 2026-06-03T00:00:00Z
```

## Worker

```powershell
python -m crawler_platform.cli --data-dir test-output/user-worker worker enqueue examples/worker_api_job.json
python -m crawler_platform.cli --data-dir test-output/user-worker worker run-once
python -m crawler_platform.cli --data-dir test-output/user-worker worker jobs
python -m crawler_platform.cli --data-dir test-output/user-worker worker stats
```

## Session

```powershell
python -m crawler_platform.cli --data-dir test-output/user-session session list
python -m crawler_platform.cli --data-dir test-output/user-session session events
```

Session events are redacted before storage.

## Observability

```powershell
python -m crawler_platform.cli --data-dir test-output/user-observability observability logs
python -m crawler_platform.cli --data-dir test-output/user-observability observability metrics
python -m crawler_platform.cli --data-dir test-output/user-observability observability trace <trace_id>
```

Run reports, trace timelines, logs, metrics, and record samples are local
FileStore artifacts.

## Examples And Templates

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir test-output/user-examples
```
