# Request Governance

Feature 09 adds a shared request governance layer for HTTP, API, pagination, detail following, and Playwright-rendered requests. Runtime storage remains FileStore only; no database or ORM dependency is required.

## Pipeline

Requests flow through one `RequestPipeline`:

1. Build the `HttpRequest`.
2. Apply anti-bot headers, cookies, user agent, referer, and optional delay.
3. Select a proxy through `ProxyManager`.
4. Acquire concurrency and rate-limit slots.
5. Fetch with HTTP or Playwright.
6. Parse the response when the engine requested parsing.
7. Decide whether to retry based on status or error type.
8. Feed proxy success or failure back into the proxy state.
9. Record counters, attempts, warnings, and task governance summary.

The engine uses the same pipeline for list pages, pagination pages, detail pages, and Playwright requests.

Feature 13 adds session handling to this same pipeline. It can load saved
cookies or Playwright storage state before a request, persist `Set-Cookie` or
rendered storage state after a response, and run `login_flow` or `refresh_flow`
when `auth_check` fails.

## RetryConfig

`retry.max_attempts` includes the first request. `enabled=false` means only one attempt is made.

```json
{
  "retry": {
    "enabled": true,
    "max_attempts": 3,
    "backoff": "exponential",
    "backoff_base_seconds": 0.2,
    "backoff_max_seconds": 2,
    "retry_on_status": [500, 502, 503, 504],
    "retry_on_errors": ["network", "timeout", "http_status", "parse", "render"],
    "jitter": false
  }
}
```

Supported backoff modes are `none`, `fixed`, and `exponential`. Tests use fake sleep/clock paths for deterministic delay coverage.

## ProxyConfig

```json
{
  "proxy": {
    "enabled": true,
    "mode": "round_robin",
    "proxies": ["http://proxy-a.local:8080", "http://proxy-b.local:8080"],
    "fail_threshold": 2,
    "cooldown_seconds": 60,
    "sticky_key": "domain"
  }
}
```

Modes are `round_robin`, `random`, and `sticky`. `proxy_file` can load one proxy per line. Failed proxies enter cooldown after `fail_threshold`; cooled proxies are skipped until the cooldown expires. The chosen proxy is attached to `HttpRequest.proxy`, which is visible to HTTP fetchers and Playwright render backends.

## AntiBotConfig

```json
{
  "anti_bot": {
    "enabled": true,
    "user_agents": ["CrawlerPlatformTest/1.0"],
    "headers_pool": [{"X-Test-Pool": "feature09"}],
    "random_delay": true,
    "min_delay_seconds": 0.1,
    "max_delay_seconds": 0.3,
    "cookies": {"session": "local"},
    "cookie_file": "examples/fixtures/cookies.json",
    "referer_policy": "previous_url",
    "respect_robots_txt": false
  }
}
```

`referer_policy` supports `none`, `previous_url`, and `origin`. `respect_robots_txt` is documented as a boundary in this feature; full robots parsing is not implemented in Feature 09.

When `session.enabled` is true, cookie merge priority is:

```text
request explicit cookies > session cookies > anti_bot cookies
```

## Rate Limit And Concurrency

```json
{
  "rate_limit": {
    "enabled": true,
    "requests_per_second": 2,
    "per_domain": true
  },
  "concurrency": {
    "enabled": true,
    "max_concurrent_requests": 4,
    "per_domain": true
  }
}
```

Rate limiting supports global and per-domain keys. Concurrency uses semaphores and records the peak concurrent request count.

## Errors And Warnings

Structured request errors include:

- `network`
- `timeout`
- `http_status`
- `parse`
- `render`
- `proxy`
- `rate_limit`
- `retry_exhausted`

Warnings and attempts preserve `error_type`, `message`, `url`, `status_code`, `attempt`, `proxy`, and `retryable` when available.

## Counters

Task records now include:

- `retry_attempts`
- `retry_successes`
- `retry_failures`
- `rate_limit_waits`
- `rate_limit_wait_seconds`
- `concurrent_requests_peak`
- `request_governance`

`request_governance` contains retry, rate-limit, concurrency, proxy, and recent attempt summaries. FastAPI task query responses return this summary through `TaskRecord.to_dict()`.

## CLI Examples

```bash
python -m crawler_platform.cli run examples/retry_http.json --data-dir ./test-output/feature09-retry
python -m crawler_platform.cli run examples/proxy_round_robin.json --data-dir ./test-output/feature09-proxy
python -m crawler_platform.cli run examples/antibot_headers.json --data-dir ./test-output/feature09-antibot
python -m crawler_platform.cli run examples/rate_limit_concurrency.json --data-dir ./test-output/feature09-rate
```

`examples/fixtures/retry_success_after_2.json` uses a local sequence fixture to simulate a transient network failure without external network access.

## FastAPI Example

`POST /tasks/run` accepts the same spider config payload. `GET /tasks/{task_id}` returns the task record with `request_governance`.

## Not Included In Feature 09

Feature 09 does not implement Scheduler, Worker queues, Web UI completion, Exporter completion, permission systems, database migrations, full robots parsing, real CAPTCHA solving, or anti-bot bypass logic.
