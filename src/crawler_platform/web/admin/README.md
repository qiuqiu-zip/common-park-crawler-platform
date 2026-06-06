# Web Admin Static Assets

Feature 17 keeps the Web management console as package-local static files:

- `index.html` is served by `GET /admin` and `GET /admin/`.
- `assets/app.js` coordinates views and actions.
- `assets/api.js` unwraps the `{ok,data,error,meta}` API envelope and redacts sensitive fields.
- `assets/components.js` contains table, pager, JSON, and status helpers.
- `assets/styles.css` contains the local UI styles.

The console has no build step, CDN, database dependency, login flow, or external
network requirement. API calls use root-relative paths such as `/spiders` and
`/tasks`.
