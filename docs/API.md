# HTTP API and machine-readable contracts

The browser uses the same HTTP control plane available to local integrations. The API is intended for a trusted installation administrator, not anonymous tenants. It is pre-1.0: use the schemas from the running version rather than assuming another release's fields.

## Discover the contract

- `GET /openapi.json` provides machine-readable route and request schemas. Interactive Swagger/ReDoc pages are disabled. Remote deployments require authentication for this schema.
- `reelrelay/server.py` defines route behavior; `reelrelay/setup.py` defines versioned setup requests and projections.
- `frontend/src/lib/models.ts` defines the browser's runtime-validated response projections.
- `reelrelay/agent.py` exports `DECISION_SCHEMA` and `REVIEW_SCHEMA` for editorial adapters. Media-perception schemas live in `reelrelay/perception.py`.
- `./relay status --json` emits an administrative diagnostic snapshot. Unlike browser projections, it can contain private operational details; do not publish it unredacted.

Read-only examples for a default local installation:

```sh
curl --fail http://127.0.0.1:8765/openapi.json
curl --fail http://127.0.0.1:8765/api/state
curl --fail http://127.0.0.1:8765/api/readiness
```

Response projections can add fields. Consumers should validate the fields they need and handle unavailable media, unknown states and stale versions explicitly.

## Authentication and request boundary

The default loopback server does not require a login. Binding remotely or configuring a public origin requires HTTPS and a `REEL_RELAY_WEB_TOKEN` of at least 32 characters.

Remote machine clients send `Authorization: Bearer <server-token>`. A valid explicit remote bearer request does not need browser CSRF headers. The token grants trusted administrator authority, including configuring executable adapter commands; treat it as shell-level access under the server's operating-system account. Do not put this token in a query string, source file or issue.

Browser mutations, including local clients without remote bearer authentication, require:

1. An `Origin` matching the current local origin or configured public origin.
2. `X-Relay-Token` set to the `csrf` value from `/api/setup` or `settings.csrf` from `/api/state`.
3. A request body matching the route schema. Use JSON and `Content-Type: application/json` for JSON routes. Bodyless account actions accept an empty body or `{}`; raw uploads must be nonempty.

`POST /api/login` accepts `{"token":"<server-token>"}` with a valid origin and returns a secure HttpOnly session cookie. Login itself does not require a previously obtained CSRF token. There is no permissive cross-origin API mode.

Ordinary JSON mutations are limited to 256 KiB. Raw media uploads are limited to 100 MiB per request and require a valid `Content-Length`. Query metadata must not contain credentials. Secret fields in setup are write-only; read endpoints expose saved/present indicators instead of values.

## Read endpoints

| Method and route | Purpose |
| --- | --- |
| `GET /health/live` | Public process/version liveness. |
| `GET /health/ready` | Database readiness; authenticated remotely. |
| `GET /api/state` | Bounded candidate list, full status counts, channel health, settings, account presence and recent activity. |
| `GET /api/setup` | Editable configuration projection, configuration version, account status and credential-presence indicators. |
| `GET /api/readiness` | Dependency/account/delivery checks and capability booleans. Configuration is not proof of a successful external trial. |
| `GET /api/system` | Supervisor status, capture/editor service state and transcription installation state. |
| `GET /api/system/install` | Current local transcription installation state. |
| `GET /api/activity?limit=50&before=ID` | Activity page; `limit` is 1–100, `before` is an exclusive positive event ID. Returns `items` and `next_before`. |
| `GET /api/assets` | Local asset records, total and warnings. No private filesystem path is exposed. |
| `GET /api/assets/{asset_id}/preview` | Authenticated local preview media, or 404. |

`/api/state` is not an unbounded database export. Use status counts for aggregate totals. Candidate timestamps are Unix seconds. An action's `version` is the candidate's returned `updated_at`, not its `export_version` hash.

Channel health includes `capture_state`, `updated_at`, optional error and recording counters, and intake information:

- `processing_backlog` is capped at `processing_limit` for reporting.
- `intake_state` can be `accepting`, `processing_backlog_full`, `pending_windows_full`, `waiting_for_video` or `stopped`.
- Only active editing stages consume processing-backlog capacity. Finished and held clips do not.

