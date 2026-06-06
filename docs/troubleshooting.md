# Troubleshooting

## Import Fails In A Source Checkout

Install editable mode or set `PYTHONPATH`:

```powershell
python -m pip install -e ".[api,dev]"
$env:PYTHONPATH='src'
```

If `crawler-platform --help` is not available because the package has not been
installed, use the source checkout form:

```powershell
$env:PYTHONPATH='src'
py -m crawler_platform.cli --help
```

## CLI Command Cannot Find A File

Run commands from the repository root. Example paths such as
`examples/local_api_json.json` are repository-relative.

## Selector Or Field Output Looks Wrong

Run a dry-run before a formal crawl:

```powershell
python -m crawler_platform.cli dry-run examples/debug_dry_run.json --data-dir test-output/troubleshooting-debug --json
```

Check `field_diagnostics` for match counts and missing required fields, then
check `field_quality` for empty values and missing rates. Use `--save-report`
to keep the report under `debug_reports/`.

## Tests Pass With A Pytest Cache Warning

If pytest reports a `.pytest_cache` write warning but all tests pass, the warning
is a local cache directory permission issue. It does not change the test result.

## Quality Gate Or Matrix Has A False Failure

Do not run `scripts/quality_gate.py` and `scripts/run_test_matrix.py` in
parallel with the same output directory. Both scripts create temporary FileStore
state while they run. Run them sequentially or use `scripts/verify_delivery.ps1`.

On Windows, keep the repository and report directory paths short. Very deep
temporary paths can exceed path handling limits during atomic writes.

If command output contains replacement characters or a GBK decode warning, set:

```powershell
$env:PYTHONIOENCODING='utf-8'
```

## Playwright Tests Skip

Real browser tests are optional. Local rendered fixture tests can run without a
browser install. Install the optional Playwright extra only when real rendered
crawling is needed.

## Examples Must Stay Offline

Run:

```powershell
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir test-output/troubleshooting-examples
```

If validation fails due to an external URL, replace the default example target
with a local fixture path.

## API Probe Cannot Import The Package

For an uninstalled source checkout:

```powershell
$env:PYTHONPATH='src'
python -c "from crawler_platform.api import create_app; app = create_app(); print(type(app).__name__); print(bool(app.openapi()))"
```

## Storage Looks Corrupted

Use dry-run checks first:

```powershell
python -m crawler_platform.cli --data-dir data storage check
python -m crawler_platform.cli --data-dir data storage repair --dry-run
```

## Database Dependency Scan Finds A Match

Runtime source should not import database drivers or ORM libraries. Keep SQL
notes in `docs/schema.sql` only, and remove runtime imports from `src/` or
`pyproject.toml`.
