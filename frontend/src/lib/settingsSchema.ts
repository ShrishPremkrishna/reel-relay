import { z } from 'zod';

export const captureSchema = z.object({
  quality: z.string(),
  segment_seconds: z.number(),
  retention_seconds: z.number(),
  pre_seconds: z.number(),
  post_seconds: z.number(),
  max_pending: z.number(),
  chat_video_offset_seconds: z.number(),
  reconnect_seconds: z.number(),
  max_channels: z.number(),
  min_free_gb: z.number(),
});
export const detectorSchema = z.object({
  window_seconds: z.number(),
  baseline_windows: z.number(),
  warmup_windows: z.number(),
  ratio: z.number(),
  min_messages: z.number(),
  min_users: z.number(),
  per_user_cap: z.number(),
  cooldown_seconds: z.number(),
  ignored_users: z.array(z.string()),
});
export const agentControlsSchema = z.object({
  command: z.array(z.string()),
  ollama_url: z.string(),
  ollama_context_tokens: z.number(),
  ollama_output_tokens: z.number(),
  timeout_seconds: z.number(),
  min_score: z.number(),
  min_confidence: z.number(),
  max_jobs_per_hour: z.number(),
  max_revisions: z.number(),
  max_retries: z.number(),
  allowed_flags: z.array(z.string()),
  allow_cloud_upload: z.boolean().optional(),
});
export const editingSchema = z.object({
  min_seconds: z.number(),
  max_seconds: z.number(),
  captions_enabled: z.boolean(),
  caption_words: z.number(),
  layout: z.enum(['auto', 'contain', 'focus', 'split']),
  sfx_enabled: z.boolean(),
  overlays_enabled: z.boolean(),
  max_sfx: z.number(),
  max_overlays: z.number(),
  sfx_gain_db: z.number(),
  style_notes: z.string(),
});
export const mediaSchema = z.object({
  engine: z.enum(['builtin', 'reel-studio']),
  transcription_model: z.string(),
  transcription_python: z.string(),
  transcription_language: z.string(),
  asset_catalog: z.string(),
  asset_root: z.string(),
  editorial_preferences: z.string(),
});
export const perceptionControlsSchema = z.object({
  ollama_url: z.string(),
  command: z.array(z.string()),
  timeout_seconds: z.number(),
  max_seconds: z.number(),
  max_megabytes: z.number(),
});
export const deliverySchema = z.object({
  public_base_url: z.string(),
  public_media_base: z.string(),
  s3_bucket: z.string(),
  s3_endpoint: z.string(),
  s3_region: z.string(),
  api_version: z.string(),
});
export const contentFlagSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
  default_action: z.enum(['allow', 'hold']).optional(),
});
export type CaptureSettings = z.infer<typeof captureSchema>;
export type DetectorSettings = z.infer<typeof detectorSchema>;
export type AgentControls = z.infer<typeof agentControlsSchema>;
export type EditingSettings = z.infer<typeof editingSchema>;
export type MediaSettings = z.infer<typeof mediaSchema>;
export type PerceptionControls = z.infer<typeof perceptionControlsSchema>;
export type DeliverySettings = z.infer<typeof deliverySchema>;
export type ContentFlag = z.infer<typeof contentFlagSchema>;

export const installationSchema = z.object({
  status: z.enum(['idle', 'running', 'succeeded', 'failed']),
  model: z.string().nullable().optional(),
  message: z.string().optional(),
});
const serviceSchema = z.object({
  enabled: z.boolean(),
  state: z.enum(['running', 'paused', 'pausing', 'stopped', 'starting']),
  heartbeat_at: z.number().nullable(),
});
export const systemSchema = z.object({
  managed: z.boolean(),
  services: z.object({ capture: serviceSchema, editor: serviceSchema }),
  installation: installationSchema,
});
export type SystemState = z.infer<typeof systemSchema>;
