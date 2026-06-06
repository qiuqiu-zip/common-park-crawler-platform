# Examples And Templates

Feature 18 provides a complete local examples library for the crawler platform.
The library is file-backed and uses only local fixtures, so validation and smoke
checks do not require network access or a database.

## Structure

- `examples/index.json` is the manifest for all indexed examples and templates.
- `examples/*.json` are runnable or reference spider configurations.
- `examples/fixtures/*` contains deterministic HTML/JSON fixture responses.
- `examples/templates/*.json` contains copy-ready starter configurations.
- `examples/README.md` is the quick reference for users browsing the examples.

Every manifest entry includes:

- `id`: stable example id for CLI/API lookup.
- `title`: user-facing label.
- `feature`: feature domain covered by the example.
- `path`: repository-relative artifact path.
- `fixture_paths`: optional explicit fixture paths; the service also discovers
  local fixture paths from the config.
- `tags`: searchable capability tags.
- `runnable`: whether the example can be executed as a local example.
- `requires_playwright`: whether execution needs the optional Playwright extra.
- `requires_external_network`: always false for the current library.
- `expected`: smoke expectations for runnable smoke examples.

Templates are indexed with `template: true`. They are valid spider configs with
local defaults, but are marked `runnable: false` because they are intended to be
copied and edited.

## CLI

```powershell
python -m crawler_platform.cli examples list
python -m crawler_platform.cli examples show local-api-json
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir ./test-output/feature18-examples
python -m crawler_platform.cli examples copy template-api-basic --to ./my-spider.json
```

Add `--json` to `list`, `show`, `validate`, `smoke`, or `copy` for structured
output.

The default smoke subset covers HTTP/API, pagination, detail follow,
incremental dedup, scheduler, worker, session, observability, and export. It
skips Playwright examples by default because Playwright is an optional extra.

## API

The FastAPI app exposes examples through the same response envelope as other
management APIs:

- `GET /examples`
- `GET /examples/{example_id}`
- `POST /examples/validate`
- `POST /examples/smoke`

`POST /examples/smoke` accepts optional JSON:

```json
{
  "data_dir": "./test-output/feature18-examples",
  "ids": ["local-api-json"]
}
```

## Web Admin

The Web admin has an Examples view that reads `/examples`, shows the selected
example JSON, and can call `/examples/validate` and `/examples/smoke`. This is a
minimal examples reference surface, not a full workflow builder.

## Adding An Example

1. Add local fixtures under `examples/fixtures`.
2. Add the spider config under `examples`.
3. Keep `start_urls`, pagination URLs, and session flow URLs local.
4. Add an entry to `examples/index.json` with unique `id`, tags, and expected
   smoke values when applicable.
5. Run:

```powershell
python -m crawler_platform.cli examples validate
python -m crawler_platform.cli examples smoke --data-dir ./test-output/feature18-examples
```

## Boundaries

This feature does not add Feature 19's full test matrix, Feature 20's final docs,
RBAC/auth, database migrations, external integrations, or new crawler engine
semantics. The platform remains file-backed; `docs/schema.sql` is still only a
future database proposal.
