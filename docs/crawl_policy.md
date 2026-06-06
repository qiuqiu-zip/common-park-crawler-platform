# Crawl Policy

`crawl_policy` is an optional safety preflight package. Existing spiders that do
not define it keep their current behavior. When the section exists, `enabled`
defaults to `true`.

## Defaults

```json
{
  "crawl_policy": {
    "enabled": true,
    "robots": {
      "enabled": true,
      "mode": "warn",
      "unavailable_policy": "warn"
    },
    "allow_cross_domain": false,
    "normalize_url": true,
    "remove_fragment": true,
    "remove_tracking_params": true,
    "tracking_params": [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
      "fbclid",
      "gclid",
      "msclkid"
    ],
    "max_requests": 100,
    "max_depth": 3,
    "max_duration_seconds": 300
  }
}
```

URL normalization removes fragments and common tracking parameters while
preserving business query parameters such as `page`, `category`, `cursor`, and
`id`.

## Scope Checks

The default allowed domain scope is inferred from `start_urls`. Set
`allowed_domains` to add explicit domains, `denied_domains` to block domains,
or `allow_cross_domain: true` only when a spider is expected to leave the start
domain. `include_url_patterns` and `exclude_url_patterns` are regular
expressions checked against normalized URLs.

## Robots

Robots checks are local-only in this release. The engine does not fetch
`robots.txt` from the network. Tests and local previews may provide inline
rules through `crawl_policy.robots.rules`.

`robots.mode: "warn"` records a warning and continues on explicit disallow.
`robots.mode: "block"` skips the disallowed URL. If rules are unavailable,
`robots.unavailable_policy` controls whether that becomes a warning, a block,
or is ignored.

Local fixture paths, `file:` URLs, and `.test` hosts skip network robots
lookup.

## Plan

`plan` previews scope and policy decisions without fetching pages:

```powershell
python -m crawler_platform.cli plan examples/crawl_policy_local.json
python -m crawler_platform.cli plan examples/crawl_policy_local.json --json
python -m crawler_platform.cli run examples/crawl_policy_local.json --dry-run --json
```

Plan output includes normalized start URLs, allowed domains, estimated request
limit, pagination and detail summaries, crawl policy counters, robots checks,
blocked URLs, warnings, field summary, and `safe_to_run`.

## Run Report

Formal task reports include `request_governance.crawl_policy` and a top-level
`crawl_policy` summary with:

- `policy_checked_urls`
- `policy_blocked_urls`
- `policy_warnings`
- `normalized_urls`