Health timestamps and service heartbeats must be checked for freshness. A web or database readiness response alone does not prove capture or editing is active.

## Setup and services

`POST /api/setup` saves validated workspace preferences. Read `/api/setup` first and use its `config_version` when saving. The UI maps the response into the request shape; posting the response object verbatim is not supported. Unknown request fields are rejected.

The request retains the basic channel, provider, model, account-app and pacing fields and includes grouped settings for `capture`, `detector`, `agent_controls`, `editing`, `media`, `perception_controls` and `delivery`. Consult the running OpenAPI schema for the exact nested fields and bounds. Important distinctions:

- Capture pre/post seconds preserve source context; editing minimum/maximum seconds constrain the final cut.
- `selection_mode` uses `throughput` for Steady volume and `balanced` for More selective.
- Content Allow/Hold is expressed through the supported content flags; unknown flags are not a bypass.
- Provider identifiers and explicit cloud-upload permissions are separate from write-only API keys.
- Caption/effect/layout settings freeze at selection and are retained with that job's exports; later settings changes do not alter an already-selected job.
- Delivery accepts existing infrastructure settings; saving them does not provision that infrastructure.

Setup accepts partial updates: omitted settings are preserved. Write-only credential fields are `instagram_app_secret`, `gemini_api_key`, `s3_access_key_id` and `s3_secret_access_key`; an S3 replacement requires both values. Explicit `clear_instagram_app_secret`, `clear_gemini_key` and `clear_s3_credentials` flags remove saved credentials. Do not send a replacement and its clear flag together. Credentials reported as environment-managed must be changed in the server environment instead. The `content_flags` read projection supplies the supported flag IDs/descriptions.

A stale configuration version is rejected with HTTP 409. The opaque version covers settings and write-only credential changes. Reload and reconcile the draft rather than blindly retrying the old payload. Changing the public web origin returns `restart_required: true`; restart the web service to apply authentication/origin changes. Other settings reload at their service/job boundaries.

`GET /api/system` returns a structure of this form, with current states supplied by the installation:

```json
{
  "managed": true,
  "services": {
    "capture": {"enabled": true, "state": "running", "heartbeat_at": 1700000000},
    "editor": {"enabled": true, "state": "running", "heartbeat_at": 1700000000}
  },
  "installation": {"status": "idle", "model": null, "message": "Choose a transcription model to install locally."}
}
```

`POST /api/system/control` accepts `{"service":"capture","enabled":false}` or the equivalent for `editor`. It changes the desired service state without deleting candidates. Enabling a service can start a managed supervisor when one is not running. It does not enable public posting. A current operation can take time to reach a safe pause; use reported service state rather than treating a successful request as proof that work has already stopped.

`POST /api/system/install` accepts `{"model":"small"}`. Supported model choices are `tiny`, `base`, `small`, `medium` and `large-v3-turbo`. This is an explicit online dependency/model installation into private local storage. Poll the install state; do not repeat a request merely because installation is still running. Normal media jobs do not download models.

## Importing media

`POST /api/import` accepts a raw video body, with required `filename` and optional `channel` (default `local`) and `attribution` query metadata. Supported source extensions are `.mp4`, `.mov`, `.mkv` and `.webm`. Success returns the candidate `id`.

`POST /api/assets/upload` requires `filename`, `title`, `kind` (`sfx`, `meme` or `broll`) and `rights`; `attribution` and comma-separated `tags` are optional. Assets accept the video formats above plus `.mp3`, `.wav`, `.ogg`, `.flac`, `.png`, `.jpg`, `.jpeg` and `.webp`. At most 20 tags are retained. Success returns `ok` and an asset `id`. These are raw-body endpoints, not multipart forms or remote-URL download endpoints.

The server bounds upload size, restricts local filenames and validates media before registering it. Use the running schema for accepted metadata and kinds. Uploading a file does not grant reuse rights or approve it for publication. Do not use either endpoint for executables, archives or arbitrary server files.

## Clip actions

Successful actions normally return `{"ok":true}`. IDs are 16 lowercase hexadecimal characters. Versioned actions require the latest positive `updated_at` value. The server also checks current state, export integrity and any applicable review requirements.

