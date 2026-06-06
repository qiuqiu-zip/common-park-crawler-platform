# Quick Start

This guide uses local fixtures only and stores runtime artifacts in a local
FileStore directory.

## Install

```powershell
python -m pip install -e ".[api,dev]"
crawler-platform --help
```

For an uninstalled source checkout:

```powershell
$env:PYTHONPATH='src'
python -m crawler_platform.cli --help
```

On Windows, `py -m crawler_platform.cli ...` is equivalent when `python` is not
on `PATH`.

## Doctor

```powershell
python -m crawler_platform.cli doctor
python -m crawler_platform.cli doctor --data-dir ./data-demo
python -m crawler_platform.cli doctor --json
```

`doctor` checks imports, CLI availability, optional FastAPI/OpenAPI creation,
the examples index, examples validation, FileStore health, data directory
writability, quality-gate availability, dependency boundaries, and generated
artifact ignore rules. It does not contact external sites.

## Run A Local API Spider

```powershell
python -m crawler_platform.cli validate examples/local_api_json.json
python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-demo --data-dir ./data-demo
python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-json --data-dir ./data-demo --json
```

Records and reports are written under:

- `./data-demo/tasks/quickstart-demo.json`
- `./data-demo/results/quickstart-demo.jsonl`
- `./data-demo/observability/reports/tasks/quickstart-demo.json`
- `./data-demo/observability/logs/tasks/quickstart-demo.jsonl`
- `./data-demo/observability/metrics/tasks/quickstart-demo.jsonl`

Do not commit runtime artifacts from `data/`, `data-demo/`, `test-output/`,
`.pytest_cache/`, `pytest-cache-files-*/`, `__pycache__/`, or `*.pyc` files.
`doctor` reports whether the standard generated-output ignore rules are present.

Inspect and export the task:

```powershell
python -m crawler_platform.cli list tasks --data-dir ./data-demo
python -m crawler_platform.cli task show quickstart-demo --paths --data-dir ./data-demo
python -m crawler_platform.cli observability report task quickstart-demo --data-dir ./data-demo
python -m crawler_platform.cli export task quickstart-demo --format json --data-dir ./data-demo
python -m crawler_platform.cli export task quickstart-demo --format csv --limit 100 --offset 0 --data-dir ./data-demo
python -m crawler_platform.cli storage check --data-dir ./data-demo
```

## Examples And Starter Configs

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples list --quickstart
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples copy template-api-basic --to ./data-demo/copied-api-template.json
python -m crawler_platform.cli init spider --type api --output ./data-demo/my-api-spider.json
python -m crawler_platform.cli validate ./data-demo/my-api-spider.json
python -m crawler_platform.cli examples smoke --data-dir ./data-demo/examples-smoke
```

The recommended first examples are `local-api-json`, `local-html-list`, and
`pagination-page`. `detail-follow` is the next useful step once list extraction
is clear.

## Run A Local HTML Spider

```powershell
python -m crawler_platform.cli run examples/local_html_list.json --data-dir ./data-demo/html
```

## Start The API And Web Admin

```powershell
uvicorn crawler_platform.api:create_app --factory --reload
```

Open `http://127.0.0.1:8000/admin`.

## Run Quality Gates

```powershell
python -m compileall -q src tests scripts
pytest -q
python scripts/quality_gate.py --quick --json-report ./test-output/feature20-docs/quick-report.json
python scripts/run_test_matrix.py --quick --json-report ./test-output/feature20-docs/matrix-report.json
```
