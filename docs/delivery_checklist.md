# Delivery Checklist

Use this checklist before handing the crawler platform to the ChatGPT final
acceptance review.

## Source Modules

- `src/crawler_platform/config_loader.py`
- `src/crawler_platform/validation.py`
- `src/crawler_platform/models.py`
- `src/crawler_platform/storage.py`
- `src/crawler_platform/engine.py`
- `src/crawler_platform/http_client.py`
- `src/crawler_platform/extractor.py`
- `src/crawler_platform/request_governance.py`
- `src/crawler_platform/playwright_runner.py`
- `src/crawler_platform/scheduler.py`
- `src/crawler_platform/worker.py`
- `src/crawler_platform/lifecycle.py`
- `src/crawler_platform/session.py`
- `src/crawler_platform/observability.py`
- `src/crawler_platform/exporter.py`
- `src/crawler_platform/api.py`
- `src/crawler_platform/cli.py`
- `src/crawler_platform/examples.py`

## Entrypoints

- CLI: `python -m crawler_platform.cli --help`
- FastAPI: `uvicorn crawler_platform.api:create_app --factory --reload`
- Web Admin: `/admin` from the FastAPI app.
- Examples CLI: `python -m crawler_platform.cli examples list`

## Examples And Templates

- Example index: `examples/index.json`
- Runnable local examples: `examples/*.json`
- Templates: `examples/templates/*.json`
- Fixtures: `examples/fixtures/*`
- Example docs: `examples/README.md` and [docs/examples.md](docs/examples.md)

## Documentation

- Project entry: `README.md`
- Architecture and user docs: [docs/architecture.md](docs/architecture.md),
  [docs/user_guide.md](docs/user_guide.md),
  [docs/developer_guide.md](docs/developer_guide.md)
- Runtime docs: [docs/cli.md](docs/cli.md), [docs/api.md](docs/api.md),
  [docs/web_admin.md](docs/web_admin.md), [docs/config.md](docs/config.md),
  [docs/storage.md](docs/storage.md)
- Quality docs: [docs/testing.md](docs/testing.md),
  [docs/test_matrix.md](docs/test_matrix.md),
  [docs/final_acceptance.md](docs/final_acceptance.md)
- Workflow and status: [docs/codex_workflow.md](docs/codex_workflow.md),
  [docs/feature_status.md](docs/feature_status.md)

## Scripts And Tests

- Quality gate: `scripts/quality_gate.py`
- Test matrix runner: `scripts/run_test_matrix.py`
- Pytest suite: `tests/test_*.py`
- Matrix metadata: `tests/feature_matrix.json`, `tests/cli_matrix.json`,
  `tests/api_matrix.json`

## Quality Gate

Run the quick gate before normal handoff:

```powershell
python scripts/quality_gate.py --quick --json-report ./test-output/feature21-final/quality-quick.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/feature21-final/matrix-quick.json
```

Run full gates when time allows:

```powershell
python scripts/quality_gate.py --full --json-report ./test-output/feature21-final/quality-full.json
python scripts/run_test_matrix.py --full --json-report ./test-output/feature21-final/matrix-full.json
```

## Data Directory

Runtime state is written under the selected `--data-dir` or API `data_dir`.
The default is `data/`; tests normally use isolated directories under
`test-output/`.

The runtime state contains spiders, tasks, results, hashes, watermarks,
checkpoints, schedules, worker queue files, lifecycle events, sessions,
observability artifacts, exports, snapshots, locks, and temporary files.

## Do Not Commit

- `test-output/`
- `data/`
- `.pytest_cache/`
- Generated export files and manifests outside examples.
- Temporary lock, snapshot, or cache artifacts.
- Local browser profile data.

## Boundaries To Tell Reviewers

- No runtime database dependency.
- No production deployment pipeline.
- No RBAC or permission model.
- No external distributed queue.
- No real third-party login or captcha bypass.
- Playwright real-browser execution remains optional.

## Rollback And Cleanup

- Remove generated `test-output/feature21-final/` reports when no longer
  needed.
- Remove local `data/` or custom runtime directories created during manual
  tests.
- Re-run `python -m compileall -q src tests scripts` and `pytest -q` after any
  cleanup that touches source, scripts, tests, or docs.
