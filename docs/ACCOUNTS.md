# Accounts and publication setup

Passwords are entered on the platform, not in model prompts or source files. Use accounts and media appropriate for the intended publishing workflow. Connecting an account and enabling publication are separate actions.

## Instagram

This implementation uses Instagram API with Instagram Login for a professional Creator or Business account. It requests `instagram_business_basic` and `instagram_business_content_publish`. External developer enrollment, app roles, review and platform eligibility are governed by Meta's current dashboard and documentation.

1. Prepare the intended professional account and a developer app with Instagram Login.
2. Provide a stable HTTPS origin for the Relay callback. Register exactly `https://relay.example.com/auth/instagram/callback`, replacing the example origin with the real one.
3. In **Setup & accounts → Connect Instagram**, enter the app ID, app secret and callback URL. Save, then start the connection and sign in on Instagram.
4. Verify the connected username and configure **Video delivery**. Instagram must be able to fetch the finished MP4; a localhost URL is insufficient.
5. Inspect a real export and its credits. Enable public posting for the exact intended username only when the installation is ready to make external posts.

App secrets and tokens are encrypted in the local vault and are not returned to the browser. The adapter handles token exchange and eligible refreshes; revocation or expiry can require reconnection. Keep the matching encryption key in private backups.

A real-account login, refresh and publication acceptance trial remains outstanding for the project. Automated tests simulate platform responses. The UI cannot complete external developer enrollment, create hosting or guarantee that an account/app has the required platform access.

See [Deployment](DEPLOYMENT.md) for signed HTTPS delivery and S3. Storage delivery does not replace the HTTPS callback needed for sign-in.

## Manual posting

A finished MP4, caption and credits can be downloaded for a separate workflow. After an upload has actually appeared on the intended account, the manual-post action records its link and the operator's confirmation. It does not upload, query or verify the post through Instagram.

Pause automatic publication before manually handling a queued clip. Do not create a second post while an existing attempt is ambiguous or in progress; follow [Operations](OPERATIONS.md).

## Twitch

The recorder supports public Twitch video. Public anonymous chat is experimental and may be refused. EventSub is the authenticated alternative.

For the implemented device flow, register a Twitch public client application, enter its client ID in setup and start the connection. Follow the displayed verification link/code and authorize `user:read:chat`. Select EventSub for channels that use it. No chat-writing scope is requested.

The CLI equivalent is `./relay auth twitch --client-id APP_ID`. A confidential client with an unsupported secret-based refresh flow is not interchangeable with this adapter's public-client setup.

## Model providers

Provider credentials and media-upload permission are separate. Installed Codex uses its existing sign-in; Ollama uses an installed local model. Gemini keys can be stored through setup or supplied through `GEMINI_API_KEY`. The key is not permission to send evidence until the corresponding cloud-upload option is enabled.

Review [Models and evidence](LOCAL_MODELS.md) to understand what each provider receives. Source footage, transcripts and model output cannot authorize account operations.

## Source and asset rights

Retain accurate source attribution and each asset's required credits. Capturing a public stream does not establish permission to redistribute it. Application licensing does not cover stream footage, games, music, effects or model weights. Choose content and providers whose terms fit the intended use.

## Platform references

- [Instagram Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login)
- [Instagram content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing/)
- [Twitch device authorization](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)
- [Twitch EventSub WebSockets](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)

Check platform documentation when changing the configured API version or access level. These links describe external prerequisites, not a completed acceptance trial for this application.
