# Testing And Quality Gates

Feature 19 adds a repeatable testing and quality gate layer for the crawler
platform. It does not perform final project acceptance, publishing, deployment,
RBAC, or database migration work.

## Test Layers

- Unit tests cover config validation, extraction, storage, request governance,
  scheduling, workers, lifecycle, sessions, observability, exporters, and
  examples.
- Integration tests cover local crawler runs, FastAPI ASGI endpoints, Web Admin
  static assets, examples validation, and examples smoke runs.
- Matrix tests read JSON matrix files from `tests/` and fail when a Feature,
  CLI command group, endpoint group, or examples smoke category is missing.
- Quality gates combine static checks, local smoke checks, and optional heavy
  test execution into one JSON report.

## Quick And Full

`--quick` is the default developer gate. It compiles source, tests, and scripts;
validates the Feature, CLI, API, and examples matrices; runs examples validate;
runs the local examples smoke subset; checks OpenAPI generation; scans runtime
code for database or ORM dependencies; checks examples for external URLs; scans
example/report JSON for obvious sensitive values; and checks Web Admin static
assets and required docs.

`--full` includes the quick checks and also runs the full pytest subprocess.
Use it before handoff when time allows. The standalone acceptance command
`pytest -q` is still run separately so test failures remain easy to inspect.

## Commands

```powershell
python -m compileall -q src tests scripts
python -m pytest -q -p no:cacheprovider
python scripts/quality_gate.py --quick --json-report ./test-output/delivery/quality-quick.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/delivery/matrix-quick.json
python scripts/quality_gate.py --full --json-report ./test-output/delivery/quality-full.json
python scripts/run_test_matrix.py --full --json-report ./test-output/delivery/matrix-full.json
python -m crawler_platform.cli examples smoke --data-dir ./test-output/feature19-examples
```

If the local `python` command is not usable, run the same commands with `py`.
On Windows, `scripts/verify_delivery.ps1` runs the delivery checks in sequence:

```powershell
.\scripts\verify_delivery.ps1
.\scripts\verify_delivery.ps1 -Full
.\scripts\verify_delivery.ps1 -SkipInstall
```

Do not run `quality_gate.py` and `run_test_matrix.py` in parallel when they use
the same default output directory. They create local FileStore state while they
run, so parallel execution can produce false failures. Prefer
`test-output/delivery/` or another short report directory on Windows.

When reading command output that may contain non-ASCII fixture text, set:

```powershell
$env:PYTHONIOENCODING='utf-8'
```

## No External Network

Default tests and quality gates must remain offline. Examples use local files
under `examples/fixtures/`, FakeFetcher-style test doubles, or local rendered
fixtures. Runnable examples must keep `requires_external_network=false`, and the
quality gate scans example JSON for real `http://` or `https://` URLs.

Real browser or remote network tests are optional. If the environment does not
have Playwright browsers installed, tests must skip with a clear reason instead
of failing the default matrix.

## No Database Runtime

Runtime code remains file-backed. SQL may exist only as documentation in
`docs/schema.sql`; it is not executed by the platform. The quality gate scans
`src/` and `pyproject.toml` for database libraries and ORM imports. The matching
acceptance command is:

```powershell
rg -n -i "\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b" src pyproject.toml
```

The expected result is no matches.

## Sensitive Values

The sensitive-value gate focuses on JSON and JSONL artifacts in examples and the
Feature 19 report directory. Sensitive field names may exist, but their values
must be fake, local fixture values, placeholders, or redacted strings. The gate
checks keys such as `password`, `passwd`, `secret`, `token`, `authorization`,
`cookie`, `api_key`, `access_key`, and `private_key`.

## JSON Report

Quality scripts emit reports shaped like:

```json
{
  "status": "passed",
  "started_at": "2026-06-04T00:00:00+00:00",
  "finished_at": "2026-06-04T00:00:01+00:00",
  "duration_ms": 1000,
  "checks": [
    {"name": "feature_matrix", "status": "passed", "details": {}}
  ],
  "summary": {"passed": 1, "failed": 0, "skipped": 0}
}
```

Failed checks include a reason, command, or output summary where applicable.

## Adding Matrix Items

When a new Feature is added, update `tests/feature_matrix.json` with the Feature
id, name, unit tests, integration tests, CLI tests, API tests, examples, docs,
required commands, `offline_only=true`, and `database_free=true`. If a category
does not apply, add a short reason in `not_applicable`.

Add new CLI command groups to `tests/cli_matrix.json` and new API paths to
`tests/api_matrix.json`. Keep quick entries deterministic and offline. Heavy
commands can be marked `mode=full`; commands that need prepared state can be
documented with `execute=false` and a reason.
