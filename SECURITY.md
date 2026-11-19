# Security

## Reporting a vulnerability

Use the repository's private vulnerability reporting channel when it is available. If it is not available, open a minimal issue asking for a private contact path; do not include exploit details, credentials, authorization codes, signed media URLs or private media in that issue. There is no published response-time or supported-release guarantee.

Include the affected version, deployment mode, impact and a minimal reproduction using synthetic data. Keep sensitive evidence private until a suitable reporting channel is established. Ordinary defects without sensitive impact can use public issues.

## Trust model

Reel Relay is for a trusted operator or trusted administrative team. It does not isolate mutually hostile users, plugins or model adapters. An administrator with access to the process, database and encryption key can access stored credentials.

- The local server binds to loopback by default. Do not treat an unauthenticated local endpoint as a multi-user security boundary.
- Remote access requires HTTPS and a dedicated server token. Browser mutations use same-origin and CSRF checks; remote API clients can use explicit bearer authentication.
- OAuth app secrets and account tokens are encrypted in the local vault. Protect the encryption key and backups. Encryption does not protect a running installation from its own administrator.
- Captured media, transcripts, captions, chat, asset metadata and model responses are untrusted content. Embedded instructions must not override application rules, select shell commands, access secrets or authorize publication.
- Media subprocesses run with reduced environments and network access denied. Missing media isolation fails closed. Custom commands remain trusted administrator-selected programs, not an untrusted plugin sandbox.
- Cloud editorial/perception providers can receive the evidence described by the selected provider. Explicit media-upload permission is separate from storing a cloud API key.
- Signed delivery links temporarily expose one eligible export. Treat them as private bearer links until their expiry; keep query strings out of access logs and support tickets.

## Private installation data

Do not commit or distribute `config.toml`, runtime databases, encryption keys, tokens, signed links, recordings, transcripts, exports, model weights, downloaded assets or diagnostic receipts. Use only the release's source allowlist when packaging. `.gitignore` alone is not a release privacy check.

Use dedicated platform and storage credentials with the access needed for this installation. Ambient personal AWS profiles are not an S3 credential source. Rotate credentials at their issuing service if exposed, replace the server token when necessary, and review existing sessions and connected accounts.

The publisher records intent before the final platform call. An uncertain result remains held for reconciliation; resetting it to a sendable state can create duplicates. Pausing cannot retract an external request already sent.

## Verification limits

A real-account Instagram authorization, refresh and publishing acceptance trial remains outstanding. Linux media isolation is implemented, but end-to-end media verification on Linux remains outstanding. Follow [Testing](docs/TESTING.md) on the actual host; do not remove isolation to make a check pass. No guarantee of uninterrupted capture, accurate model judgments or safe use of arbitrary third-party adapters is implied.
