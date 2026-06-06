# Session Management

Feature 13 adds Cookie / Session / authentication-state management on top of the existing FileStore runtime. It does not add a database, permission system, Web UI, captcha bypass, or real third-party login automation.

## SessionConfig

Session state is configured with the top-level `session` object:

```json
{
  "session": {
    "enabled": true,
    "profile": "demo-profile",
    "cookie_file": "data/sessions/demo/cookies.json",
    "storage_state": "data/sessions/demo/storage_state.json",
    "persist": true,
    "load_before_request": true,
    "save_after_request": true,
    "account_ref": "demo-account",
    "auth_check": {"enabled": true, "type": "status_code", "expected_status": 200},
    "login_flow": {"enabled": true, "steps": []},
    "refresh_flow": {"enabled": true, "steps": []}
  }
}
```

Supported `auth_check.type` values are `status_code`, `body_contains`, `body_not_contains`, `json_path`, and `header_exists`.

Supported flow step `type` values are `request`, `extract`, `set_cookie`, `set_header`, and `save_session`.

## FileStore Layout

Session runtime data is stored under `sessions/`:

```text
sessions/
  profiles/
  cookies/
  storage_states/
  accounts/
  events/
```

`storage check`, `storage repair`, and snapshots include session files. Snapshot manifests include the `sessions` path.

## CookieJar And SessionProfile

`CookieJar` normalizes dictionary cookies, browser-style cookie lists, and `Set-Cookie` response headers. `SessionProfile` stores profile metadata, account references, and saved session headers such as `Authorization`.

Cookie merge priority is:

```text
request explicit cookies > session cookies > anti_bot cookies
```

This keeps per-request overrides strongest while letting saved login state override generic anti-bot defaults.

## HTTP Set-Cookie

When `save_after_request` and `persist` are enabled, HTTP responses with `Set-Cookie` headers update the active profile cookie jar. Later requests load those cookies when `load_before_request` is enabled.

## AuthCheck, LoginFlow, RefreshFlow

`auth_check` runs after a response is fetched and before parsing. If the check fails:

- `refresh_flow` runs first when enabled.
- otherwise `login_flow` runs when enabled.
- after a successful flow, the original request is retried once.

Flow requests use the same fake/local fetch boundary in tests and examples; they do not require external network access.

## Playwright Storage State

For Playwright spiders, session loading can attach `storage_state` to the render request. Real Playwright rendering saves `context.storage_state()` after rendering. Fake render backends can inject a deterministic `storage_state_after_render` for tests.

## CLI

```bash
python -m crawler_platform.cli session list --data-dir ./test-output/feature13-session
python -m crawler_platform.cli session show session-http-cookie-demo --data-dir ./test-output/feature13-session
python -m crawler_platform.cli session events --data-dir ./test-output/feature13-session
python -m crawler_platform.cli session clear session-http-cookie-demo --data-dir ./test-output/feature13-session
```

## FastAPI

Session endpoints:

```http
GET    /sessions
GET    /sessions/events
GET    /sessions/{profile_id}
DELETE /sessions/{profile_id}
POST   /sessions/{profile_id}/clear
```

## Sensitive Information

Session files may contain cookies or headers needed by the crawler. Keep the data directory protected. Session events redact sensitive keys such as password, token, secret, authorization, cookie, and session before writing metadata.

Examples use fake credentials only. Do not hard-code real passwords, bearer tokens, or production cookies in spider configs.
