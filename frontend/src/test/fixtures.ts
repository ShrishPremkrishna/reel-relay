import type { Candidate, Readiness, Setup, Workspace } from '../lib/models';

export const candidate: Candidate = {
  id: 'aabbccddeeff1122',
  channel: 'testcreator',
  status: 'ready',
  created_at: 1700000000,
  updated_at: 1700000050,
  score: 8.1,
  confidence: 0.9,
  caption: 'The moment. Source: testcreator',
  decision: {
    hook: 'The quiet pause before the reveal',
    reason: 'The setup has a clear payoff.',
    flags: [],
  },
  review: { approved: true, notes: 'The reaction lands after the pause.' },
  error: null,
  is_test: false,
  has_export: true,
  has_evidence: true,
  can_request_changes: true,
  export_version: 'export-1',
  origin: 'manual_import',
};
export const setup: Setup = {
  csrf: 'csrf-test',
  channels: [
    {
      name: 'testcreator',
      enabled: true,
      chat: 'anonymous-irc',
      attribution: 'Source: testcreator',
    },
  ],
  provider: 'ollama',
  model: 'local-vision-model',
  auth: {
    instagram_app_id: '12345',
    instagram_redirect_uri: 'https://relay.example/auth/instagram/callback',
    twitch_client_id: '',
  },
  instagram_app_secret_saved: true,
  accounts: { instagram: { connected: false }, twitch: { connected: false } },
  publishing: {
    enabled: false,
    dry_run: true,
    auto_approve: false,
    expected_username: '',
    max_per_day: 12,
    min_interval_seconds: 1800,
  },
  public_base_url: '',
  media_host_configured: false,
};
export const workspace: Workspace = {
  candidates: [candidate],
  counts: { ready: 1 },
  channels: [],
  settings: {
    publishing: false,
    automatic: false,
    daily_limit: 12,
    dry_run: true,
    provider: 'ollama',
    csrf: 'csrf-test',
  },
  accounts: { instagram: false, twitch: false },
};
export const readiness: Readiness = {
  checks: [
    { id: 'source', label: 'Source capture', state: 'ready', detail: 'A source is configured.' },
    {
      id: 'instagram',
      label: 'Instagram connection',
      state: 'missing',
      detail: 'Connect your account.',
      action: { label: 'Connect Instagram', href: '/setup#instagram' },
    },
  ],
  can_capture: true,
  can_edit: true,
  can_publish: false,
  auto_publish_enabled: false,
  media_engine: 'ffmpeg',
  perception: { provider: 'none', modalities: ['frames'], configured: false },
};
export function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
