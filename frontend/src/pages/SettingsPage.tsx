import { useEffect, useRef, useState } from 'react';
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  Link2,
  Pause,
  Plus,
  Radio,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { ApiError, message, mutate, safeExternalUrl } from '../lib/api';
import { setupDraft, type Readiness, type Setup, type SetupDraft } from '../lib/models';
import { Badge, Button, Field, Notice, PageHeader, Section } from '../components/primitives';
import { PerceptionFields } from '../components/PerceptionFields';
import { DeliveryGuidance } from '../components/DeliveryGuidance';
import { CaptureFields } from '../components/settings/CaptureFields';
import { EditingFields } from '../components/settings/EditingFields';
import { AgentFields } from '../components/settings/AgentFields';
import { MediaFields } from '../components/settings/MediaFields';
import { CredentialField } from '../components/settings/CredentialField';
import { DeliveryFields } from '../components/settings/DeliveryFields';
import { SystemPanel } from '../components/settings/SystemPanel';
import { Advanced } from '../components/settings/fields';

type Draft = Omit<SetupDraft, 'max_per_day' | 'min_interval_seconds'> & {
  max_per_day: string;
  min_interval_minutes: string;
};
function fromSetup(s: Setup): Draft {
  const { min_interval_seconds, ...d } = setupDraft(s);
  return {
    ...d,
    max_per_day: String(d.max_per_day),
    min_interval_minutes: String(min_interval_seconds / 60),
  };
}
const providerHelp: Record<Setup['provider'], string> = {
  codex:
    'Uses the Codex CLI installed on the server, its current sign-in and your selected model. Evidence is sent to that provider.',
  ollama: 'Uses an installed vision model on your machine. Enter its exact model name.',
  gemini:
    'Uses Gemini for story selection and final frame checks. Enter a supported model name and allow editorial evidence uploads below.',
  command:
    'Uses a trusted editor adapter. Configure its argument array under Model limits & reliability.',
  manual:
    'Prepares evidence for an external editor. Automatic selection pauses until an editorial decision is supplied.',
};
interface Device {
  code: string;
  url: string;
  status: string;
}

