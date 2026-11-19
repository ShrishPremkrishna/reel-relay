import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  Film,
  Inbox,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  Sparkles,
} from 'lucide-react';
import type { Readiness, Setup, Workspace } from '../lib/models';
import {
  candidateTitle,
  chooseCandidate,
  countGroup,
  filterCandidates,
  isRecording,
  status,
  timeAgo,
  type QueueFilter,
} from '../lib/domain';
import { Badge, Button, EmptyState, Notice, PageHeader } from '../components/primitives';
import { MediaIntake } from '../components/MediaIntake';
import { ClipDetail } from '../components/ClipDetail';

const filters: { id: QueueFilter; label: string }[] = [
  { id: 'review', label: 'Needs review' },
  { id: 'ready', label: 'Ready' },
  { id: 'working', label: 'In progress' },
  { id: 'published', label: 'Published' },
  { id: 'all', label: 'All clips' },
];
export function QueuePage({
  state,
  setup,
  readiness,
  refreshing,
  reload,
  notify,
}: {
  state: Workspace;
  setup: Setup;
  readiness: Readiness | null;
  refreshing: boolean;
  reload: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [filter, setFilter] = useState<QueueFilter>(() => {
    const linked = state.candidates.find(
      (c) => c.id === new URLSearchParams(location.search).get('clip'),
    );
    return linked ? status(linked.status).group : 'review';
  });
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(new URLSearchParams(location.search).get('clip'));
  const preview = useRef<HTMLDivElement>(null);
  const [previewRequest, setPreviewRequest] = useState(0);
  useEffect(() => {
    if (!previewRequest || !window.matchMedia('(max-width: 1050px)').matches) return;
    preview.current?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    });
    preview.current?.focus({ preventScroll: true });
  }, [previewRequest]);
  const clips = useMemo(
    () => filterCandidates(state.candidates, filter, search),
    [state.candidates, filter, search],
  );
  const missingSelection = !!selectedId && !state.candidates.some((c) => c.id === selectedId);
  const selected = missingSelection ? undefined : chooseCandidate(clips, selectedId);
  const hasOlderMatches = !search && !clips.length && countGroup(state, filter) > 0;
  const recording = state.channels.filter((c) => isRecording(c));
  const ready = countGroup(state, 'ready');
  const active = countGroup(state, 'working');
  const total = countGroup(state, 'all');
  const publishing = state.settings.publishing;
  function select(id: string) {
    setSelectedId(id);
    setPreviewRequest((n) => n + 1);
    const url = new URL(location.href);
    url.searchParams.set('clip', id);
    history.replaceState(null, '', url);
  }
  function showLatest() {
    setSelectedId(null);
    setFilter('all');
    const url = new URL(location.href);
    url.searchParams.delete('clip');
    history.replaceState(null, '', url);
  }
  return (
    <>
      <PageHeader
        eyebrow="YOUR WORKSPACE"
        title="Make the moment count."
        description="Live streams in. Stories worth sharing out."
        action={
          <div className="button-row">
            <MediaIntake
              mode="clip"
              csrf={setup.csrf}
              notify={notify}
              onComplete={async (id) => {
                await reload();
                setFilter('all');
                select(id);
              }}
            />
            <Button onClick={() => void reload()} busy={refreshing}>
              <RefreshCw size={16} />
              Refresh
            </Button>
          </div>
        }
      />
      {!setup.channels.length && (
        <div className="onboarding-panel">
          <span className="onboarding-icon">
            <Sparkles size={26} />
          </span>
          <div>
            <h2>Your first clip starts with a source.</h2>
            <p>
              Add a Twitch channel, choose an editor and connect Instagram. The pipeline takes it
              from there.
            </p>
          </div>
          <a className="button button-primary" href="/setup#sources">
            Set up your workspace
          </a>
        </div>
      )}
      <section className="metrics" aria-label="Workspace overview">
        <Metric
          label="Watching live"
          value={recording.length}
          detail={recording.map((c) => c.channel).join(', ') || 'No streams recording'}
          icon={<Radio size={18} />}
          tone={recording.length ? 'success' : 'neutral'}
        />
        <Metric
          label="In the pipeline"
          value={active}
          detail="Selecting, editing & checking"
          icon={<Sparkles size={18} />}
        />
        <Metric
          label="Ready to share"
          value={ready}
          detail={
            state.settings.automatic ? 'Queued when checks pass' : 'Awaiting publishing approval'
          }
          icon={<Film size={18} />}
        />
        <Metric
          label="Published"
          value={state.counts.published || 0}
          detail={`Ceiling: ${state.settings.daily_limit} per rolling day`}
          icon={<CheckCircle2 size={18} />}
        />
      </section>
      {!publishing && (
        <Notice
          tone="neutral"
          action={
            <a className="text-link" href="/setup#instagram">
              {setup.accounts.instagram.connected ? 'Finish publishing setup' : 'Connect Instagram'}
            </a>
          }
        >
          <strong>
            {setup.accounts.instagram.connected
              ? 'Publishing is paused.'
              : 'Your clips can flow straight to Instagram.'}
          </strong>
          <span>
            {setup.accounts.instagram.connected
              ? 'Capture and editing continue. Enable publishing when the account and quality checks are ready.'
              : 'Connect the dedicated account and complete setup to publish automatically.'}
          </span>
        </Notice>
      )}
      {readiness && !readiness.can_edit && (
        <Notice
          tone="warning"
          action={
            <a className="text-link" href="/setup#editor">
              Check editor setup
            </a>
          }
        >
          <strong>Editing needs setup.</strong>
          <span>
            Captured moments will stay in the queue until the required editor and media tools are
            ready.
          </span>
        </Notice>
      )}
      {missingSelection && (
        <Notice tone="warning" action={<Button onClick={showLatest}>View latest clips</Button>}>
          <strong>This clip is outside the current queue snapshot.</strong>
          <span>
            It may be older than the loaded moments or no longer available. Choose a clip from the
            list to review it.
          </span>
        </Notice>
      )}
      <div className="queue-workspace">
        <section className="queue-list-section" aria-label="Clip queue">
          <div className="queue-heading">
            <div>
              <h2>
                Your clips <span className="count-chip">{total}</span>
              </h2>
              <p>Strong moments rise. The rest stay out.</p>
            </div>
            <a className="icon-button" aria-label="Open workflow settings" href="/setup">
              <Settings2 size={18} />
            </a>
          </div>
          <div className="queue-tabs" role="tablist" aria-label="Clip status">
            {filters.map((f, index) => (
              <button
                role="tab"
                aria-selected={filter === f.id}
                aria-label={`${f.label} ${countGroup(state, f.id)}`}
                aria-controls="clip-list"
                id={`filter-${f.id}`}
                tabIndex={filter === f.id ? 0 : -1}
                key={f.id}
                onClick={() => setFilter(f.id)}
                onKeyDown={(e) => {
                  const next =
                    e.key === 'ArrowRight'
                      ? (index + 1) % filters.length
                      : e.key === 'ArrowLeft'
                        ? (index + filters.length - 1) % filters.length
                        : e.key === 'Home'
                          ? 0
                          : e.key === 'End'
                            ? filters.length - 1
                            : -1;
                  if (next < 0) return;
                  e.preventDefault();
                  setFilter(filters[next].id);
                  document.getElementById(`filter-${filters[next].id}`)?.focus();
                }}
                className={filter === f.id ? 'active' : ''}
              >
                {f.label}
                <span>{countGroup(state, f.id)}</span>
              </button>
            ))}
          </div>
          <label className="search-field">
            <Search size={17} aria-hidden="true" />
            <input
              type="search"
              aria-label="Search clips"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search a creator or moment"
            />
            {search && (
              <button type="button" onClick={() => setSearch('')}>
                Clear
              </button>
            )}
          </label>
          <div
            className="clip-list"
            role="tabpanel"
            id="clip-list"
            aria-labelledby={`filter-${filter}`}
          >
            {clips.map((c) => {
              const s = status(c.status);
              return (
                <button
                  type="button"
                  className={`clip-card ${selected?.id === c.id ? 'selected' : ''}`}
                  aria-pressed={selected?.id === c.id}
                  key={c.id}
                  onClick={() => select(c.id)}
                >
                  <div className="clip-thumbnail">
                    {c.has_export || c.has_evidence ? (
                      <img
                        loading="lazy"
                        src={`/media/${c.id}/poster${c.export_version ? '?v=' + encodeURIComponent(c.export_version) : ''}`}
                        alt=""
                      />
                    ) : (
                      <Film size={24} />
                    )}
                    <span className="thumbnail-type">{c.has_export ? 'EDIT' : 'SOURCE'}</span>
                  </div>
                  <div className="clip-card-body">
                    <div className="clip-meta">
                      <span>
                        {c.channel}
                        {c.is_test ? ' · test' : ''}
                      </span>
                      <time dateTime={new Date(c.created_at * 1000).toISOString()}>
                        {timeAgo(c.created_at)}
                      </time>
                    </div>
                    <h3>{candidateTitle(c)}</h3>
                    {c.decision?.reason && <p className="clip-card-reason">{c.decision.reason}</p>}
                    <div className="clip-card-footer">
                      <Badge tone={s.tone}>{s.label}</Badge>
                      {c.score !== null && (
                        <span
                          className="score-label"
                          aria-label={`Editorial score ${c.score.toFixed(1)} out of 10`}
                        >
                          {c.score.toFixed(1)}
                          <span>/10</span>
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              );
            })}
            {!clips.length && (
              <EmptyState
                icon={<Inbox size={28} />}
                title={
                  search
                    ? 'No matching moments'
                    : hasOlderMatches
                      ? 'Older clips are outside this view'
                      : filter === 'review'
                        ? 'Nothing needs your attention'
                        : 'No clips here yet'
                }
                description={
                  search
                    ? 'Try a creator name or a shorter search.'
                    : hasOlderMatches
                      ? 'This status has clips outside the latest loaded moments. The counts above include those older clips.'
                      : filter === 'review'
                        ? 'Clips that need a decision or a closer look will appear here. Check In progress for ongoing work.'
                        : 'New clips will appear as sources are captured and move through the pipeline.'
                }
                action={
                  filter !== 'all' && !search ? (
                    <Button onClick={() => setFilter('all')}>View all clips</Button>
                  ) : undefined
                }
              />
            )}
          </div>
          {total > state.candidates.length && (
            <p className="field-help list-note">
              Showing the latest {state.candidates.length} moments. Counts include the full queue.
            </p>
          )}
        </section>
        <div
          className="preview-destination"
          ref={preview}
          tabIndex={-1}
          aria-label="Selected clip preview"
        >
          <ClipDetail
            candidate={selected}
            csrf={state.settings.csrf}
            onChange={reload}
            notify={notify}
          />
        </div>
      </div>
    </>
  );
}
function Metric({
  label,
  value,
  detail,
  icon,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  detail: string;
  icon: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {label}
        <span className={`metric-icon ${tone}`}>{icon}</span>
      </div>
      <strong>{value.toLocaleString()}</strong>
      <p>{detail}</p>
    </div>
  );
}
