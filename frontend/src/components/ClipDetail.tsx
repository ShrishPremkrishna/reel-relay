import { useState } from 'react';
import {
  Check,
  ChevronDown,
  Download,
  ExternalLink,
  FileText,
  Film,
  GitCompareArrows,
  MessageSquare,
  Play,
  RotateCcw,
  Volume2,
  X,
} from 'lucide-react';
import { ApiError, message, mutate, safeExternalUrl } from '../lib/api';
import { canReject, status } from '../lib/domain';
import type { Candidate } from '../lib/models';
import { Badge, Button, EmptyState, Field, Notice } from './primitives';
import { MediaPreview } from './MediaPreview';

interface Props {
  candidate?: Candidate;
  csrf: string;
  onChange: () => Promise<void>;
  notify: (text: string) => void;
}

export function ClipDetail({ candidate, csrf, onChange, notify }: Props) {
  if (!candidate)
    return (
      <section className="panel preview-panel">
        <EmptyState
          icon={<Play size={27} />}
          title="A closer look"
          description="Choose a moment to see the source, the edit and its quality checks."
        />
      </section>
    );
  return (
    <ClipDetailContent
      key={`${candidate.id}:${candidate.export_version || ''}`}
      candidate={candidate}
      csrf={csrf}
      onChange={onChange}
      notify={notify}
    />
  );
}