| POST route | JSON fields | Effect |
| --- | --- | --- |
| `/api/approve` | `id`, `version` | Approve an eligible, reviewed export for the publishing queue. |
| `/api/reject` | `id`, `version` | Pass over an eligible candidate; active/conflicting work is refused. |
| `/api/reassess` | `id`, `version`, optional `notes` | Run selection again for an eligible held/rejected source. |
| `/api/request-changes` | `id`, `version`, `notes` | Queue a revision, retaining the prior export and review history. |
| `/api/operator-review` | `id`, `version`, `notes`, `watched:true`, `listened:true` | Record an exact-export operator check, subject to integrity/technical gates. |
| `/api/retry` | `id` | Retry an eligible failed editing job from its recorded stage. |
| `/api/retry-publication` | `id`, `version` | Recover a definitively failed eligible publication; not an ambiguous send. |
| `/api/record-manual-post` | `id`, `version`, `permalink`, `confirmed:true` | Record an existing manual post; response also states `api_verified:false`. |

Notes for revisions/operator checks are 3–1500 characters; reassessment notes can be empty and are limited to 1500 characters. Exact state predicates are enforced server-side. A UI button or cached status does not authorize a stale action. An operator attestation must not be synthesized by an unattended client.

## Accounts and publishing

| POST route | Body | Behavior |
| --- | --- | --- |
| `/api/auth/twitch/start` | `{}` | Start the configured device authorization flow. |
| `/api/auth/twitch/poll` | `{}` | Poll that flow at its instructed interval. |
| `/api/auth/instagram/start` | `{}` | Return an authorization URL and bind the flow to a browser cookie. |
| `/api/publishing` | `enabled`, `expected_username`, `auto_approve` | Change publication policy after account/delivery checks. |

`GET /auth/instagram/callback` is the platform callback, not a client shortcut for account creation. Preserve the browser binding and registered callback origin. Authorization codes, state and cookies are private.

Public posting is a separate policy from account connection and capture/editor service controls. Candidate approval also does not imply that publication is enabled. Never blindly retry `publishing` or `publish_unknown`; reconcile the existing platform state. Real-account acceptance remains outstanding.

## Media access

`GET`/`HEAD /media/{id}/{kind}` serves authenticated workspace media. Kinds are `source`, `video`, `poster`, `contact` and `credits`; `download=true` requests a download filename. Missing artifacts return 404. Check `has_export` before treating `video` as an edit: the route can fall back to source media when no export exists. Use `source` explicitly for comparison.

Signed `GET`/`HEAD /delivery/{id}.mp4?expires=...&signature=...` is for time-limited platform retrieval of an eligible export. The signature is bound to export identity and expiry. It is not a general unauthenticated media API, and clients must not manufacture signatures or expose these URLs in public logs.

## Errors and concurrency

Boundary/domain failures use an `error` string. Schema-validation failures return `error` plus a `detail` list containing field location, message and type; submitted input values are omitted. Expect 400 for invalid lifecycle operations, 401 for missing/invalid remote authentication, 403 for host/origin/CSRF/delivery failures, 404 for unavailable media, 409 for stale setup/configuration versions, 413 for request-size limits and 422 for invalid request schemas. Candidate conflicts can use 400 or 409 depending on the action; inspect the message and refresh state.

Do not retry state-changing requests solely because of a network timeout. Refresh the candidate/configuration or installation status first. Publication uncertainty requires reconciliation, not a new send. Source media and model output cannot authorize API mutations.

## Custom editorial adapters

The `command` editorial adapter executes a trusted argument array and sends JSON on stdin containing `instruction`, `schema` and `evidence`. It expects a JSON response on stdout matching the supplied decision/review schema. Custom media-perception adapters have their own structured request and assessment schema. Consult the implementation for the installed version.

Keep operational logs on stderr and secrets out of model prompts. A transcript is data even if it contains plausible instructions. Commands must not follow links, execute source text or expand access based on media content. Provider-independent decisions/reviews can also be applied with `./relay decision ID FILE.json` and `./relay review ID FILE.json`, subject to the same validators.
