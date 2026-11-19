import { z } from 'zod';
import {
  captureSchema,
  detectorSchema,
  agentControlsSchema,
  editingSchema,
  mediaSchema,
  perceptionControlsSchema,
  deliverySchema,
  contentFlagSchema,
  type CaptureSettings,
  type DetectorSettings,
  type AgentControls,
  type EditingSettings,
  type MediaSettings,
  type PerceptionControls,
  type DeliverySettings,
} from './settingsSchema';

export const candidateSchema = z.object({
  id: z.string(),
  channel: z.string(),
  status: z.string(),
  created_at: z.number(),
  updated_at: z.number(),
  score: z.number().nullable(),
  confidence: z.number().nullable().optional(),
  caption: z.string().nullable().optional(),
  decision: z
    .object({
      hook: z.string().optional(),
      reason: z.string().optional(),
      flags: z.array(z.string()).optional(),
    })
    .passthrough()
    .nullable()
    .optional(),
  review: z
    .object({
      approved: z.boolean().optional(),
      confidence: z.number().optional(),
      issues: z.array(z.string()).optional(),
      notes: z.string().optional(),
      audio_review: z.string().optional(),
    })
    .passthrough()
    .nullable()
    .optional(),
  error: z.string().nullable().optional(),
  is_test: z.union([z.boolean(), z.number()]).transform(Boolean),
  has_export: z.boolean(),
  has_evidence: z.boolean(),
  can_request_changes: z.boolean().optional(),
  posted_manually: z.boolean().optional(),
  permalink: z.string().nullable().optional(),
  start: z.number().optional(),
  end: z.number().optional(),
  export_version: z.string().nullable().optional(),
  origin: z.string().optional(),
});
export type Candidate = z.infer<typeof candidateSchema>;

export const healthSchema = z.object({
  channel: z.string(),
  updated_at: z.number(),
  capture_state: z.string().optional(),
  chat_state: z.string().optional(),
  recorded_seconds: z.number().optional(),
  chat_messages: z.number().optional(),
  pending_windows: z.number().optional(),
  processing_backlog: z.number().int().nonnegative().optional(),
  processing_limit: z.number().int().nonnegative().optional(),
  intake_state: z
    .enum([
      'accepting',
      'processing_backlog_full',
      'pending_windows_full',
      'waiting_for_video',
      'stopped',
    ])
    .optional(),
  capture_error: z.string().nullable().optional(),
  chat_error: z.string().nullable().optional(),
});
export type ChannelHealth = z.infer<typeof healthSchema>;
export const workspaceSchema = z.object({
  candidates: z.array(candidateSchema),
  counts: z.record(z.string(), z.number()),
  channels: z.array(healthSchema),
  settings: z.object({
    publishing: z.boolean(),
    automatic: z.boolean(),
    daily_limit: z.number(),
    dry_run: z.boolean(),
    provider: z.string(),
    csrf: z.string(),
  }),
  accounts: z.object({ instagram: z.boolean(), twitch: z.boolean() }),
});
export type Workspace = z.infer<typeof workspaceSchema>;

