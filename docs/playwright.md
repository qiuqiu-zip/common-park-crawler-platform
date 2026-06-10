# Playwright And Browser Pool

Feature 08 adds a Playwright execution boundary for rendered HTML pages. The
runtime still has no required third-party dependency: HTTP/API spiders continue
without Playwright, and rendered crawls require the `playwright` optional extra
and browser binaries unless they use local already-rendered fixtures.

## Config

Use `type=playwright` or set `playwright.enabled=true`.

```json
{
  "type": "playwright",
  "playwright": {
    "enabled": true,
    "headless": true,
    "browser_pool_size": 2,
    "wait_until": "domcontentloaded",
    "wait_for_selector": ".video-card",
    "wait_for_selector_timeout_ms": 10000,
    "post_load_wait_ms": 1000,
    "scroll_strategy": {
      "enabled": true,
      "mode": "viewport",
      "max_scrolls": 3,
      "scroll_pause_ms": 800,
      "stop_selector": ".video-card"
    }
  }
}
```

`pool_size` is accepted as an alias for `browser_pool_size`. `headless=false`
launches headful browsers when the real Playwright backend is available.

`wait_for_selector` waits for one CSS selector after `page.goto(...)`.
`post_load_wait_ms` adds a fixed pause before extraction. `scroll_strategy`
supports:

- `none`: never scroll.
- `viewport`: scroll by one viewport height per step.
- `incremental`: scroll by roughly half a viewport per step.
- `bottom`: jump to the bottom each step.

`stop_selector` stops scrolling early when a target selector appears. These
controls exist for rendered pages that are technically loaded but still need
DOM readiness or lazy-load triggers before extraction.

## Browser Pool

`BrowserPool` owns up to `browser_pool_size` browser instances. It tracks idle
and active slots, reuses idle browsers, supports concurrent `fetch_many`
requests, and releases slots after render failures. Each fetch creates a fresh
browser context and page so cookies and page state do not leak between requests.

The engine routes rendered requests through `PlaywrightFetcher` for spiders with
`type=playwright` or `playwright.enabled=true`. List pages, pagination pages,
and detail pages all use the same rendered fetch path. After the initial
navigation, the rendered fetch path may wait for a selector, apply a fixed
post-load delay, and perform bounded scrolling before `page.content()` is read.

Feature 13 adds optional session `storage_state` loading before render and
context `storage_state` saving after render. Tests can use
`FakeRenderBackend(storage_state_after_render=...)` without installing browsers.

## Local Fixtures

When Playwright is not installed, local file paths can still be read as
already-rendered HTML fixtures. This keeps CLI and tests deterministic without
network access. Remote rendered pages still require the Playwright optional
dependency and installed browser binaries.

## CLI

```powershell
python -m crawler_platform.cli run examples/playwright_local_fixture.json --data-dir test-output/feature08-playwright-local
```

## FastAPI

`POST /tasks/run` accepts the same Playwright spider payload. Tests inject a fake
render backend through `create_app(..., playwright_fetcher=...)`; production uses
the real optional Playwright backend.

## Boundaries

This feature does not add proxy pools, anti-bot behavior, scheduler workers,
Web UI completion, exporter completion, or database runtime support.
