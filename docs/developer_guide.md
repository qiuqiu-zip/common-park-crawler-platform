# Developer Guide

## Code Structure

- `models.py`: dataclasses and enums for spider configs and runtime records.
- `validation.py`: schema generation and validation rules.
- `config_loader.py`: config loading and dumping.
- `engine.py`: task execution.
- `extractor.py`: field extraction.
- `http_client.py` and `playwright_runner.py`: fetch backends.
- `storage.py`: file-backed persistence.
- `scheduler.py`, `worker.py`, and `lifecycle.py`: orchestration.
- `session.py`, `observability.py`, and `exporter.py`: cross-cutting services.
- `api.py`: FastAPI app.
- `cli.py`: command-line interface.
- `examples.py`: examples index, validation, smoke, and copy helpers.

## Add A Field Extractor

1. Add the extraction behavior in `extractor.py`.
2. Add validation for the canonical field shape in `validation.py`.
3. Add unit tests in `tests/test_extractor.py`.
4. Add or update a local example under `examples/`.
5. Update `docs/config.md` and `docs/user_guide.md` if user-facing behavior
   changes.

## Add A Pagination Type

1. Extend the pagination config model and validation.
2. Update engine pagination handling.
3. Add deterministic tests with local fixtures.
4. Add an example and include it in `examples/index.json`.
5. Update `docs/pagination.md` and `docs/config.md`.

## Add An Export Format

1. Implement format output in `exporter.py`.
2. Wire CLI/API options if needed.
3. Add tests in `tests/test_exporter.py`.
4. Update `docs/exporter.md` and `docs/cli.md`.

## Add An API Endpoint

1. Add the route in `api.py` using existing service classes.
2. Preserve the `{ok,data,error,meta}` envelope for JSON responses.
3. Add ASGI tests without introducing `httpx`.
4. Update `docs/api.md` and `tests/api_matrix.json`.

## Add A CLI Command

1. Add parser wiring in `cli.py`.
2. Keep `--json` output for machine-readable command groups where practical.
3. Add tests for the command behavior.
4. Update `docs/cli.md` and `tests/cli_matrix.json`.

## Add An Example

1. Put local fixture data under `examples/fixtures/`.
2. Add the spider JSON under `examples/`.
3. Register it in `examples/index.json`.
4. Ensure `requires_external_network=false` for default runnable examples.
5. Run `python -m crawler_platform.cli examples validate`.

## Add A Test Matrix Item

Update `tests/feature_matrix.json`, `tests/cli_matrix.json`, or
`tests/api_matrix.json`. Matrix entries are read by pytest and by
`scripts/run_test_matrix.py`.

## Quality Gate

```powershell
python scripts/quality_gate.py --quick --json-report ./test-output/quality/quick-report.json
python scripts/quality_gate.py --full --json-report ./test-output/quality/full-report.json
```

## Constraints

- Do not add database runtime dependencies.
- Do not make default tests access real external network targets.
- Keep Playwright real-browser tests optional and skippable.
- Keep examples deterministic through local fixtures or fakes.
