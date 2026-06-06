# CLI Manual

All commands below use local fixtures or empty local FileStore roots.

## validate

```powershell
python -m crawler_platform.cli validate examples/local_api_json.json
python -m crawler_platform.cli validate examples/local_html_list.json
```

## doctor

```powershell
python -m crawler_platform.cli doctor
python -m crawler_platform.cli doctor --data-dir ./data-demo
python -m crawler_platform.cli doctor --json
```

Doctor is offline. It checks package imports, CLI availability, optional API
startup, examples, storage health, writable data paths, quick quality-gate
availability, dependency boundaries, and ignore rules for generated artifacts.

## run

```powershell
python -m crawler_platform.cli run examples/local_api_json.json --data-dir test-output/cli-api
python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-demo --data-dir test-output/cli-api
python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-json --data-dir test-output/cli-api --json
python -m crawler_platform.cli run examples/local_html_list.json --data-dir test-output/cli-html
python -m crawler_platform.cli run examples/crawl_policy_local.json --dry-run --json
```

The default run output is a human summary with task id, record counts, result
path, report path, log and metric paths, and the next export command. Add
`--json` for machine output.

## plan

```powershell
python -m crawler_platform.cli plan examples/crawl_policy_local.json
python -m crawler_platform.cli plan examples/crawl_policy_local.json --json
```

Plan is a request-scope and risk preview. It normalizes configured URLs,
checks crawl policy, summarizes robots decisions from local rules, and prints
blocked URLs and warnings. It does not fetch pages or save crawl results.

## dry-run

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/cli-debug
python -m crawler_platform.cli debug dry-run examples/debug_transforms.json --data-dir test-output/cli-debug-transforms --json
python -m crawler_platform.cli dry-run examples/debug_quality_report.json --data-dir test-output/cli-debug-quality --save-report
python -m crawler_platform.cli debug selector examples/fixtures/crawl_policy_page.html --selector ".title" --selector-type css --json
python -m crawler_platform.cli debug extract examples/debug_extract_local.json --input-file examples/fixtures/debug_extract_items.json --json
```

Dry-run previews the first target by default and prints samples, selector
diagnostics, warnings, and field quality. Use `--max-pages`, `--max-records`,
and `--sample-size` to adjust preview scope. It does not write formal crawl
results or incremental state.

## examples

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples list --quickstart
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir test-output/cli-examples
python -m crawler_platform.cli examples copy template-api-basic --to test-output/copied-template.json
```

## init

```powershell
python -m crawler_platform.cli init spider --type api --output test-output/my-api-spider.json
python -m crawler_platform.cli init spider --type http --output test-output/my-http-spider.json
python -m crawler_platform.cli init spider --template detail_follow --output test-output/my-detail-spider.json
```

The init command copies a local template backed by local fixtures and validates
the resulting SpiderConfig.

## storage

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-store storage check
python -m crawler_platform.cli --data-dir test-output/cli-store storage repair --dry-run
python -m crawler_platform.cli --data-dir test-output/cli-store storage snapshot create
python -m crawler_platform.cli --data-dir test-output/cli-store storage snapshot list
```

## incremental

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-store incremental watermark list
python -m crawler_platform.cli --data-dir test-output/cli-store incremental checkpoint list
```

## scheduler

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-scheduler scheduler register examples/scheduled_interval.json
python -m crawler_platform.cli --data-dir test-output/cli-scheduler scheduler list
python -m crawler_platform.cli --data-dir test-output/cli-scheduler scheduler run-due --now 2026-06-03T00:00:00Z
```

## worker

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-worker worker enqueue examples/worker_api_job.json
python -m crawler_platform.cli --data-dir test-output/cli-worker worker run-once
python -m crawler_platform.cli --data-dir test-output/cli-worker worker jobs
python -m crawler_platform.cli --data-dir test-output/cli-worker worker stats
```

## task lifecycle

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-tasks task show <task_id>
python -m crawler_platform.cli --data-dir test-output/cli-tasks task show <task_id> --paths
python -m crawler_platform.cli --data-dir test-output/cli-tasks task events <task_id>
python -m crawler_platform.cli --data-dir test-output/cli-tasks task lifecycle <task_id>
```

Lifecycle mutation commands include `pause`, `resume`, `cancel`, `retry`, and
`rerun`.

## session

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-session session list
python -m crawler_platform.cli --data-dir test-output/cli-session session events
python -m crawler_platform.cli --data-dir test-output/cli-session session show <profile_id>
```

## observability

```powershell
python -m crawler_platform.cli --data-dir test-output/cli-observability observability logs
python -m crawler_platform.cli --data-dir test-output/cli-observability observability metrics
python -m crawler_platform.cli --data-dir test-output/cli-observability observability report task <task_id>
python -m crawler_platform.cli --data-dir test-output/cli-observability observability trace <trace_id>
```

## export

```powershell
python -m crawler_platform.cli export list --data-dir test-output/cli-export
python -m crawler_platform.cli export task <task_id> --format jsonl --data-dir test-output/cli-export
python -m crawler_platform.cli export task <task_id> --format csv --limit 100 --offset 0 --data-dir test-output/cli-export
python -m crawler_platform.cli export show <export_id> --data-dir test-output/cli-export
```

Use `--limit` and `--offset` for large task result files so quick inspections do
not need to export the whole result set.

## quality gate and test matrix

```powershell
python scripts/quality_gate.py --quick --json-report ./test-output/feature20-docs/quick-report.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/feature20-docs/matrix-report.json
```
