# Reel Relay interface

React, TypeScript and Vite build the browser UI. The Python server serves its compiled assets, so application users do not need Node.js. Frontend development requires Node.js 22.12 or later.

```sh
npm ci
npm run check
npm run format:check
```

`npm run build` writes `../reelrelay/web/index.html`, `setup.html` and hashed assets. Commit generated output with UI source changes. Static assets are public so the sign-in screen can load; application data and mutations remain behind the backend boundary.

`npm run dev` provides local hot reload and proxies API/media requests to `127.0.0.1:8765`. Use the Python-served production build for origin, CSRF and CSP acceptance. Work against an isolated test installation, not an active capture or publishing queue.

The UI uses runtime-validated API schemas, domain helpers, route-level pages, reusable media/forms and a polling hook. Settings preserve unsaved changes during polling and use configuration versions to reject stale writes. Clip mutations use candidate versions where required. API changes must update backend validation, runtime schemas, tests and [API documentation](../docs/API.md) together.

Navigation uses server routes `/`, `/setup` and `/?view=...`. Responsive clip selection should focus and reveal the selected preview. Check keyboard access and narrow screens as well as desktop layout.

Component tests mock the API boundary and do not publish media. Credentials are submitted to the local server and cleared from forms; do not add browser-storage tokens, analytics, remote fonts or third-party scripts without a deliberate architectural review.
