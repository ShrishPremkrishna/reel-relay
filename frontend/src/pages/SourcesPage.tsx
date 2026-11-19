import { Activity, Clock3, MessageSquare, Plus, Radio, Settings2 } from 'lucide-react';
import type { Setup, Workspace } from '../lib/models';
import { channelStatus, duration, isRecording } from '../lib/domain';
import { Badge, EmptyState, Notice, PageHeader } from '../components/primitives';

export function SourcesPage({ state, setup }: { state: Workspace; setup: Setup }) {
  return (
    <>
      <PageHeader
        eyebrow="LIVE INPUT"
        title="Keep an eye on the right streams."
        description="A rolling buffer keeps the context. Chat activity tells the editor where to look."
        action={
          <a className="button button-primary" href="/setup#sources">
            <Plus size={17} />
            Add a source
          </a>
        }
      />
      {!setup.channels.length ? (
        <section className="panel">
          <EmptyState
            icon={<Radio size={28} />}
            title="No sources yet"
            description="Add a Twitch username to start watching for potential highlights."
            action={
              <a className="button button-primary" href="/setup#sources">
                Choose a source
              </a>
            }
          />
        </section>
      ) : (
        <div className="source-grid">
          {setup.channels.map((c) => {
            const health = state.channels.find((h) => h.channel === c.name);
            const s = channelStatus(health);
            const needsStart = c.enabled && (!health || health.capture_state === 'stopped');
            const intakePaused =
              c.enabled &&
              health &&
              isRecording(health) &&
              ['processing_backlog_full', 'pending_windows_full'].includes(
                health.intake_state || '',
              );
            return (
              <article className="panel source-card" key={c.name}>
                <header>
                  <span className="source-avatar">{c.name.slice(0, 1).toUpperCase()}</span>
                  <div>
                    <h2>{c.name}</h2>
                    <p>Twitch · {c.chat === 'eventsub' ? 'Official chat' : 'Public chat'}</p>
                  </div>
                  <Badge tone={!c.enabled ? 'neutral' : s.tone}>
                    {c.enabled ? s.label : 'Not watching'}
                  </Badge>
                </header>
                <dl className="source-metrics">
                  <div>
                    <dt>
                      <Clock3 size={15} />
                      Recorded
                    </dt>
                    <dd>{duration(health?.recorded_seconds)}</dd>
                  </div>
                  <div>
                    <dt>
                      <MessageSquare size={15} />
                      Chat messages
                    </dt>
                    <dd>{(health?.chat_messages || 0).toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>
                      <Activity size={15} />
                      Pending moments
                    </dt>
                    <dd>{health?.pending_windows || 0}</dd>
                  </div>
                </dl>
                {(health?.capture_error || health?.chat_error) && (
                  <Notice tone="warning">{health.capture_error || health.chat_error}</Notice>
                )}
                {intakePaused && (
                  <Notice tone="warning">
                    <strong>
                      {health.intake_state === 'processing_backlog_full'
                        ? 'New clips paused while the editor catches up.'
                        : 'New clips paused while pending capture windows finish.'}
                    </strong>
                    <span>
                      Recording continues. New clips resume automatically when there is room.
                    </span>
                    {health.intake_state === 'processing_backlog_full' &&
                      health.processing_backlog !== undefined &&
                      health.processing_limit !== undefined && (
                        <span>
                          Editing queue: {health.processing_backlog} of {health.processing_limit}.
                          Completed and held clips do not count.
                        </span>
                      )}
                  </Notice>
                )}
                {needsStart && (
                  <Notice tone="neutral">
                    <strong>Configured to watch; capture is stopped.</strong>
                    <span>
                      {setup.config_version ? (
                        <>
                          Open{' '}
                          <a className="text-link" href="/setup#runtime">
                            Workspace services
                          </a>{' '}
                          and start live capture. Keep this workspace running while you want streams
                          captured.
                        </>
                      ) : (
                        <>
                          From the project folder, start the workspace with{' '}
                          <code>./relay start</code>. Keep it running while you want streams
                          captured.
                        </>
                      )}
                    </span>
                  </Notice>
                )}
                <footer>
                  <span>
                    {c.enabled ? 'Configured to watch' : 'Capture is paused for this source'}
                  </span>
                  <a className="text-link" href="/setup#sources">
                    <Settings2 size={15} />
                    Manage
                  </a>
                </footer>
              </article>
            );
          })}
        </div>
      )}
      <section className="panel source-explainer">
        <Radio size={22} />
        <div>
          <h2>A spike is a lead, not a punchline.</h2>
          <p>
            The editor checks the setup, payoff and surrounding context before making a cut.
            Giveaways, repeated messages and unfinished moments can be passed over. A quieter queue
            can still mean the system is working.
          </p>
        </div>
      </section>
    </>
  );
}