function ClipDetailContent({
  candidate: c,
  csrf,
  onChange,
  notify,
}: Required<Omit<Props, 'candidate'>> & { candidate: Candidate }) {
  const [source, setSource] = useState(false);
  const [notes, setNotes] = useState('');
  const [reassessmentNotes, setReassessmentNotes] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [watched, setWatched] = useState(false);
  const [listened, setListened] = useState(false);
  const [permalink, setPermalink] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState('');
  const current = status(c.status);
  async function act(kind: string, extra: Record<string, unknown> = {}, success = 'Saved.') {
    setError('');
    setPending(kind);
    try {
      await mutate(
        `/api/${kind}`,
        { id: c.id, ...(kind === 'retry' ? {} : { version: c.updated_at }), ...extra },
        csrf,
      );
      notify(success);
      await onChange();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? 'This clip changed while you were reviewing it. Refresh the queue and review the latest version before trying again. Your changes have not been submitted.'
          : message(e),
      );
    } finally {
      setPending(null);
    }
  }
  const mediaKind = source || !c.has_export ? 'source' : 'video';
  const media = `/media/${c.id}/${mediaKind}${mediaKind === 'video' && c.export_version ? '?v=' + encodeURIComponent(c.export_version) : ''}`;
  const postUrl = safeExternalUrl(c.permalink);
  const needsHuman =
    c.has_export && ['needs_human_review', 'needs_preview_review'].includes(c.status);
  return (
    <section className="panel preview-panel" aria-label="Clip preview">
      <header className="preview-heading">
        <div>
          <span className="eyebrow">{mediaKind === 'source' ? 'THE SOURCE' : 'THE EDIT'}</span>
          <h2>{c.channel}</h2>
        </div>
        <Badge tone={current.tone}>{current.label}</Badge>
      </header>
      <MediaPreview
        key={media}
        src={media}
        source={mediaKind === 'source'}
        poster={
          c.has_export && mediaKind === 'video'
            ? `/media/${c.id}/poster?v=${encodeURIComponent(c.export_version || String(c.updated_at))}`
            : undefined
        }
        label={`${c.channel} ${mediaKind === 'video' ? 'edited clip' : 'source moment'}`}
      />
      <div className="preview-content">
        {c.has_export && (
          <div className="preview-tools">
            <Button variant="ghost" onClick={() => setSource(!source)}>
              <GitCompareArrows size={16} />
              {source ? 'Show edited clip' : 'Compare with source'}
            </Button>
            <a
              className="icon-button"
              aria-label="Download edited MP4"
              href={`/media/${c.id}/video?download=true`}
            >
              <Download size={18} />
            </a>
          </div>
        )}
        <h3 className="clip-detail-title">{c.decision?.hook || 'The moment behind the clip'}</h3>
        {c.is_test && (
          <Notice tone="neutral">Synthetic test footage. This clip cannot be published.</Notice>
        )}
        {c.error && <Notice tone="danger">{c.error}</Notice>}
        {c.status === 'publish_unknown' && (
          <Notice tone="danger">
            Instagram may have received this post. Check the account and reconcile the existing
            attempt before trying anything else.
          </Notice>
        )}
        {c.review?.notes && <p className="review-summary">{c.review.notes}</p>}
        <div className="clip-primary-actions">
          {c.status === 'ready' && !c.is_test && (
            <Button
              variant="primary"
              disabled={!!pending}
              busy={pending === 'approve'}
              onClick={() => void act('approve', {}, 'Clip approved for the publishing queue.')}
            >
              <Check size={17} />
              Approve clip
            </Button>
          )}
          {c.status === 'failed' && (
            <Button
              variant="primary"
              disabled={!!pending}
              busy={pending === 'retry'}
              onClick={() => void act('retry', {}, 'Edit returned to the queue.')}
            >
              <RotateCcw size={17} />
              Retry edit
            </Button>
          )}
          {c.status === 'publish_failed' && !c.is_test && (
            <Button
              variant="primary"
              disabled={!!pending}
              busy={pending === 'retry-publication'}
              onClick={() =>
                void act(
                  'retry-publication',
                  {},
                  'Reviewed export returned to the publishing queue.',
                )
              }
            >
              <RotateCcw size={17} />
              Retry failed upload
            </Button>
          )}
          {canReject.has(c.status) && (
            <Button
              variant="ghost"
              disabled={!!pending}
              busy={pending === 'reject'}
              onClick={() => void act('reject', {}, 'Moment passed over.')}
            >
              <X size={16} />
              Pass over
            </Button>
          )}
          {postUrl && (
            <a
              className="button button-secondary"
              href={postUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink size={16} />
              View on Instagram
            </a>
          )}
        </div>
        {c.status === 'publish_failed' && (
          <p className="field-help">
            The server must confirm that the earlier upload failed before publication began. A retry
            keeps the existing reviewed export and does not enable posting.
          </p>
        )}
        {error && <Notice tone="danger">{error}</Notice>}
        {c.has_evidence && ['editorial_review', 'rejected'].includes(c.status) && (
          <div className="reassessment-panel">
            <h3>Give the editor another look.</h3>
            <p className="field-help">
              Reassess the original moment with fresh context. The previous decision is kept, and
              all selection and quality checks run again.
            </p>
            <details className="disclosure">
              <summary>
                Add context (optional)
                <ChevronDown size={16} />
              </summary>
              <Field label="Context for reassessment">
                <textarea
                  rows={3}
                  maxLength={1500}
                  value={reassessmentNotes}
                  onChange={(e) => setReassessmentNotes(e.target.value)}
                  placeholder="What makes this moment work, or what did the first review miss?"
                />
              </Field>
            </details>
            <Button
              variant="primary"
              disabled={!!pending}
              busy={pending === 'reassess'}
              onClick={() =>
                void act(
                  'reassess',
                  { notes: reassessmentNotes.trim() },
                  'Moment queued for reassessment. Selection and quality checks will run again.',
                )
              }
            >
              <RotateCcw size={16} />
              Reassess moment
            </Button>
          </div>
        )}
        {c.can_request_changes && (
          <details className="disclosure">
            <summary>
              <MessageSquare size={17} />
              Request changes
              <ChevronDown size={16} />
            </summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (notes.trim().length < 3) {
                  setError('Describe the change in at least three characters.');
                  return;
                }
                void act(
                  'request-changes',
                  { notes: notes.trim() },
                  'Feedback saved. A new version will be edited and reviewed.',
                );
              }}
            >
              <Field
                label="What should change?"
                hint="The current export is kept. The new version goes through quality checks again."
              >
                <textarea
                  rows={3}
                  minLength={3}
                  maxLength={1500}
                  required
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Keep the pause before the reveal; make the reaction larger; use fewer effects."
                />
              </Field>
              <Button disabled={!!pending} busy={pending === 'request-changes'} type="submit">
                Send to editor
              </Button>
            </form>
          </details>
        )}
        {needsHuman && mediaKind === 'source' && (
          <Notice
            tone="neutral"
            action={
              <Button onClick={() => setSource(false)}>Return to edit for final check</Button>
            }
          >
            You are comparing the original source. Complete the final preview check on the edited
            export.
          </Notice>
        )}
        {needsHuman && mediaKind === 'video' && (
          <details className="disclosure">
            <summary>
              <Volume2 size={17} />
              Complete a preview check
              <ChevronDown size={16} />
            </summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!watched || !listened || reviewNotes.trim().length < 3) {
                  setError('Watch and listen to this export, then describe what you checked.');
                  return;
                }
                void act(
                  'operator-review',
                  { notes: reviewNotes.trim(), watched: true, listened: true },
                  'Preview check saved. The clip is ready for publishing approval.',
                );
              }}
            >
              <p className="field-help">
                Resolve the issues above before clearing this exact export. You can request changes
                instead.
              </p>
              <Field label="Your review note">
                <textarea
                  value={reviewNotes}
                  onChange={(e) => setReviewNotes(e.target.value)}
                  minLength={3}
                  maxLength={1500}
                  required
                  rows={3}
                  placeholder="What did you check or resolve?"
                />
              </Field>
              <label className="check-field">
                <input
                  type="checkbox"
                  required
                  checked={watched}
                  onChange={(e) => setWatched(e.target.checked)}
                />
                <span>I checked the captions, framing and payoff.</span>
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  required
                  checked={listened}
                  onChange={(e) => setListened(e.target.checked)}
                />
                <span>I listened to speech and effect levels.</span>
              </label>
              <Button type="submit" disabled={!!pending} busy={pending === 'operator-review'}>
                Save preview check
              </Button>
            </form>
          </details>
        )}
        <details className="disclosure">
          <summary>
            <Film size={17} />
            Why this moment?
            <ChevronDown size={16} />
          </summary>
          <p className="evidence-copy">
            {c.decision?.reason || 'The editor has not assessed this moment yet.'}
          </p>
          {c.score !== null && (
            <div className="quality-score">
              <strong>
                {c.score.toFixed(1)}
                <span>/10</span>
              </strong>
              <p>
                Editorial score<span>A quality assessment, not a prediction of reach.</span>
              </p>
            </div>
          )}
          {c.decision?.flags && c.decision.flags.length > 0 && (
            <ul className="issue-list">
              {c.decision.flags.map((flag) => (
                <li key={flag}>{flag}</li>
              ))}
            </ul>
          )}
          {c.origin && <p className="metadata-line">Source: {c.origin.replaceAll('_', ' ')}</p>}
          <p className="metadata-line">
            Clip ID <code>{c.id}</code>
          </p>
        </details>
        {c.has_export && (
          <details className="disclosure">
            <summary>
              <FileText size={17} />
              Caption & export files
              <ChevronDown size={16} />
            </summary>
            <Field label="Caption with source credit">
              <textarea readOnly rows={5} value={c.caption || ''} />
            </Field>
            <div className="button-row">
              <Button
                onClick={() =>
                  void navigator.clipboard
                    .writeText(c.caption || '')
                    .then(() => notify('Caption copied.'))
                    .catch(() =>
                      setError(
                        'Clipboard access was unavailable. Select and copy the caption above.',
                      ),
                    )
                }
              >
                Copy caption
              </Button>
              <a className="text-link" href={`/media/${c.id}/credits?download=true`}>
                Download credits
              </a>
            </div>
          </details>
        )}
        {!c.is_test &&
          c.has_export &&
          c.review?.approved &&
          ['ready', 'approved'].includes(c.status) && (
            <details className="disclosure">
              <summary>
                <ExternalLink size={17} />
                Record an existing manual post
                <ChevronDown size={16} />
              </summary>
              <p className="field-help">
                Only use this after uploading the exact file yourself. It prevents a later API
                duplicate; it does not publish anything.
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!confirmed) return;
                  void act(
                    'record-manual-post',
                    { permalink: permalink.trim(), confirmed: true },
                    'Manual upload recorded. This clip will not be posted again.',
                  );
                }}
              >
                <Field label="Published Instagram Reel URL">
                  <input
                    type="url"
                    value={permalink}
                    onChange={(e) => setPermalink(e.target.value)}
                    required
                    maxLength={2000}
                    placeholder="https://www.instagram.com/reel/…/"
                  />
                </Field>
                <label className="check-field">
                  <input
                    type="checkbox"
                    required
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  <span>I uploaded this exact export and checked the published post.</span>
                </label>
                <Button type="submit" disabled={!!pending} busy={pending === 'record-manual-post'}>
                  Record manual post
                </Button>
              </form>
            </details>
          )}
        {c.posted_manually && (
          <p className="field-help">
            Manual upload recorded from your confirmation; not verified through Instagram’s API.
          </p>
        )}
      </div>
    </section>
  );
}