export function SettingsPage({
  setup,
  readiness,
  onChange,
  notify,
}: {
  setup: Setup;
  readiness: Readiness | null;
  onChange: () => Promise<void>;
  notify: (text: string) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => fromSetup(setup));
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [restartNotice, setRestartNotice] = useState('');
  const revision = useRef(0);
  const [pending, setPending] = useState<string | null>(null);
  const [expected, setExpected] = useState(setup.publishing.expected_username);
  const [automatic, setAutomatic] = useState(setup.publishing.auto_approve);
  const [publishingDirty, setPublishingDirty] = useState(false);
  const [device, setDevice] = useState<Device | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const twitchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    if (!dirty) setDraft(fromSetup(setup));
    if (!publishingDirty) {
      setExpected(setup.publishing.expected_username);
      setAutomatic(setup.publishing.auto_approve);
    }
  }, [setup, dirty, publishingDirty]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty || publishingDirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty, publishingDirty]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (twitchTimer.current) clearTimeout(twitchTimer.current);
    };
  }, []);
  function patch(change: Partial<Draft>) {
    revision.current += 1;
    setDraft((d) => ({ ...d, ...change }));
    setDirty(true);
  }
  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    patch({ [key]: value });
  }
  function markInvalidEdit() {
    revision.current += 1;
    setDirty(true);
  }
  async function discardChanges() {
    await onChange();
    setDirty(false);
    setConflict(false);
    setError('');
  }
  function channel(index: number, field: string, value: string | boolean) {
    update(
      'channels',
      draft.channels.map((c, i) => (i === index ? { ...c, [field]: value } : c)),
    );
  }
  async function save() {
    if (!form.current?.reportValidity())
      throw new Error('Check the highlighted fields before saving.');
    if (draft.editing && draft.editing.min_seconds > draft.editing.max_seconds)
      throw new Error('Minimum clip length must not exceed maximum clip length.');
    if (
      draft.capture &&
      draft.capture.retention_seconds < draft.capture.pre_seconds + draft.capture.post_seconds + 30
    )
      throw new Error('Capture buffer must cover the before and after windows plus 30 seconds.');
    if (draft.detector && draft.detector.warmup_windows > draft.detector.baseline_windows)
      throw new Error('Warm-up windows must not exceed baseline windows.');
    if (!!draft.s3_access_key_id !== !!draft.s3_secret_access_key)
      throw new Error('Enter both storage keys, or leave both blank to keep existing credentials.');
    const invalidNumber = (value: unknown): boolean =>
      typeof value === 'number'
        ? !Number.isFinite(value)
        : value !== null && typeof value === 'object'
          ? Object.values(value).some(invalidNumber)
          : false;
    if (invalidNumber(draft)) throw new Error('Complete the empty numeric fields before saving.');
    const savedRevision = revision.current;
    const { min_interval_minutes, ...fields } = draft;
    const payload: SetupDraft = {
      ...fields,
      channels: draft.channels.map((c) => ({ ...c, name: c.name.trim().toLowerCase() })),
      model: draft.model.trim(),
      instagram_app_id: draft.instagram_app_id.trim(),
      instagram_app_secret: draft.instagram_app_secret || null,
      instagram_redirect_uri: draft.instagram_redirect_uri.trim(),
      twitch_client_id: draft.twitch_client_id.trim(),
      max_per_day: Number(draft.max_per_day),
      min_interval_seconds: Math.round(Number(min_interval_minutes) * 60),
      ...(setup.perception
        ? {
            perception_model: draft.perception_model?.trim() || '',
            gemini_api_key: draft.gemini_api_key?.trim() || null,
          }
        : {}),
    };
    const result = await mutate<{ ok: boolean; restart_required?: boolean; message?: string }>(
      '/api/setup',
      payload,
      setup.csrf,
    );
    if (result.restart_required)
      setRestartNotice(
        result.message ||
          'Settings saved. Restart the server to apply the new public workspace origin.',
      );
    await onChange();
    if (revision.current !== savedRevision) return;
    setDraft((d) => ({
      ...d,
      instagram_app_secret: null,
      ...(setup.perception ? { gemini_api_key: null } : {}),
      ...(setup.config_version
        ? {
            s3_access_key_id: null,
            s3_secret_access_key: null,
            clear_gemini_key: false,
            clear_s3_credentials: false,
            clear_instagram_app_secret: false,
          }
        : {}),
    }));
    setDirty(false);
    setConflict(false);
  }
  async function run(name: string, callback: () => Promise<void>) {
    setError('');
    setPending(name);
    try {
      await callback();
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        (name === 'save' || name === 'instagram' || name === 'twitch')
      )
        setConflict(true);
      setError(message(e));
    } finally {
      if (mounted.current) setPending(null);
    }
  }
  async function connectInstagram() {
    await save();
    const result = await mutate<{ url: string }>('/api/auth/instagram/start', {}, setup.csrf);
    const url = safeExternalUrl(result.url);
    if (
      !url ||
      !['www.instagram.com', 'instagram.com', 'api.instagram.com'].includes(new URL(url).hostname)
    )
      throw new Error(
        'Instagram returned an unexpected sign-in address. Check your app configuration.',
      );
    location.assign(url);
  }
  async function pollTwitch() {
    try {
      const result = await mutate<{ status: string; username?: string; interval: number }>(
        '/api/auth/twitch/poll',
        {},
        setup.csrf,
      );
      if (!mounted.current) return;
      if (result.status === 'connected') {
        setDevice((d) => (d ? { ...d, status: `Connected as @${result.username}` } : null));
        notify('Twitch connected.');
        await onChange();
      } else
        twitchTimer.current = setTimeout(
          () => void pollTwitch(),
          Math.max(1, Math.min(120, result.interval || 5)) * 1000,
        );
    } catch (e) {
      if (mounted.current) setDevice((d) => (d ? { ...d, status: message(e) } : null));
    }
  }
  async function connectTwitch() {
    await save();
    if (twitchTimer.current) clearTimeout(twitchTimer.current);
    const data = await mutate<{ user_code: string; verification_uri: string; interval: number }>(
      '/api/auth/twitch/start',
      {},
      setup.csrf,
    );
    const url = safeExternalUrl(data.verification_uri);
    if (!url || !['www.twitch.tv', 'twitch.tv', 'id.twitch.tv'].includes(new URL(url).hostname))
      throw new Error('Twitch returned an unexpected verification address.');
    setDevice({ code: data.user_code, url, status: 'Waiting for you to finish signing in…' });
    twitchTimer.current = setTimeout(
      () => void pollTwitch(),
      Math.max(1, Math.min(120, data.interval || 5)) * 1000,
    );
  }
  async function publish(enabled: boolean) {
    await mutate(
      '/api/publishing',
      { enabled, expected_username: expected.trim().replace(/^@/, ''), auto_approve: automatic },
      setup.csrf,
    );
    await onChange();
    setPublishingDirty(false);
    notify(enabled ? 'Publishing settings saved.' : 'Publishing paused.');
  }
  const active = setup.publishing.enabled && !setup.publishing.dry_run;
  const accountReady = setup.accounts.instagram.connected && !setup.accounts.instagram.expired;
  const publishReady =
    accountReady && setup.media_host_configured && (readiness?.can_publish ?? false);
  const automaticReady =
    publishReady &&
    !!readiness?.can_capture &&
    !!readiness?.can_edit &&
    !!readiness?.perception.configured;
  const matches =
    expected.trim().replace(/^@/, '').toLowerCase() ===
      (setup.accounts.instagram.username || '').toLowerCase() && !!expected.trim();
  return (
    <>
      <PageHeader
        eyebrow="MAKE IT YOURS"
        title="One setup. A steady flow."
        description="Connect your sources and accounts, then let the pipeline do the repetitive work."
      />
      {error && (
        <Notice
          tone="danger"
          action={
            conflict ? (
              <Button onClick={() => void run('reload', discardChanges)} disabled={!!pending}>
                Discard changes & reload
              </Button>
            ) : undefined
          }
        >
          {error}
          {conflict && (
            <span>
              Your changes are still here. Reload the current settings before making a new save.
            </span>
          )}
        </Notice>
      )}
      {restartNotice && <Notice tone="warning">{restartNotice}</Notice>}
      <div className="settings-layout">
        <div>
          <nav className="settings-jumps" aria-label="Settings sections">
            {setup.config_version && <a href="#runtime">Services</a>}
            <a href="#sources">Sources</a>
            <a href="#editor">Editor</a>
            {draft.editing && <a href="#style">Style</a>}
            <a href="#instagram">Instagram</a>
            <a href="#delivery">Delivery</a>
            <a href="#schedule">Schedule</a>
          </nav>
          {setup.config_version && (
            <SystemPanel
              csrf={setup.csrf}
              readiness={readiness}
              onChange={onChange}
              notify={notify}
            />
          )}
          <form
            ref={form}
            className="settings-form"
            onInvalidCapture={(event) => {
              let element = event.target as HTMLElement;
              while (element.parentElement) {
                element = element.parentElement;
                if (element instanceof HTMLDetailsElement) element.open = true;
              }
            }}
            onSubmit={(e) => {
              e.preventDefault();
              void run('save', async () => {
                await save();
                notify('Workspace settings saved.');
              });
            }}
          >
            <fieldset className="settings-fields" disabled={!!pending}>
              <legend className="sr-only">Workspace settings</legend>
              <Section
                id="sources"
                title="Sources to watch"
                description="Choose channels and preserve the creator’s attribution."
                action={<Radio size={21} className="section-icon" />}
              >
                {!draft.channels.length && (
                  <p className="field-help">
                    No channels added yet. Start with one or two sources whose moments fit your
                    audience.
                  </p>
                )}
                {draft.channels.map((c, i) => (
                  <fieldset className="channel-editor" key={i}>
                    <legend className="sr-only">Source {i + 1}</legend>
                    <div className="channel-editor-heading">
                      <span>Source {i + 1}</span>
                      <Button
                        type="button"
                        variant="ghost"
                        aria-label={`Remove ${c.name || 'source ' + (i + 1)}`}
                        onClick={() =>
                          update(
                            'channels',
                            draft.channels.filter((_, j) => j !== i),
                          )
                        }
                      >
                        <Trash2 size={15} />
                        Remove
                      </Button>
                    </div>
                    <div className="form-grid">
                      <Field label="Twitch username">
                        <input
                          value={c.name}
                          onChange={(e) => channel(i, 'name', e.target.value)}
                          required
                          pattern="[A-Za-z0-9_]{1,25}"
                          maxLength={25}
                          placeholder="creatorname"
                          spellCheck={false}
                        />
                      </Field>
                      <Field label="Chat connection">
                        <select value={c.chat} onChange={(e) => channel(i, 'chat', e.target.value)}>
                          <option value="anonymous-irc">Public chat · experimental</option>
                          <option value="eventsub">Official EventSub</option>
                        </select>
                      </Field>
                    </div>
                    <Field label="Source credit">
                      <input
                        value={c.attribution}
                        onChange={(e) => channel(i, 'attribution', e.target.value)}
                        maxLength={400}
                        placeholder="Source: twitch.tv/creatorname · Fan edit"
                      />
                    </Field>
                    <label className="check-field">
                      <input
                        type="checkbox"
                        checked={c.enabled}
                        onChange={(e) => channel(i, 'enabled', e.target.checked)}
                      />
                      <span>Watch this channel</span>
                    </label>
                  </fieldset>
                ))}
                <Button
                  type="button"
                  onClick={() =>
                    update('channels', [
                      ...draft.channels,
                      { name: '', enabled: true, chat: 'anonymous-irc', attribution: '' },
                    ])
                  }
                >
                  <Plus size={16} />
                  Add a channel
                </Button>
                <CaptureFields
                  capture={draft.capture}
                  detector={draft.detector}
                  onCapture={(v) => update('capture', v)}
                  onDetector={(v) => update('detector', v)}
                />
              </Section>
              <Section
                id="editor"
                title="Your editing engine"
                description="Choose the provider that selects stories and checks completed edits."
                action={<Sparkles size={21} className="section-icon" />}
              >
                <div className="form-grid">
                  <Field label="Editing provider">
                    <select
                      value={draft.provider}
                      onChange={(e) => update('provider', e.target.value as Draft['provider'])}
                    >
                      <option value="codex">Installed Codex</option>
                      <option value="ollama">Local model · Ollama</option>
                      {setup.agent_controls && <option value="gemini">Gemini API · cloud</option>}
                      <option value="command">Custom editor</option>
                      <option value="manual">External / manual editor</option>
                    </select>
                  </Field>
                  <Field
                    label={
                      draft.provider === 'ollama'
                        ? 'Installed model name'
                        : draft.provider === 'gemini'
                          ? 'Gemini editorial model'
                          : 'Model (optional)'
                    }
                  >
                    <input
                      value={draft.model}
                      onChange={(e) => update('model', e.target.value)}
                      maxLength={200}
                      placeholder={
                        draft.provider === 'ollama'
                          ? 'Your vision model'
                          : draft.provider === 'gemini'
                            ? 'Supported Gemini model name'
                            : 'Use provider default'
                      }
                      required={draft.provider === 'ollama' || draft.provider === 'gemini'}
                    />
                  </Field>
                </div>
                <p className="field-help">{providerHelp[draft.provider]}</p>
                {setup.selection_mode !== undefined && (
                  <div className="editing-goal">
                    <Field label="Editing goal">
                      <select
                        value={draft.selection_mode || 'balanced'}
                        onChange={(e) =>
                          update('selection_mode', e.target.value as Draft['selection_mode'])
                        }
                      >
                        <option value="throughput">Steady volume</option>
                        <option value="balanced">More selective</option>
                      </select>
                    </Field>
                    <p className="field-help">
                      {draft.selection_mode === 'throughput'
                        ? 'Accept clear, entertaining moments, including familiar jokes and modest payoffs. Aim for a steady flow without requiring exceptional novelty.'
                        : 'Choose fewer, more distinctive moments with a stronger editorial score.'}{' '}
                      Both goals keep caption, audio and export checks.
                    </p>
                  </div>
                )}
                {draft.agent_controls && (
                  <AgentFields
                    value={draft.agent_controls}
                    flags={setup.content_flags || []}
                    provider={draft.provider}
                    onChange={(v) => update('agent_controls', v)}
                    onInvalidEdit={markInvalidEdit}
                  />
                )}
                {setup.perception && (
                  <PerceptionFields
                    config={draft}
                    selectionMode={draft.selection_mode}
                    keySaved={setup.perception.api_key_saved}
                    showCredential={!setup.config_version}
                    onChange={patch}
                  />
                )}
                {setup.config_version &&
                  (draft.provider === 'gemini' ||
                    draft.perception_provider === 'gemini' ||
                    setup.gemini_api_key_saved ||
                    setup.perception?.api_key_saved) && (
                    <Advanced
                      title="Gemini API credential"
                      description="One credential is shared by the editorial and additional media-review providers. Each has its own upload permission."
                      open={draft.provider === 'gemini' || draft.perception_provider === 'gemini'}
                    >
                      <CredentialField
                        label="Gemini API key"
                        value={draft.gemini_api_key}
                        saved={!!(setup.gemini_api_key_saved || setup.perception?.api_key_saved)}
                        source={setup.credential_sources?.gemini}
                        clear={draft.clear_gemini_key}
                        onChange={(v) => patch({ gemini_api_key: v, clear_gemini_key: false })}
                        onClear={(v) => patch({ clear_gemini_key: v, gemini_api_key: null })}
                      />
                    </Advanced>
                  )}
                <MediaFields
                  media={draft.media}
                  perception={draft.perception_controls}
                  provider={draft.perception_provider}
                  onMedia={(v) => update('media', v)}
                  onPerception={(v) => update('perception_controls', v)}
                  onInvalidEdit={markInvalidEdit}
                />
                {readiness && (
                  <div className="capability-row">
                    <span>
                      Media engine <strong>{readiness.media_engine.replaceAll('_', ' ')}</strong>
                    </span>
                    <Badge tone={readiness.can_edit ? 'success' : 'warning'}>
                      {readiness.can_edit ? 'Ready to edit' : 'Setup needed'}
                    </Badge>
                  </div>
                )}
                {readiness && (
                  <p className="field-help">
                    Configured review inputs:{' '}
                    {readiness.perception.configured
                      ? readiness.perception.modalities.join(', ')
                      : readiness.perception.provider === 'local-asr'
                        ? 'Local speech checks need transcription setup'
                        : 'No audio/video perception provider configured'}
                    . Completed reviews record which inputs were actually inspected; configuration
                    alone does not clear a clip.
                  </p>
                )}
              </Section>
              {draft.editing && (
                <EditingFields value={draft.editing} onChange={(v) => update('editing', v)} />
              )}
              <Section
                id="instagram"
                title="Connect Instagram"
                description="Use your dedicated Creator or Business account. Connecting does not enable posting."
                action={<Link2 size={21} className="section-icon" />}
              >
                <div className="account-line">
                  <div className="account-icon">
                    <FilmMark />
                  </div>
                  <div>
                    <strong>
                      {setup.accounts.instagram.connected
                        ? '@' + setup.accounts.instagram.username
                        : 'Instagram'}
                    </strong>
                    <p>
                      {setup.accounts.instagram.expired
                        ? 'Session expired. Connect again.'
                        : setup.accounts.instagram.connected
                          ? 'Account connected'
                          : 'Ready when your account is'}
                    </p>
                  </div>
                  <Badge tone={accountReady ? 'success' : 'neutral'}>
                    {accountReady ? 'Connected' : 'Not connected'}
                  </Badge>
                </div>
                <details
                  className="disclosure setup-disclosure"
                  open={!setup.auth.instagram_app_id}
                >
                  <summary>
                    Meta app configuration
                    <ChevronDown size={16} />
                  </summary>
                  <p className="field-help">
                    Register your app with{' '}
                    <a
                      className="text-link"
                      href="https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Instagram Login
                    </a>
                    . Configure your account’s app role and register the exact callback URL.
                  </p>
                  <div className="form-grid">
                    <Field label="Instagram app ID">
                      <input
                        inputMode="numeric"
                        pattern="[0-9]*"
                        value={draft.instagram_app_id}
                        onChange={(e) => update('instagram_app_id', e.target.value)}
                        maxLength={64}
                        autoComplete="off"
                      />
                    </Field>
                    <CredentialField
                      label="Instagram app secret"
                      value={draft.instagram_app_secret}
                      saved={setup.instagram_app_secret_saved}
                      source={setup.credential_sources?.instagram_app_secret}
                      clear={draft.clear_instagram_app_secret}
                      onChange={(v) =>
                        patch({
                          instagram_app_secret: v,
                          ...(setup.config_version ? { clear_instagram_app_secret: false } : {}),
                        })
                      }
                      onClear={
                        setup.config_version
                          ? (v) =>
                              patch({ clear_instagram_app_secret: v, instagram_app_secret: null })
                          : undefined
                      }
                    />
                  </div>
                  <Field label="Sign-in callback URL">
                    <input
                      type="url"
                      value={draft.instagram_redirect_uri}
                      onChange={(e) => update('instagram_redirect_uri', e.target.value)}
                      placeholder="https://your-relay.example/auth/instagram/callback"
                      maxLength={2000}
                    />
                  </Field>
                </details>
                <Button
                  type="button"
                  busy={pending === 'instagram'}
                  disabled={!!pending}
                  onClick={() => void run('instagram', connectInstagram)}
                >
                  <Link2 size={16} />
                  {accountReady ? 'Reconnect Instagram' : 'Connect Instagram'}
                </Button>
                <p className="field-help privacy-note">
                  <ShieldCheck size={16} />
                  Only account information and publishing permission are requested.
                </p>
              </Section>
              <Section
                title="Official Twitch chat"
                description="Optional when public chat works. Required for channels using EventSub."
              >
                <div className="account-status-row">
                  <Badge
                    tone={
                      setup.accounts.twitch.connected && !setup.accounts.twitch.expired
                        ? 'success'
                        : 'neutral'
                    }
                  >
                    {setup.accounts.twitch.connected
                      ? '@' + setup.accounts.twitch.username
                      : 'Not connected'}
                  </Badge>
                </div>
                <details className="disclosure setup-disclosure">
                  <summary>
                    Twitch application & sign-in
                    <ChevronDown size={16} />
                  </summary>
                  <p className="field-help">
                    Register a public client in the{' '}
                    <a
                      className="text-link"
                      href="https://dev.twitch.tv/console/apps"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Twitch developer console
                    </a>
                    . This connection reads chat; it never sends messages.
                  </p>
                  <Field label="Twitch application client ID">
                    <input
                      pattern="[A-Za-z0-9]*"
                      value={draft.twitch_client_id}
                      onChange={(e) => update('twitch_client_id', e.target.value)}
                      autoComplete="off"
                      maxLength={128}
                    />
                  </Field>
                  <Button
                    type="button"
                    busy={pending === 'twitch'}
                    disabled={!!pending}
                    onClick={() => void run('twitch', connectTwitch)}
                  >
                    Connect Twitch
                  </Button>
                  {device && (
                    <div className="device-panel">
                      <p>Enter this code on Twitch</p>
                      <strong>{device.code}</strong>
                      <a
                        className="button button-secondary"
                        href={device.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open Twitch sign-in
                        <ExternalLink size={15} />
                      </a>
                      <p role="status">{device.status}</p>
                    </div>
                  )}
                </details>
              </Section>
              {draft.delivery ? (
                <DeliveryFields
                  value={draft.delivery}
                  setup={setup}
                  secrets={draft}
                  onChange={(v) => update('delivery', v)}
                  onSecrets={patch}
                />
              ) : (
                <DeliveryGuidance setup={setup} />
              )}
              <Section
                id="schedule"
                title="Publishing pace"
                description="Set ceilings, not quotas. Weak moments are passed over even when there is room."
              >
                <div className="form-grid">
                  <Field label="Maximum posts per rolling day">
                    <input
                      type="number"
                      min="1"
                      max="100"
                      step="1"
                      required
                      value={draft.max_per_day}
                      onChange={(e) => update('max_per_day', e.target.value)}
                    />
                  </Field>
                  <Field label="Minimum minutes between posts">
                    <input
                      type="number"
                      min="0"
                      max="1440"
                      step="0.1"
                      required
                      value={draft.min_interval_minutes}
                      onChange={(e) => update('min_interval_minutes', e.target.value)}
                    />
                  </Field>
                </div>
              </Section>
              <div className="save-bar">
                <span>
                  {dirty ? 'You have unsaved changes.' : 'Workspace settings are up to date.'}
                </span>
                <Button
                  type="submit"
                  variant="primary"
                  busy={pending === 'save'}
                  disabled={!!pending}
                >
                  <Save size={16} />
                  Save settings
                </Button>
              </div>
            </fieldset>
          </form>
        </div>
        <aside className="setup-sidebar">
          <section className="panel publishing-panel">
            <span className="eyebrow">AUTOMATION</span>
            <div className="publishing-title">
              <h2>{active ? 'Your pipeline is publishing.' : 'Ready when you are.'}</h2>
              <Badge tone={active ? 'success' : 'neutral'}>{active ? 'On' : 'Off'}</Badge>
            </div>
            <p>
              {active
                ? `Approved clips can publish to @${setup.publishing.expected_username}.`
                : 'Complete the connections below, then enable publishing for the intended account.'}
            </p>
            <ul className="readiness-list">
              {readiness ? (
                readiness.checks.map((check) => (
                  <li key={check.id}>
                    <span className={check.state === 'ready' ? 'check-ready' : 'check-missing'}>
                      {check.state === 'ready' ? <Check size={15} /> : <span />}
                    </span>
                    <div>
                      <strong>{check.label}</strong>
                      <small>{check.detail}</small>
                      {check.action && (
                        <a
                          className="text-link"
                          href={
                            check.id === 'delivery'
                              ? '/setup#delivery'
                              : check.action.href.startsWith('/') &&
                                  !check.action.href.startsWith('//')
                                ? check.action.href
                                : safeExternalUrl(check.action.href)
                          }
                        >
                          {check.id === 'delivery' ? 'Set up HTTPS delivery' : check.action.label}
                        </a>
                      )}
                    </div>
                  </li>
                ))
              ) : (
                <li>
                  <span className="check-missing">
                    <span />
                  </span>
                  <div>
                    <strong>Readiness check unavailable</strong>
                    <small>Update the server to finish the automated setup check.</small>
                  </div>
                </li>
              )}
            </ul>
            <Field label="Intended Instagram username">
              <input
                autoComplete="off"
                value={expected}
                onChange={(e) => {
                  setExpected(e.target.value);
                  setPublishingDirty(true);
                }}
                placeholder="your_clip_account"
                maxLength={64}
              />
            </Field>
            <label className="automation-choice">
              <input
                type="checkbox"
                checked={automatic}
                disabled={!!pending || !automaticReady}
                onChange={(e) => {
                  setAutomatic(e.target.checked);
                  setPublishingDirty(true);
                }}
              />
              <span>
                <strong>Publish automatically</strong>
                <small>
                  Queue edits that pass all quality checks. Clips with unresolved issues stay held.
                </small>
              </span>
            </label>
            {!automaticReady && (
              <p className="field-help">
                Automatic mode becomes available when capture, editing, media review and delivery
                are configured.
              </p>
            )}
            {!automatic && (
              <p className="field-help">
                With this off, approve each finished clip before it enters the publishing queue.
              </p>
            )}
            {!active ? (
              <Button
                variant="primary"
                disabled={
                  !!pending || dirty || !publishReady || !matches || (automatic && !automaticReady)
                }
                busy={pending === 'publish'}
                onClick={() => void run('publish', () => publish(true))}
              >
                Enable public posting
              </Button>
            ) : (
              <>
                <Button
                  variant="secondary"
                  disabled={!!pending}
                  busy={pending === 'pause'}
                  onClick={() => void run('pause', () => publish(false))}
                >
                  <Pause size={16} />
                  Pause publishing
                </Button>
                {publishingDirty && (
                  <Button
                    variant="primary"
                    disabled={
                      !!pending ||
                      dirty ||
                      !publishReady ||
                      !matches ||
                      (automatic && !automaticReady)
                    }
                    busy={pending === 'publish'}
                    onClick={() => void run('publish', () => publish(true))}
                  >
                    Save publishing mode
                  </Button>
                )}
              </>
            )}
            {dirty && (
              <p className="field-help">
                Save workspace changes before enabling or changing publishing.
              </p>
            )}
            {publishReady && !matches && (
              <p className="field-help">
                Enter the connected account’s exact username to enable posting.
              </p>
            )}
            <p className="publishing-footnote">
              Enabling allows reviewed exports to be shared publicly. Pausing cannot recall a post
              already sent to Instagram.
            </p>
          </section>
          <div className="settings-assurance">
            <ShieldCheck size={20} />
            <div>
              <strong>Your accounts stay yours.</strong>
              <p>
                Account passwords are entered on the platforms. Tokens stay encrypted on your
                server, never in browser storage.
              </p>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
function FilmMark() {
  return <CheckCircle2 size={22} />;
}
