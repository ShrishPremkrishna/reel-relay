# Reel Relay

Reel Relay is a self-hosted Twitch clipping app. It watches configured streams, uses chat activity to find candidate moments, makes vertical edits, and presents the source, edit, captions and checks in a local browser interface. An optional publisher sends reviewed exports through Instagram's official API.

The application includes a durable queue, an FFmpeg renderer, local transcription setup and encrypted credential storage. It does not require another editing repository. Choose a local model, an installed editorial tool, Gemini, or a trusted custom adapter. A paid model API is not required for a local setup.

**Release status:** the local media path has been exercised on macOS. Linux media isolation is implemented, but end-to-end media verification on Linux remains outstanding. A real-account Instagram login, token-refresh and publication trial also remains outstanding; automated publisher tests use simulated services. These limits are not production-readiness or entertainment-quality guarantees.

## Start locally

Install these prerequisites through your operating system's supported package manager:

- **Python 3.12**, the version used by the checked-in dependency locks.
- **FFmpeg and FFprobe**, including libass, libx264, drawtext and loudnorm support.
- **Media isolation:** `sandbox-exec` on macOS, or bubblewrap with working unprivileged user namespaces on Linux. Windows is not supported.

From a source checkout or unpacked source release:

```sh
./bootstrap.sh
./relay start --open
```

Bootstrap creates the local Python environment and initializes a private workspace. It installs the application's locked dependencies; it does not connect accounts, download transcription weights, create public hosting or enable posting. The compiled UI is included, so Node.js is needed only for frontend development.

The app opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). Keep the process running and the machine awake while capturing streams. Ctrl+C stops its supervised services. `./relay start` launches capture, editing, publishing and the web server together. `./relay serve` initially launches only the web interface; enabling a workflow service in the UI can start managed workers behind it.

## Configure the workspace in the UI

Open **Setup & accounts**. Start with one source and inspect a few results before increasing concurrency or posting frequency.

| Section | What to configure |
| --- | --- |
| Workspace services | Pause or resume capture and editing; check and install local transcription. A pause does not delete queued work. |
| Sources to watch | Twitch usernames, attribution, public chat or EventSub, capture context before/after a spike and advanced detection settings. |
| Your editing engine | Provider, model, editing goal, allowed/held content flags and model-call limits. Supply provider keys here when needed. |
| Editing style | Finished length, captions, layout, effects and style notes. Effects require real files in the local catalog. |
| Connect Instagram | App configuration, callback address and the intended Creator or Business account. Connecting does not enable posting. |
| Official Twitch chat | Connect the configured Twitch app for authenticated EventSub chat. |
| Video delivery | An existing HTTPS origin or S3-compatible destination that Instagram can retrieve. External hosting still has to be provisioned separately. |
| Publishing pace | Maximum posts per rolling day and minimum spacing. These are ceilings, not quotas. |

Local transcription installation is an explicit online setup step. It downloads the selected model and installs a separate, locked environment under the private data directory. Normal transcription and rendering use local files with network access denied.

**Steady volume** accepts clear, entertaining moments without an entertainment-score cutoff. **More selective** also applies the configured score threshold and can use the additional media-perception provider. Both keep source-integrity, caption, audio and export checks. Allowing a content flag changes editorial policy; it does not authorize a broken export or grant rights to the footage.

For local Ollama use, install Ollama and a suitable model separately, then select its exact installed identifier. Editorial frame review needs vision capability; optional generative listening needs native audio capability. A model accepting images does not establish that it can hear audio. Gemini requires a chosen model, a key and the applicable cloud-upload permission. See [Models and evidence](docs/LOCAL_MODELS.md).

## Review a clip

The **Clip queue** separates work in progress, held moments, finished edits and published clips. Source-only moments are labeled **SOURCE**; rendered versions are labeled **EDIT**.

- Play the edit and use **Compare with source** to inspect the original context.
- **Request changes** sends revision notes through the normal editing and quality checks while retaining prior exports.
- **Reassess moment** gives a held or rejected source another selection pass with optional context.
- Complete a manual preview check only after watching and listening to the exact export shown. A source comparison is not an export check.
- Download the MP4 and credits for a separate workflow. Recording an existing manual post records your confirmation; it does not upload or verify a post through Instagram.

Use **Import video** in the Clip queue for a local source, or **Add an effect** in the Effects library for a reusable asset. Uploads are limited to 100 MiB and retain the supplied credit or rights metadata. Adding media does not approve it for publication.

The **Sources** page reports recording and intake status. When the editing backlog is full, recording continues within the rolling buffer and new candidate intake resumes as capacity returns. Held and finished clips do not consume editing capacity. The **Effects library** previews the local catalog; **Activity** links operational events back to clips.

Chat spikes are leads, not proof that a moment is funny. Models can mishear dialogue, miss action between frames or infer a payoff that is not present. Review real results when tuning the app; neither an editorial score nor a successful render predicts reach.

## Accounts and public delivery

The app starts locally and public posting starts disabled. Configure a professional Instagram account, its developer app, an exact HTTPS callback and reachable media before enabling publication. See [Accounts](docs/ACCOUNTS.md) and [Deployment](docs/DEPLOYMENT.md).

Remote access requires a dedicated `REEL_RELAY_WEB_TOKEN` of at least 32 characters and HTTPS. S3 delivery uses dedicated credentials entered in setup or supplied as `REEL_RELAY_S3_ACCESS_KEY_ID` and `REEL_RELAY_S3_SECRET_ACCESS_KEY`; ambient personal AWS profiles are not used. The app does not create domains, tunnels, certificates, buckets or storage lifecycle policies.

Publication uncertainty is held for reconciliation rather than blindly retried. Pausing publishing cannot recall a request already sent to Instagram. The real-account publishing acceptance trial remains to be completed; verify your account, permissions, media delivery and playback before unattended operation.

## Data and licensing

`config.toml` and `runtime/` are private installation data, not release contents. Runtime includes the database, captured footage, transcripts, evidence, exports, downloaded models and encrypted credentials. Back up the database, media and matching encryption key together. Treat logs, signed delivery URLs and diagnostic snapshots as private until reviewed.

The source code is [MIT licensed](LICENSE). Model weights, Twitch footage, game/music content and third-party effects have their own terms and rights. The release includes no footage, account credentials, model weights or effects pack. The builtin renderer works with an empty asset catalog. See [Media engine](docs/MEDIA_ENGINE.md) for catalog interchange and [Security](SECURITY.md) for trust boundaries.

## Documentation and development

- [Operations and recovery](docs/OPERATIONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [HTTP API and machine-readable schemas](docs/API.md)
- [Testing and verification limits](docs/TESTING.md)
- [Editorial behavior](docs/EDITORIAL_RESEARCH.md)
- [Choosing Twitch sources](docs/WATCHLIST.md)
- [Contributing](CONTRIBUTING.md)

SQLite is the default for one machine. PostgreSQL supports concurrent claims, but multiple hosts still need shared artifacts, compatible configuration and a shared credential key. The included container is a control-plane image; it is not an all-in-one media-processing deployment.