export const channelSchema = z.object({
  name: z.string(),
  enabled: z.boolean(),
  chat: z.enum(['anonymous-irc', 'eventsub']),
  attribution: z.string(),
});
export type Channel = z.infer<typeof channelSchema>;
const accountSchema = z.object({
  connected: z.boolean(),
  username: z.string().nullable().optional(),
  expired: z.boolean().optional(),
  expires_at: z.number().nullable().optional(),
});
export const setupSchema = z.object({
  csrf: z.string(),
  channels: z.array(channelSchema),
  provider: z.enum(['manual', 'command', 'ollama', 'codex', 'gemini']),
  model: z.string(),
  selection_mode: z.enum(['balanced', 'throughput']).optional(),
  auth: z.object({
    instagram_app_id: z.string(),
    instagram_redirect_uri: z.string(),
    twitch_client_id: z.string(),
  }),
  instagram_app_secret_saved: z.boolean(),
  accounts: z.object({ instagram: accountSchema, twitch: accountSchema }),
  publishing: z.object({
    enabled: z.boolean(),
    dry_run: z.boolean(),
    auto_approve: z.boolean(),
    expected_username: z.string(),
    max_per_day: z.number(),
    min_interval_seconds: z.number(),
  }),
  public_base_url: z.string(),
  media_host_configured: z.boolean(),
  config_version: z.string().nullable().optional(),
  credential_sources: z
    .object({
      gemini: z.enum(['environment', 'saved', 'none']),
      s3: z.enum(['environment', 'saved', 'none']),
      instagram_app_secret: z.enum(['saved', 'none']),
    })
    .optional(),
  capture: captureSchema.optional(),
  detector: detectorSchema.optional(),
  agent_controls: agentControlsSchema.optional(),
  editing: editingSchema.optional(),
  media: mediaSchema.optional(),
  perception_controls: perceptionControlsSchema.optional(),
  delivery: deliverySchema.optional(),
  content_flags: z.array(contentFlagSchema).optional(),
  s3_credentials_saved: z.boolean().optional(),
  gemini_api_key_saved: z.boolean().optional(),

  perception: z
    .object({
      provider: z.enum(['none', 'ollama', 'gemini', 'command']),
      model: z.string(),
      allow_cloud_upload: z.boolean(),
      api_key_saved: z.boolean(),
    })
    .optional(),
});
export type Setup = z.infer<typeof setupSchema>;
export interface SetupDraft {
  channels: Channel[];
  provider: Setup['provider'];
  model: string;
  selection_mode?: 'balanced' | 'throughput';
  instagram_app_id: string;
  instagram_app_secret: string | null;
  instagram_redirect_uri: string;
  twitch_client_id: string;
  max_per_day: number;
  min_interval_seconds: number;
  perception_provider?: 'none' | 'ollama' | 'gemini' | 'command';
  perception_model?: string;
  allow_cloud_upload?: boolean;
  gemini_api_key?: string | null;
  config_version?: string;
  capture?: CaptureSettings;
  detector?: DetectorSettings;
  agent_controls?: AgentControls;
  editing?: EditingSettings;
  media?: MediaSettings;
  perception_controls?: PerceptionControls;
  delivery?: DeliverySettings;
  s3_access_key_id?: string | null;
  s3_secret_access_key?: string | null;
  clear_gemini_key?: boolean;
  clear_s3_credentials?: boolean;
  clear_instagram_app_secret?: boolean;
}
export function setupDraft(s: Setup): SetupDraft {
  return {
    channels: s.channels.map((c) => ({ ...c })),
    provider: s.provider,
    model: s.model,
    ...(s.selection_mode ? { selection_mode: s.selection_mode } : {}),
    ...s.auth,
    instagram_app_secret: null,
    max_per_day: s.publishing.max_per_day,
    min_interval_seconds: s.publishing.min_interval_seconds,
    ...(s.config_version
      ? {
          config_version: s.config_version,
          clear_gemini_key: false,
          clear_s3_credentials: false,
          clear_instagram_app_secret: false,
          s3_access_key_id: null,
          s3_secret_access_key: null,
        }
      : {}),
    capture: s.capture ? structuredClone(s.capture) : undefined,
    detector: s.detector ? structuredClone(s.detector) : undefined,
    agent_controls: s.agent_controls ? structuredClone(s.agent_controls) : undefined,
    editing: s.editing ? structuredClone(s.editing) : undefined,
    media: s.media ? structuredClone(s.media) : undefined,
    perception_controls: s.perception_controls ? structuredClone(s.perception_controls) : undefined,
    delivery: s.delivery ? structuredClone(s.delivery) : undefined,

    ...(s.perception
      ? {
          perception_provider: s.perception.provider,
          perception_model: s.perception.model,
          allow_cloud_upload: s.perception.allow_cloud_upload,
          gemini_api_key: null,
        }
      : {}),
  };
}

export const readinessSchema = z.object({
  checks: z.array(
    z.object({
      id: z.string(),
      label: z.string(),
      state: z.enum(['ready', 'missing', 'warning']),
      detail: z.string(),
      action: z.object({ label: z.string(), href: z.string() }).optional(),
    }),
  ),
  can_capture: z.boolean(),
  can_edit: z.boolean(),
  can_publish: z.boolean(),
  auto_publish_enabled: z.boolean(),
  media_engine: z.string(),
  perception: z.object({
    provider: z.string(),
    modalities: z.array(z.string()),
    configured: z.boolean(),
  }),
});
export type Readiness = z.infer<typeof readinessSchema>;
export const assetsSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      kind: z.string(),
      tags: z.array(z.string()),
      duration: z.number().nullable(),
      rights: z.string(),
      attribution: z.string(),
      preview_url: z.string().nullable(),
      media_type: z.string().optional(),
    }),
  ),
  total: z.number(),
  warnings: z.array(z.string()),
});
export type Asset = z.infer<typeof assetsSchema>['items'][number];
export const activitySchema = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      at: z.number(),
      kind: z.string(),
      candidate_id: z.string().nullable(),
      summary: z.string(),
      level: z.enum(['info', 'warning', 'error']),
    }),
  ),
  next_before: z.number().nullable(),
});
export type Activity = z.infer<typeof activitySchema>;
