import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Pause, Play, Radio, Sparkles, RefreshCw } from 'lucide-react';
import { ApiError, message, mutate, request } from '../../lib/api';
import { systemSchema, installationSchema, type SystemState } from '../../lib/settingsSchema';
import type { Readiness } from '../../lib/models';
import { Badge, Button, Field, Notice, Section } from '../primitives';

export function SystemPanel({
  csrf,
  readiness,
  onChange,
  notify,
}: {
  csrf: string;
  readiness: Readiness | null;
  onChange: () => Promise<void>;
  notify: (text: string) => void;
}) {
  const [system, setSystem] = useState<SystemState | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(true),
    [unsupported, setUnsupported] = useState(false),
    [pending, setPending] = useState<string | null>(null),
    [model, setModel] = useState('small');
  const mutating = useRef(false);
  const inFlight = useRef<AbortController | null>(null),
    alive = useRef(true),
    lastInstallation = useRef<string | null>(null),
    lastInstallationModel = useRef<string | null | undefined>(undefined),
    callbacks = useRef({ onChange, notify });
  callbacks.current = { onChange, notify };
  const reload = useCallback(async (force = false) => {
    if (mutating.current) return;
    if (inFlight.current && !force) return;
    if (force) {
      inFlight.current?.abort();
      inFlight.current = null;
    }
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const data = await request('/api/system', systemSchema, { signal: controller.signal });
      if (!alive.current || controller.signal.aborted) return;
      setSystem(data);
      const fetchedModel = data.installation.model;
      if (
        fetchedModel &&
        ['tiny', 'base', 'small', 'medium', 'large-v3-turbo'].includes(fetchedModel) &&
        (data.installation.status === 'running' || fetchedModel !== lastInstallationModel.current)
      )
        setModel(fetchedModel);
      // Unchanged idle snapshots must not replace the user's next model choice.
      lastInstallationModel.current = fetchedModel;
      setError('');
      setUnsupported(false);
      if (lastInstallation.current === 'running' && data.installation.status === 'succeeded') {
        callbacks.current.notify('Local transcription is ready.');
        void callbacks.current.onChange().catch((e) => {
          if (alive.current) setError(message(e));
        });
      }
      lastInstallation.current = data.installation.status;
    } catch (e) {
      if (!controller.signal.aborted) {
        if (e instanceof ApiError && e.status === 404) setUnsupported(true);
        else setError(message(e));
      }
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
      if (alive.current && !controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void reload();
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void reload();
    }, 3000);
    return () => {
      alive.current = false;
      clearInterval(timer);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [reload]);
  async function control(service: 'capture' | 'editor', enabled: boolean) {
    setPending(service);
    setError('');
    mutating.current = true;
    inFlight.current?.abort();
    inFlight.current = null;
    try {
      const result = await mutate<unknown>('/api/system/control', { service, enabled }, csrf);
      const parsed = systemSchema.safeParse(result);
      if (!parsed.success)
        throw new Error('The server returned an unsupported service state. Refresh the workspace.');
      setSystem(parsed.data);
      mutating.current = false;
      await reload(true);
      await onChange();
      notify(
        `${service === 'capture' ? 'Capture' : 'Editing'} ${enabled ? 'requested to start' : 'requested to pause'}.`,
      );
    } catch (e) {
      setError(message(e));
    } finally {
      mutating.current = false;
      if (alive.current) setPending(null);
    }
  }
  async function install() {
    setPending('install');
    setError('');
    mutating.current = true;
    inFlight.current?.abort();
    inFlight.current = null;
    try {
      const result = await mutate<unknown>('/api/system/install', { model }, csrf);
      const installation = installationSchema.parse(result);
      setSystem((s) => (s ? { ...s, installation } : s));
      lastInstallation.current = installation.status;
      mutating.current = false;
      await reload(true);
      notify('Transcription installation started. You can keep using the workspace.');
    } catch (e) {
      setError(message(e));
    } finally {
      mutating.current = false;
      if (alive.current) setPending(null);
    }
  }
  const installed = readiness?.checks.some((c) => c.id === 'transcription' && c.state === 'ready');
  const installing = pending === 'install' || system?.installation.status === 'running';
  return (
    <Section
      id="runtime"
      title="Workspace services"
      description="Start or pause capture and editing here. Publishing has its own control below."
    >
      {error && (
        <Notice
          tone="danger"
          action={
            <Button type="button" onClick={() => void reload()}>
              <RefreshCw size={15} />
              Retry
            </Button>
          }
        >
          {error}
        </Notice>
      )}
      {loading && !system ? (
        <p className="field-help" role="status">
          Checking workspace services…
        </p>
      ) : unsupported ? (
        <Notice tone="warning">
          This server version does not support in-app service controls. Update the server to manage
          capture and editing here.
        </Notice>
      ) : (
        system && (
          <div className="service-grid">
            {(['capture', 'editor'] as const).map((service) => {
              const current = system.services[service],
                working = current.state === 'running',
                transition = current.state === 'starting' || current.state === 'pausing';
              return (
                <div className="service-card" key={service}>
                  <div className="service-heading">
                    {service === 'capture' ? <Radio size={19} /> : <Sparkles size={19} />}
                    <strong>{service === 'capture' ? 'Live capture' : 'Clip editor'}</strong>
                    <Badge tone={working ? 'success' : transition ? 'active' : 'neutral'}>
                      {current.state}
                    </Badge>
                  </div>
                  <p>
                    {service === 'capture'
                      ? 'Watches enabled sources and collects candidate moments.'
                      : 'Selects, edits and checks queued moments.'}
                  </p>
                  <Button
                    type="button"
                    busy={pending === service || transition}
                    disabled={!!pending}
                    onClick={() => void control(service, !working)}
                  >
                    {working ? <Pause size={15} /> : <Play size={15} />}{' '}
                    {transition
                      ? current.state === 'starting'
                        ? 'Starting…'
                        : 'Pausing…'
                      : working
                        ? 'Pause ' + (service === 'capture' ? 'capture' : 'editing')
                        : (current.state === 'paused' ? 'Resume ' : 'Start ') +
                          (service === 'capture' ? 'capture' : 'editing')}
                  </Button>
                </div>
              );
            })}
          </div>
        )
      )}
      <div className="transcription-setup">
        <div className="subsection-heading">
          <Download size={18} />
          <h3>Local transcription</h3>
          <Badge tone={installed ? 'success' : 'warning'}>
            {installed ? 'Ready' : 'Install once'}
          </Badge>
        </div>
        <p className="field-help">
          Installs a local speech runtime and downloads your chosen model. Clips are not uploaded.
          Larger models use more memory and take longer to install.
        </p>
        <div className="install-row">
          <Field label="Transcription model">
            <select
              value={model}
              disabled={!!installing}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="tiny">Tiny</option>
              <option value="base">Base</option>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large-v3-turbo">Large v3 turbo</option>
            </select>
          </Field>
          <Button
            type="button"
            busy={!!installing}
            disabled={!!pending || unsupported || !system}
            onClick={() => void install()}
          >
            <Download size={16} />
            {installing ? 'Installing…' : 'Download & install'}
          </Button>
        </div>
        {system?.installation.status === 'running' && (
          <p className="field-help" role="status">
            {system.installation.message ||
              'Installation is running. This may take several minutes.'}
          </p>
        )}
        {system?.installation.status === 'failed' && (
          <Notice tone="danger">
            {system.installation.message ||
              'Installation did not finish. Check available disk space and retry.'}
          </Notice>
        )}
        {system?.installation.status === 'succeeded' && (
          <p className="field-help" role="status">
            {system.installation.message || 'Transcription installation completed.'}
          </p>
        )}
      </div>
    </Section>
  );
}
