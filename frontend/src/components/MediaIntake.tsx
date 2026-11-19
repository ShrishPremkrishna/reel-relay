import { useEffect, useId, useRef, useState } from 'react';
import { Upload, X } from 'lucide-react';
import { z } from 'zod';
import { message, request } from '../lib/api';
import { Button, Field, Notice } from './primitives';

const MAX_BYTES = 100 * 1024 * 1024;
const VIDEO = ['mp4', 'mov', 'mkv', 'webm'];
const AUDIO = ['mp3', 'wav', 'ogg', 'flac'];
const IMAGE = ['png', 'jpg', 'jpeg', 'webp'];
export function validateMediaFile(file: File, mode: 'clip' | 'asset', kind = 'sfx') {
  const extension = file.name.split('.').pop()?.toLowerCase() || '';
  if (file.size === 0) return 'Choose a file that contains media.';
  if (file.size > MAX_BYTES) return 'Choose a file no larger than 100 MB.';
  const allowed =
    mode === 'clip' ? VIDEO : kind === 'sfx' ? [...AUDIO, ...VIDEO] : [...IMAGE, ...VIDEO];
  if (!allowed.includes(extension))
    return mode === 'clip'
      ? 'Choose an MP4, MOV, MKV or WebM video.'
      : kind === 'sfx'
        ? 'Sound effects need an audio or video file with an audio track.'
        : 'Reactions and B-roll need a supported image or video.';
  return '';
}
export function MediaIntake({
  mode,
  csrf,
  onComplete,
  notify,
}: {
  mode: 'clip' | 'asset';
  csrf: string;
  onComplete: (id: string) => Promise<void>;
  notify: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Upload size={16} />
        {mode === 'clip' ? 'Import video' : 'Add an effect'}
      </Button>
      {open && (
        <IntakeDialog
          mode={mode}
          csrf={csrf}
          onClose={() => setOpen(false)}
          onComplete={onComplete}
          notify={notify}
        />
      )}
    </>
  );
}
function IntakeDialog({
  mode,
  csrf,
  onClose,
  onComplete,
  notify,
}: {
  mode: 'clip' | 'asset';
  csrf: string;
  onClose: () => void;
  onComplete: (id: string) => Promise<void>;
  notify: (message: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null),
    [kind, setKind] = useState('sfx'),
    [title, setTitle] = useState(''),
    [channel, setChannel] = useState(''),
    [rights, setRights] = useState(''),
    [attribution, setAttribution] = useState(''),
    [tags, setTags] = useState(''),
    [pending, setPending] = useState(false),
    [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null),
    input = useRef<HTMLInputElement>(null),
    mounted = useRef(true);
  const heading = useId();
  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    if (element?.showModal) element.showModal();
    else element?.setAttribute('open', '');
    return () => {
      mounted.current = false;
      element?.close?.();
      previous?.focus();
    };
  }, []);
  function choose(next: File | undefined) {
    if (!next) return;
    const issue = validateMediaFile(next, mode, kind);
    setError(issue);
    if (issue) {
      setFile(null);
      if (input.current) input.current.value = '';
      return;
    }
    setFile(next);
    if (!title) setTitle(next.name.replace(/\.[^.]+$/, ''));
  }
  async function submit() {
    if (!file) return;
    const issue = validateMediaFile(file, mode, kind);
    if (issue) {
      setError(issue);
      return;
    }
    setPending(true);
    setError('');
    try {
      const params = new URLSearchParams({ filename: file.name, attribution: attribution.trim() });
      if (mode === 'clip') params.set('channel', channel.trim() || 'import');
      else {
        params.set('title', title.trim());
        params.set('kind', kind);
        params.set('rights', rights.trim());
        params.set(
          'tags',
          tags
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
            .join(','),
        );
      }
      const result = await request(
        `${mode === 'clip' ? '/api/import' : '/api/assets/upload'}?${params}`,
        z.object({ id: z.string() }),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream', 'X-Relay-Token': csrf },
          body: file,
        },
      );
      if (!mounted.current) return;
      notify(
        mode === 'clip'
          ? 'Video imported. The editor will process it when running.'
          : 'Effect added to the library.',
      );
      onClose();
      await onComplete(result.id);
    } catch (e) {
      if (mounted.current) setError(message(e));
    } finally {
      if (mounted.current) setPending(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="media-dialog"
      aria-labelledby={heading}
      onCancel={(e) => {
        e.preventDefault();
        if (!pending) onClose();
      }}
    >
      <div className="dialog-heading">
        <div>
          <span className="eyebrow">
            {mode === 'clip' ? 'A NEW MOMENT' : 'YOUR EFFECTS LIBRARY'}
          </span>
          <h2 id={heading}>{mode === 'clip' ? 'Import a video' : 'Add an effect'}</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Close upload"
          disabled={pending}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <p className="field-help">
        {mode === 'clip'
          ? 'Add a local source video to the editing queue. It follows the same selection and export checks as a captured moment.'
          : 'Add a sound, reaction or B-roll file for the editor to use. Record its usage rights and attribution.'}
      </p>
      {error && <Notice tone="danger">{error}</Notice>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <fieldset disabled={pending} className="intake-fields">
          {mode === 'asset' && (
            <Field label="Effect type">
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  if (file) setError(validateMediaFile(file, mode, e.target.value));
                }}
              >
                <option value="sfx">Sound effect</option>
                <option value="meme">Reaction</option>
                <option value="broll">B-roll</option>
              </select>
            </Field>
          )}
          <div
            className={`upload-drop${file ? ' has-file' : ''}`}
            onDragOver={(e) => {
              e.preventDefault();
            }}
            onDrop={(e) => {
              e.preventDefault();
              if (!pending) choose(e.dataTransfer.files[0]);
            }}
          >
            <Upload size={25} />
            <strong>{file ? file.name : 'Choose a file or drop it here'}</strong>
            <span>
              {file
                ? `${(file.size / 1024 / 1024).toFixed(1)} MB`
                : 'Up to 100 MB · Media files only'}
            </span>
            <Field label={mode === 'clip' ? 'Source video file' : 'Effect file'}>
              <input
                ref={input}
                type="file"
                accept={(mode === 'clip'
                  ? VIDEO
                  : kind === 'sfx'
                    ? [...AUDIO, ...VIDEO]
                    : [...IMAGE, ...VIDEO]
                )
                  .map((e) => '.' + e)
                  .join(',')}
                onChange={(e) => choose(e.target.files?.[0])}
              />
            </Field>
          </div>
          {mode === 'asset' ? (
            <>
              <Field label="Effect title">
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={200}
                  required
                />
              </Field>
              <Field
                label="Mood & tags"
                hint="Separate tags with commas, such as surprise, pause, impact."
              >
                <input value={tags} onChange={(e) => setTags(e.target.value)} maxLength={1000} />
              </Field>
              <Field
                label="Usage rights"
                hint="For example: original recording, licensed asset, or permission from the creator."
              >
                <textarea
                  value={rights}
                  onChange={(e) => setRights(e.target.value)}
                  maxLength={500}
                  rows={2}
                  required
                />
              </Field>
            </>
          ) : (
            <Field label="Source name" hint="A creator or channel name to keep with the original.">
              <input
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                maxLength={25}
                pattern="[A-Za-z0-9_]{1,25}"
                placeholder="creatorname"
              />
            </Field>
          )}
          <Field label="Attribution">
            <input
              value={attribution}
              onChange={(e) => setAttribution(e.target.value)}
              maxLength={400}
              placeholder="Source or required credit"
            />
          </Field>
        </fieldset>
        {pending && (
          <p className="field-help" role="status">
            Uploading and checking the file… Keep this page open until it finishes.
          </p>
        )}
        <div className="dialog-actions">
          <Button type="button" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            busy={pending}
            disabled={!file || pending || !!validateMediaFile(file, mode, kind)}
          >
            {mode === 'clip' ? 'Import into queue' : 'Add to library'}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
