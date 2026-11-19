import type { Candidate, ChannelHealth, Workspace } from './models';

export type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'active';
export type QueueFilter = 'review' | 'ready' | 'working' | 'published' | 'all';
const states: Record<string, { label: string; tone: Tone; group: QueueFilter }> = {
  captured: { label: 'Captured', tone: 'neutral', group: 'working' },
  preparing: { label: 'Preparing', tone: 'active', group: 'working' },
  needs_agent: { label: 'Waiting for editor', tone: 'neutral', group: 'working' },
  judging: { label: 'Selecting the story', tone: 'active', group: 'working' },
  selected: { label: 'Selected', tone: 'active', group: 'working' },
  rendering: { label: 'Editing', tone: 'active', group: 'working' },
  needs_preview_review: { label: 'Checking the edit', tone: 'active', group: 'working' },
  reviewing: { label: 'Checking the edit', tone: 'active', group: 'working' },
  preview_changes: { label: 'Revision queued', tone: 'active', group: 'working' },
  revising: { label: 'Refining', tone: 'active', group: 'working' },
  editorial_review: { label: 'Needs a closer look', tone: 'warning', group: 'review' },
  needs_human_review: { label: 'Needs your review', tone: 'warning', group: 'review' },
  ready: { label: 'Ready to approve', tone: 'success', group: 'ready' },
  approved: { label: 'Queued to publish', tone: 'success', group: 'ready' },
  uploading: { label: 'Uploading', tone: 'active', group: 'working' },
  container_ready: { label: 'Instagram processing', tone: 'active', group: 'working' },
  publishing: { label: 'Publishing', tone: 'active', group: 'working' },
  published: { label: 'Published', tone: 'success', group: 'published' },
  publish_unknown: { label: 'Check Instagram', tone: 'danger', group: 'review' },
  publish_failed: { label: 'Publishing issue', tone: 'danger', group: 'review' },
  rejected: { label: 'Passed over', tone: 'neutral', group: 'all' },
  failed: { label: 'Needs attention', tone: 'danger', group: 'review' },
};
export const status = (value: string) =>
  states[value] || {
    label: value.replaceAll('_', ' '),
    tone: 'neutral' as Tone,
    group: 'all' as QueueFilter,
  };
export function filterCandidates(candidates: Candidate[], filter: QueueFilter, search: string) {
  const needle = search.trim().toLowerCase();
  return candidates.filter(
    (c) =>
      (filter === 'all' || status(c.status).group === filter) &&
      (!needle ||
        [c.channel, c.decision?.hook, c.decision?.reason, c.id].some((v) =>
          v?.toLowerCase().includes(needle),
        )),
  );
}
export function candidateTitle(c: Candidate) {
  const title = c.decision?.hook?.trim() || c.caption?.split('\n')[0]?.trim();
  if (title) return title.length > 90 ? title.slice(0, 87).trimEnd() + '…' : title;
  const seconds = (c.end ?? 0) - (c.start ?? 0);
  return `Source moment${seconds > 0 ? ' · ' + duration(seconds) : ''}`;
}
export function chooseCandidate(candidates: Candidate[], selected: string | null) {
  return (
    candidates.find((c) => c.id === selected) ||
    candidates.find((c) => c.has_export && c.status !== 'published' && c.status !== 'rejected') ||
    candidates[0]
  );
}
export function countGroup(state: Workspace, group: QueueFilter) {
  return Object.entries(state.counts).reduce(
    (n, [key, value]) => n + (group === 'all' || status(key).group === group ? value : 0),
    0,
  );
}
export function isRecording(c: ChannelHealth, now = Date.now() / 1000) {
  return c.capture_state === 'recording' && now - c.updated_at < 30;
}
export function channelStatus(c: ChannelHealth | undefined) {
  if (!c) return { label: 'Not started', tone: 'neutral' as Tone };
  if (isRecording(c)) return { label: 'Recording', tone: 'success' as Tone };
  if (c.capture_error || c.chat_error) return { label: 'Needs attention', tone: 'warning' as Tone };
  if (c.capture_state === 'recording')
    return { label: 'No recent update', tone: 'warning' as Tone };
  return { label: (c.capture_state || 'Waiting').replaceAll('_', ' '), tone: 'neutral' as Tone };
}
export function duration(seconds: number | undefined) {
  if (!seconds || seconds <= 0) return '—';
  if (seconds < 1) return `${Number(seconds.toFixed(2)) || '<0.01'}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}
export function timeAgo(timestamp: number) {
  const seconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(timestamp * 1000).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
}
export const canReject = new Set([
  'captured',
  'needs_agent',
  'editorial_review',
  'selected',
  'ready',
  'approved',
  'failed',
  'preview_changes',
  'needs_preview_review',
  'needs_human_review',
]);
